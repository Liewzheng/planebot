# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.utils import timezone
from django.db.models import Q
from django.contrib.auth.models import AnonymousUser

# Third party imports
from rest_framework import authentication
from rest_framework.exceptions import AuthenticationFailed

# Module imports
from plane.db.models import APIToken
from plane.service_principals.constants import PrincipalType


class _ServicePrincipalProxy(AnonymousUser):
    """Marker user for an SP-authenticated request.

    Behaves as an authenticated user for DRF's ``IsAuthenticated`` check but
    is anonymous from the perspective of the existing role-based permission
    classes (``WorkspaceMember`` / ``ProjectMember`` lookups return no rows
    because the proxy has no DB id). The SP itself is carried on
    ``request.auth`` (the ``APIToken`` row) and the authz-aware layer
    rebuilds a :class:`ServicePrincipal_` via
    :func:`plane.core.authz.principal_from_request`.

    Closing PLANE-82 depends on this split: the owner user on the APIToken
    row is *not* the authenticated principal — calling that user would
    silently grant the SP every permission the owner holds.
    """

    def __init__(self, *, sp):
        super().__init__()
        self._is_service_principal_proxy = True
        self.service_principal_id = getattr(sp, "id", None)

    # DRF's IsAuthenticated permission requires ``is_authenticated == True``.
    @property
    def is_authenticated(self) -> bool:  # type: ignore[override]
        return True

    def __repr__(self) -> str:  # pragma: no cover — debug only
        return f"_ServicePrincipalProxy(sp_id={self.service_principal_id!r})"


class APIKeyAuthentication(authentication.BaseAuthentication):
    """
    Authentication with an API Key. Handles both human and service-principal
    tokens. The ``principal_type`` column on APIToken is the source of truth
    for which kind of principal authenticates the request.
    """

    www_authenticate_realm = "api"
    media_type = "application/json"
    auth_header_name = "X-Api-Key"

    def get_api_token(self, request):
        return request.headers.get(self.auth_header_name)

    def validate_api_token(self, token):
        try:
            api_token = APIToken.objects.select_related(
                "user", "service_principal"
            ).get(
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
        return api_token

    def authenticate(self, request):
        token = self.get_api_token(request=request)
        if not token:
            return None

        api_token = self.validate_api_token(token)

        if getattr(api_token, "principal_type", 0) == PrincipalType.SERVICE:
            sp = getattr(api_token, "service_principal", None)
            if sp is None:
                raise AuthenticationFailed(
                    "Service token is missing its principal binding."
                )
            if not getattr(sp, "is_active", False):
                raise AuthenticationFailed("Service principal is inactive.")
            return _ServicePrincipalProxy(sp=sp), api_token

        return api_token.user, api_token
