# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
from urllib.parse import urlencode

# Django imports
from django.http import HttpResponseRedirect
from django.utils import timezone
from django.views import View

# Module imports
from plane.authentication.adapter.error import AUTHENTICATION_ERROR_CODES
from plane.authentication.rate_limit import throttle_auth_redirect
from plane.authentication.utils.host import base_host
from plane.authentication.utils.login import user_login
from plane.authentication.utils.mfa import (
    clear_mfa_pending,
    get_mfa_pending_user,
)
from plane.authentication.utils.redirection_path import get_redirection_path
from plane.db.models import RecoveryCode, TOTPDevice
from plane.utils.path_validator import get_safe_redirect_url

MFA_PATH = "/accounts/mfa/"


def mfa_error_redirect(request, error_key: str) -> HttpResponseRedirect:
    params = {
        "error_code": AUTHENTICATION_ERROR_CODES[error_key],
        "error_message": error_key,
    }
    url = f"{base_host(request=request, is_app=True).rstrip('/')}{MFA_PATH}?{urlencode(params)}"
    return HttpResponseRedirect(url)


def mfa_session_expired_redirect(request) -> HttpResponseRedirect:
    params = {
        "error_code": AUTHENTICATION_ERROR_CODES["MFA_SESSION_EXPIRED"],
        "error_message": "MFA_SESSION_EXPIRED",
    }
    url = f"{base_host(request=request, is_app=True).rstrip('/')}/?{urlencode(params)}"
    return HttpResponseRedirect(url)


def mfa_success_redirect(request, pending, user) -> HttpResponseRedirect:
    path = pending.get("next_path") or get_redirection_path(user=user)
    url = get_safe_redirect_url(
        base_url=base_host(request=request, is_app=True),
        next_path=path,
        params={},
    )
    return HttpResponseRedirect(url)


class MFATOTPVerifyEndpoint(View):
    # Rate-limit second-factor attempts before any DB access, using the same
    # AuthenticationThrottle the first-factor views use.
    @throttle_auth_redirect(is_app=True)
    def post(self, request):
        pending, user = get_mfa_pending_user(request)
        if not pending or user is None:
            return mfa_session_expired_redirect(request)

        code = request.POST.get("code", "").strip()
        if not code:
            return mfa_error_redirect(request, "MFA_CODE_REQUIRED")

        device = TOTPDevice.objects.filter(user=user, confirmed=True).first()
        # The device may have been removed or disabled between the two steps
        if device is None:
            clear_mfa_pending(request)
            return mfa_error_redirect(request, "MFA_INVALID_CODE")

        if not device.verify(code):
            return mfa_error_redirect(request, "MFA_INVALID_CODE")

        # Second factor checks out — only now authenticate the session
        clear_mfa_pending(request)
        user_login(request=request, user=user, is_app=True)
        return mfa_success_redirect(request=request, pending=pending, user=user)


class MFARecoveryCodeVerifyEndpoint(View):
    @throttle_auth_redirect(is_app=True)
    def post(self, request):
        pending, user = get_mfa_pending_user(request)
        if not pending or user is None:
            return mfa_session_expired_redirect(request)

        code = request.POST.get("code", "").strip().upper()
        if not code:
            return mfa_error_redirect(request, "MFA_RECOVERY_CODE_REQUIRED")

        recovery_code = RecoveryCode.objects.filter(
            user=user,
            code_hash=RecoveryCode.hash_code(code),
            used_at__isnull=True,
        ).first()
        if recovery_code is None:
            return mfa_error_redirect(request, "MFA_INVALID_RECOVERY_CODE")

        # Recovery codes are single-use
        recovery_code.used_at = timezone.now()
        recovery_code.save()

        clear_mfa_pending(request)
        user_login(request=request, user=user, is_app=True)
        return mfa_success_redirect(request=request, pending=pending, user=user)
