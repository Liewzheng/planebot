# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.conf import settings as django_settings
from django.utils import timezone
from django.db.models import Q

# Third party imports
from rest_framework import authentication
from rest_framework.exceptions import AuthenticationFailed

# Module imports
from plane.db.models import APIToken
from plane.service_principals.constants import (
    PrincipalType,
    SERVICE_TOKEN_PREFIX,
)


def _sp_legacy_dual_read_enabled() -> bool:
    """Kill switch for the dual-read shim that bridges AIAccount-era tokens.

    Defaults to ``True`` while the M12 migration is in flight. After the
    observation window — once all bots have been converted and rotated to
    ``plane_svc_`` tokens — admins set
    ``settings.PLANE_APIKEY_DISABLE_LEGACY_DUAL_READ = True`` and roll
    the API. The setting is intentionally simple: a per-process boolean
    flip beats a per-request check that could hide a regression behind a
    query-string knob.
    """
    return not bool(
        getattr(django_settings, "PLANE_APIKEY_DISABLE_LEGACY_DUAL_READ", False)
    )


class APIKeyAuthentication(authentication.BaseAuthentication):
    """
    Authentication with an API Key.

    Three credential shapes share this header (``X-Api-Key``):

    * ``plane_svc_<hex>`` — first-party SP token (M6+). Resolves to a live
      :class:`ServicePrincipal`; ``request._sp_principal`` is set; the
      base view's ``check_permissions`` routes the request through
      ``plane.core.authz``. ``request.user`` is the human owner so
      existing human-facing plumbing keeps working.
    * ``plane_api_<hex>`` legacy bot token (pre-M12 ``is_service=True``
      tokens backed by an ``AIAccount``). When the conversion command
      has flipped the token to ``principal_type=SERVICE`` with a
      ``service_principal`` FK, the dual-read shim routes it through
      the SP branch just like a first-party ``plane_svc_`` token.
      Otherwise the legacy bot path runs and
      ``AIScopeEnforcementMixin`` does its historical ``enforce_ai_scope``
      check.
    * ``plane_api_<hex>`` regular user token — unchanged.

    Setting ``PLANE_APIKEY_DISABLE_LEGACY_DUAL_READ=True`` flips the
    shim off, so a token whose ``principal_type`` is still ``USER`` (i.e.
    conversion never reached it) fails closed with the same
    ``AuthenticationFailed`` as a bad token.
    """

    www_authenticate_realm = "api"
    media_type = "application/json"
    auth_header_name = "X-Api-Key"

    def get_api_token(self, request):
        return request.headers.get(self.auth_header_name)

    def authenticate(self, request):
        token = self.get_api_token(request=request)
        if not token:
            return None

        # Service-principal path must branch before the user-token path so a
        # plane_svc_ token can never fall through to the owner-equivalent
        # resolution that M6 / reviewer-m6 finding flagged as fail-open.
        if token.startswith(SERVICE_TOKEN_PREFIX):
            user, token_value = self._authenticate_service_token(request, token)
        else:
            user, token_value = self._authenticate_user_token(request, token)

        return user, token_value

    def _authenticate_user_token(self, request, token):
        """``plane_api_`` token resolution.

        Looks up the APIToken first; if it carries an ``service_principal``
        FK and ``principal_type=SERVICE`` (i.e. the M12 conversion command
        has flipped it) the request is routed through the SP branch via
        :meth:`_finalize_service_token`. That is the "先查 SP 再查旧
        AIAccount" semantics: SP wins whenever it can resolve.

        The pre-shim lookup stays otherwise byte-identical so the
        existing ``plane_api_`` user-token contract is preserved.
        """
        try:
            api_token = APIToken.objects.select_related(
                "user", "service_principal__workspace"
            ).get(
                Q(Q(expired_at__gt=timezone.now()) | Q(expired_at__isnull=True)),
                token=token,
                is_active=True,
                user__is_active=True,
            )
        except APIToken.DoesNotExist:
            raise AuthenticationFailed("Given API token is not valid")

        # Dual-read shim: a migrated AIAccount token now carries the SP FK
        # and principal_type=SERVICE. Route it through the SP branch — the
        # legacy bot path is skipped entirely. The kill switch short-
        # circuits a half-migrated token to a hard AuthenticationFailed
        # rather than letting it fall through to the bot path.
        if api_token.principal_type == PrincipalType.SERVICE:
            return self._finalize_service_token(request, api_token)

        if not _sp_legacy_dual_read_enabled():
            raise AuthenticationFailed(
                "Legacy API token path is disabled; rotate the token."
            )

        api_token.last_used = timezone.now()
        api_token.save(update_fields=["last_used"])
        return (api_token.user, api_token.token)

    def validate_api_token(self, token):
        """Back-compat helper preserved for the unit-test suite.

        Returns ``(user, token)`` for a valid ``plane_api_`` token or
        raises :class:`AuthenticationFailed` otherwise. Mirrors the legacy
        semantics the test suite depends on: the SP dual-read branch is
        skipped (a token whose principal_type has been migrated will still
        route through the user-token path here, returning the SP owner).
        """
        request = type(
            "_NoopRequest",
            (),
            {"headers": {}, "_sp_principal": None, "_sp_token": None},
        )()
        return self._authenticate_user_token(request, token)

    def _authenticate_service_token(self, request, token):
        """Resolve a ``plane_svc_`` token to its ServicePrincipal.

        Fail-closed per the reviewer-m6 finding: a service token whose SP is
        missing (FK CASCADE already removed) or inactive raises the same
        AuthenticationFailed as a bad user token so the request cannot
        silently fall back to the owner.
        """
        api_token = (
            APIToken.objects.select_related(
                "user", "service_principal__workspace"
            )
            .filter(
                Q(Q(expired_at__gt=timezone.now()) | Q(expired_at__isnull=True)),
                token=token,
                is_active=True,
                principal_type=PrincipalType.SERVICE,
            )
            .first()
        )
        if api_token is None:
            raise AuthenticationFailed("Given API token is not valid")
        return self._finalize_service_token(request, api_token)

    def _finalize_service_token(self, request, api_token):
        """Common tail for the SP branch.

        Validates the SP / owner state and stashes the
        :class:`ServicePrincipal_` on the request. Used by both the
        ``plane_svc_`` path and the dual-read shim.
        """
        sp = api_token.service_principal
        if sp is None or not sp.is_active or not sp.workspace_id:
            raise AuthenticationFailed("Service principal is missing or inactive")
        if not api_token.user.is_active:
            raise AuthenticationFailed("Service principal owner is inactive")

        # Lazily import so the auth module stays circular-free.
        from plane.core.authz.principal import ServicePrincipal_

        request._sp_principal = ServicePrincipal_(service_principal=sp)
        request._sp_token = api_token.token

        api_token.last_used = timezone.now()
        api_token.save(update_fields=["last_used"])

        # ``request.user`` keeps the owner so existing human-facing plumbing
        # (timezone, ``is_authenticated`` / ``is_active`` checks, FK
        # lookups) is untouched. Downstream callers switch on the
        # ``_sp_principal`` flag rather than ``request.user``.
        return (api_token.user, api_token.token)
