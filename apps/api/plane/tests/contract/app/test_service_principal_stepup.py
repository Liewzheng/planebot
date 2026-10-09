# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: SP step-up TOTP on create / rotate / delete.

Mirrors the ai_accounts step-up contract: the acting admin must submit a
valid TOTP code when they have 2FA enabled; admins without TOTP can perform
the operations without a code.
"""

import pytest
import pyotp
from rest_framework import status

from plane.db.models import TOTPDevice


def sp_url(slug):
    return f"/api/workspaces/{slug}/service-principals/"


def rotate_url(slug, pk):
    return f"{sp_url(slug)}{pk}/rotate-token/"


@pytest.mark.contract
@pytest.mark.django_db
class TestServicePrincipalStepUp:
    @pytest.fixture
    def mfa_secret(self, create_user):
        secret = pyotp.random_base32()
        TOTPDevice.objects.create(
            user=create_user,
            secret=TOTPDevice.encrypt_secret(secret),
            confirmed=True,
        )
        return secret

    def code(self, secret):
        return pyotp.TOTP(secret).now()

    def test_create_requires_totp(self, session_client, workspace, mfa_secret):
        # Missing code -> rejected
        response = session_client.post(
            sp_url(workspace.slug), {"name": "stepup-bot"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == 5200  # MFA_CODE_REQUIRED

        # Wrong code -> rejected
        response = session_client.post(
            sp_url(workspace.slug),
            {"name": "stepup-bot", "totp_code": "000000"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == 5205  # MFA_INVALID_CODE

        # Valid code -> created
        response = session_client.post(
            sp_url(workspace.slug),
            {"name": "stepup-bot", "totp_code": self.code(mfa_secret)},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

    def test_rotate_requires_totp(self, session_client, workspace, mfa_secret):
        # Create with valid code first
        create = session_client.post(
            sp_url(workspace.slug),
            {"name": "rotate-stepup", "totp_code": self.code(mfa_secret)},
            format="json",
        )
        assert create.status_code == status.HTTP_201_CREATED
        sp_id = create.data["id"]
        url = rotate_url(workspace.slug, sp_id)

        # Missing code -> rejected
        response = session_client.post(url, {}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == 5200

        # Valid code -> rotated
        response = session_client.post(
            url, {"totp_code": self.code(mfa_secret)}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        assert "token" in response.data

    def test_delete_requires_totp(self, session_client, workspace, mfa_secret):
        create = session_client.post(
            sp_url(workspace.slug),
            {"name": "delete-stepup", "totp_code": self.code(mfa_secret)},
            format="json",
        )
        assert create.status_code == status.HTTP_201_CREATED
        sp_id = create.data["id"]
        url = f"{sp_url(workspace.slug)}{sp_id}/"

        # Missing code -> rejected
        response = session_client.delete(url)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == 5200

        # Valid code -> deleted
        response = session_client.delete(
            url, {"totp_code": self.code(mfa_secret)}, format="json"
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT

    def test_admins_without_totp_can_perform(self, session_client, workspace):
        """Without 2FA enabled on the acting admin, step-up is silent."""
        # No TOTPDevice for create_user -> POST / rotate / DELETE pass through
        create = session_client.post(
            sp_url(workspace.slug), {"name": "no-2fa-bot"}, format="json"
        )
        assert create.status_code == status.HTTP_201_CREATED
        sp_id = create.data["id"]

        rotate = session_client.post(
            rotate_url(workspace.slug, sp_id), {}, format="json"
        )
        assert rotate.status_code == status.HTTP_200_OK

        delete = session_client.delete(f"{sp_url(workspace.slug)}{sp_id}/")
        assert delete.status_code == status.HTTP_204_NO_CONTENT