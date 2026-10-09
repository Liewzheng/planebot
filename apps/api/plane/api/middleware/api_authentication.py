# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
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


class APIKeyAuthentication(authentication.BaseAuthentication):
    """
    Authentication with an API Key.

    Two credential families share this header (``X-Api-Key``) but route to
    distinct principals:

    * ``plane_api_<hex>`` — the legacy user token. Authentication resolves to
      the owning ``User`` (``request.user.is_bot`` keeps driving the bot path
      via ``AIScopeEnforcementMixin``). Behavior matches the historical
      contract exactly.
    * ``plane_svc_<hex>`` — service principal token (M6+). Authentication
      resolves to the ``ServicePrincipal``; ``request.user`` is still the
      human owner so downstream DRF plumbing (timezone, ``is_authenticated``
      checks, FK lookups) keeps working unchanged. The SP principal is
      stashed on ``request._sp_principal`` and the base view's
      ``check_permissions`` routes the request through ``plane.core.authz``.
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
            user, token_value = self._authenticate_user_token(token)

        return user, token_value

    def _authenticate_user_token(self, token):
        """Legacy ``plane_api_`` user-token path — behavior unchanged."""
        try:
            api_token = APIToken.objects.get(
                Q(Q(expired_at__gt=timezone.now()) | Q(expired_at__isnull=True)),
                token=token,
                is_active=True,
                user__is_active=True,
            )
        except APIToken.DoesNotExist:
            raise AuthenticationFailed("Given API token is not valid")

        # save api token last used
        api_token.last_used = timezone.now()
        api_token.save(update_fields=["last_used"])
        return (api_token.user, api_token.token)

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
                token=token,
                is_active=True,
                principal_type=PrincipalType.SERVICE,
            )
            .first()
        )
        if api_token is None:
            raise AuthenticationFailed("Given API token is not valid")
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
