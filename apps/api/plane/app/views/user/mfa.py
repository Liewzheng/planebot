# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import io

# Third party imports
import qrcode
import qrcode.image.svg
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.views.base import BaseAPIView
from plane.authentication.adapter.error import (
    AuthenticationException,
    AUTHENTICATION_ERROR_CODES,
)
from plane.authentication.rate_limit import AuthenticationThrottle
from plane.authentication.utils.mfa import is_mfa_enabled
from plane.db.models import RecoveryCode, TOTPDevice


def mfa_error_response(error_key: str, http_status: int, message: str | None = None) -> Response:
    exc = AuthenticationException(
        error_code=AUTHENTICATION_ERROR_CODES[error_key],
        error_message=error_key,
        payload={"error": message or error_key},
    )
    return Response(exc.get_error_dict(), status=http_status)


def check_mfa_enabled() -> Response | None:
    if not is_mfa_enabled():
        return mfa_error_response("MFA_NOT_ENABLED", status.HTTP_403_FORBIDDEN)
    return None


def check_current_password(request) -> Response | None:
    password = request.data.get("password", "")
    if not password:
        return mfa_error_response("MFA_PASSWORD_REQUIRED", status.HTTP_400_BAD_REQUEST)
    if not request.user.check_password(password):
        return mfa_error_response("MFA_INCORRECT_PASSWORD", status.HTTP_400_BAD_REQUEST)
    return None


def qr_svg(data: str) -> str:
    """Render an otpauth URI as a standalone SVG (pure-python, no PIL)."""
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buffer = io.BytesIO()
    img.save(buffer)
    return buffer.getvalue().decode("utf-8")


class UserMFASetupEndpoint(BaseAPIView):
    # Rate-limit setup attempts the same way the auth endpoints are throttled
    throttle_classes = [AuthenticationThrottle]

    def post(self, request):
        disabled = check_mfa_enabled()
        if disabled:
            return disabled

        # An already-enabled device has to be disabled before setting up again
        if TOTPDevice.objects.filter(user=request.user, confirmed=True).exists():
            return mfa_error_response("MFA_ALREADY_ENABLED", status.HTTP_400_BAD_REQUEST)

        device = TOTPDevice.create_unconfirmed(user=request.user)
        otpauth_uri = device.provision_uri(request.user)
        return Response(
            {
                "secret": device.get_secret(),
                "otpauth_uri": otpauth_uri,
                "qr_svg": qr_svg(otpauth_uri),
            },
            status=status.HTTP_200_OK,
        )


class UserMFAEnableEndpoint(BaseAPIView):
    throttle_classes = [AuthenticationThrottle]

    def post(self, request):
        disabled = check_mfa_enabled()
        if disabled:
            return disabled

        device = TOTPDevice.objects.filter(user=request.user).first()
        if device is None:
            return mfa_error_response("MFA_SETUP_REQUIRED", status.HTTP_400_BAD_REQUEST)
        if device.confirmed:
            return mfa_error_response("MFA_ALREADY_ENABLED", status.HTTP_400_BAD_REQUEST)

        code = str(request.data.get("code", "")).strip()
        if not code:
            return mfa_error_response("MFA_CODE_REQUIRED", status.HTTP_400_BAD_REQUEST)
        if not device.verify(code):
            return mfa_error_response("MFA_INVALID_CODE", status.HTTP_400_BAD_REQUEST)

        device.confirmed = True
        device.save()

        # Recovery codes are replaced on every enable and returned only now,
        # in plaintext, exactly once (hard delete — see create_unconfirmed)
        RecoveryCode.all_objects.filter(user=request.user).delete()
        recovery_codes = RecoveryCode.generate_for_user(request.user)
        return Response({"recovery_codes": recovery_codes}, status=status.HTTP_200_OK)


class UserMFADisableEndpoint(BaseAPIView):
    throttle_classes = [AuthenticationThrottle]

    def post(self, request):
        disabled = check_mfa_enabled()
        if disabled:
            return disabled

        password_error = check_current_password(request)
        if password_error:
            return password_error

        device = TOTPDevice.objects.filter(user=request.user).first()
        if device is None:
            return mfa_error_response("MFA_NOT_ENABLED", status.HTTP_400_BAD_REQUEST)

        device.delete(soft=False)
        RecoveryCode.all_objects.filter(user=request.user).delete()
        return Response({"message": "MFA disabled"}, status=status.HTTP_200_OK)


class UserMFARecoveryCodesRegenerateEndpoint(BaseAPIView):
    throttle_classes = [AuthenticationThrottle]

    def post(self, request):
        disabled = check_mfa_enabled()
        if disabled:
            return disabled

        password_error = check_current_password(request)
        if password_error:
            return password_error

        device = TOTPDevice.objects.filter(user=request.user, confirmed=True).first()
        if device is None:
            return mfa_error_response("MFA_NOT_ENABLED", status.HTTP_400_BAD_REQUEST)

        RecoveryCode.all_objects.filter(user=request.user).delete()
        recovery_codes = RecoveryCode.generate_for_user(request.user)
        return Response({"recovery_codes": recovery_codes}, status=status.HTTP_200_OK)
