# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import os
import time

# Django imports
from django.http import HttpResponseRedirect

# Module imports
from plane.authentication.utils.host import base_host
from plane.db.models import TOTPDevice, User
from plane.license.utils.instance_value import get_configuration_value

# Session key under which the pre-MFA login is parked in the anonymous session
MFA_SESSION_KEY = "mfa_pending"
# Time-to-live for the pending MFA login (10 minutes), in seconds
MFA_SESSION_TTL_SECONDS = 600


def is_mfa_enabled() -> bool:
    (IS_MFA_ENABLED,) = get_configuration_value(
        [
            {
                "key": "IS_MFA_ENABLED",
                "default": os.environ.get("IS_MFA_ENABLED", "1"),
            },
        ]
    )
    return IS_MFA_ENABLED == "1"


def user_has_mfa_device(user) -> bool:
    return TOTPDevice.objects.filter(user=user, confirmed=True).exists()


def get_mfa_redirect(request, user, next_path=None):
    """
    Gate a successful first-factor authentication behind MFA.

    When the instance has MFA enabled and the user has a confirmed TOTP
    device, park the login in the (still anonymous) session as ``mfa_pending``
    and return a redirect to the web app's MFA step. ``login()`` is never
    called here — the session is only authenticated in the MFA verify view
    after the second factor checks out. Returns None when no MFA step is
    required, so callers can proceed with ``user_login``.
    """
    if not is_mfa_enabled() or not user_has_mfa_device(user):
        return None

    request.session[MFA_SESSION_KEY] = {
        "user_id": str(user.id),
        "next_path": next_path or "",
        "expires_at": time.time() + MFA_SESSION_TTL_SECONDS,
    }
    request.session.save()
    return HttpResponseRedirect(f"{base_host(request=request, is_app=True)}/accounts/mfa/")


def get_valid_mfa_pending(request):
    """
    Return the pending MFA payload from the session if it exists and has not
    expired, otherwise None (an expired payload is cleared).
    """
    pending = request.session.get(MFA_SESSION_KEY)
    if not pending:
        return None
    if pending.get("expires_at", 0) < time.time():
        clear_mfa_pending(request)
        return None
    return pending


def clear_mfa_pending(request):
    request.session.pop(MFA_SESSION_KEY, None)
    request.session.save()


def get_mfa_pending_user(request):
    """
    Return (pending, user) for a valid non-expired pending MFA login, else
    (None, None). A pending payload pointing at a missing user is cleared.
    """
    pending = get_valid_mfa_pending(request)
    if not pending:
        return None, None
    user = User.objects.filter(pk=pending.get("user_id")).first()
    if user is None:
        clear_mfa_pending(request)
        return None, None
    return pending, user
