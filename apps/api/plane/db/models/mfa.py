# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import base64
import hashlib
import secrets

# Django imports
from django.conf import settings
from django.db import models

# Third party imports
import pyotp
from cryptography.fernet import Fernet

# Module imports
from plane.db.models.base import BaseModel

RECOVERY_CODE_LENGTH = 10
RECOVERY_CODE_COUNT = 10


def get_mfa_fernet() -> Fernet:
    # Derive a stable encryption key from the Django secret key
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest())
    return Fernet(key)


class TOTPDevice(BaseModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="totp_device",
    )
    # Fernet-encrypted TOTP secret (ciphertext, never the plaintext)
    secret = models.TextField()
    confirmed = models.BooleanField(default=False)

    class Meta:
        verbose_name = "TOTP Device"
        verbose_name_plural = "TOTP Devices"
        db_table = "totp_devices"
        ordering = ("-created_at",)

    @classmethod
    def encrypt_secret(cls, plaintext: str) -> str:
        return get_mfa_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")

    @classmethod
    def decrypt_secret(cls, ciphertext: str) -> str:
        return get_mfa_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")

    @classmethod
    def create_unconfirmed(cls, user) -> "TOTPDevice":
        # Replace any previous setup attempt — hard delete via all_objects:
        # the default manager only soft-deletes, and the soft-deleted row
        # would still trip the OneToOne unique constraint on user_id
        cls.all_objects.filter(user=user).delete()
        return cls.objects.create(user=user, secret=cls.encrypt_secret(pyotp.random_base32()))

    def get_secret(self) -> str:
        return self.decrypt_secret(self.secret)

    def provision_uri(self, user) -> str:
        return pyotp.TOTP(self.get_secret()).provisioning_uri(name=user.email, issuer_name="Plane")

    def verify(self, code: str) -> bool:
        # Tolerate one time-step drift in either direction
        return pyotp.TOTP(self.get_secret()).verify(str(code).strip(), valid_window=1)


class RecoveryCode(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recovery_codes",
    )
    # sha256 hex digest of the code; the plaintext is shown only once at generation time
    code_hash = models.CharField(max_length=64)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Recovery Code"
        verbose_name_plural = "Recovery Codes"
        db_table = "recovery_codes"
        ordering = ("-created_at",)

    @staticmethod
    def hash_code(code: str) -> str:
        return hashlib.sha256(code.encode("utf-8")).hexdigest()

    @classmethod
    def generate_for_user(cls, user, count: int = RECOVERY_CODE_COUNT) -> list[str]:
        codes = []
        for _ in range(count):
            code = secrets.token_hex(RECOVERY_CODE_LENGTH // 2).upper()
            cls.objects.create(user=user, code_hash=cls.hash_code(code))
            codes.append(code)
        return codes
