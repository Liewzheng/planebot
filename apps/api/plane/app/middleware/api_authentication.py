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

    The proxy is a full-fledged authenticated user from DRF's point of view
    (``is_authenticated=True``, ``is_anonymous=False``) — every permission
    class's first guard ``if request.user.is_anonymous: return False`` no
    longer short-circuits the SP path. Once past the anonymous check the
    permission classes and ``allow_permission`` consult
    :func:`plane.core.authz.principal_from_request` (which reads
    ``request.auth`` — the APIToken — and builds a :class:`ServicePrincipal_`
    from the linked SP) and run the four-step authorize() chain.

    The proxy is **deliberately** not a User row: ``WorkspaceMember`` /
    ``ProjectMember`` filters resolve to no rows because the proxy has no DB
    id. So the legacy role-based permission classes that just call
    ``WorkspaceMember.objects.filter(member=request.user, …)`` continue to
    fail closed for SP requests. The authz-aware branch that follows the
    ``is_anonymous`` check is what makes SPs work when the view opts in.

    Closing PLANE-82 depends on this split: the owner user on the APIToken
    row is *not* the authenticated principal — calling that user would
    silently grant the SP every permission the owner holds.
    """

    def __init__(self, *, sp):
        super().__init__()
        self._is_service_principal_proxy = True
        self.service_principal_id = getattr(sp, "id", None)

    # DRF's IsAuthenticated permission requires ``is_authenticated == True``
    # AND ``is_anonymous == False``. Both must be True/False respectively so
    # the SP path is reachable inside the legacy permission classes.
    @property
    def is_authenticated(self) -> bool:  # type: ignore[override]
        return True

    @property
    def is_anonymous(self) -> bool:  # type: ignore[override]
        return False

    def __repr__(self) -> str:  # pragma: no cover — debug only
        return f"_ServicePrincipalProxy(sp_id={self.service_principal_id!r})"


class APIKeyAuthentication(authentication.BaseAuthentication):
    """
    Authentication for the internal app API via ``X-Api-Key``.

    Only service-principal tokens (``plane_svc_…`` with
    ``principal_type=SERVICE``) are accepted. The app's
    :class:`IsAuthenticatedSP` permission class then routes the request
    through ``plane.core.authz.authorize()``.

    User API tokens (``plane_api_…``) are deliberately rejected here:

    * The user-token path lives on the v1 API
      (``/api/v1/`` → ``plane.api.middleware.api_authentication``).
    * The app's permission classes (e.g. ``WorkSpaceBasePermission``)
      allow any authenticated POST; widening that surface to a leaked
      user token would silently expand the blast radius of a token leak
      without going through the authz layer.
    * Rejecting at the auth layer keeps the human-session contract
      exactly as it was on ``integration/selfhost`` before M9.
    """

    www_authenticate_realm = "api"
    media_type = "application/json"
    auth_header_name = "X-Api-Key"

    def authenticate_header(self, request):
        """Return the WWW-Authenticate challenge for 401 responses.

        DRF's default ``permission_denied`` returns 401 only when the
        rejected authenticator exposes a challenge; without this method
        a user-token request would degrade to 403. We always return a
        challenge so the boundary is consistent (a missing or rejected
        X-Api-Key is 401, not 403).
        """
        return f'{self.www_authenticate_realm} realm="api"'

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

        # User API tokens don't authenticate the internal app API. See
        # the class docstring for the rationale; the v1 API has its own
        # middleware that handles them.
        if getattr(api_token, "principal_type", 0) != PrincipalType.SERVICE:
            raise AuthenticationFailed(
                "User API tokens are not accepted on the internal app API."
            )

        sp = getattr(api_token, "service_principal", None)
        if sp is None:
            raise AuthenticationFailed(
                "Service token is missing its principal binding."
            )
        if not getattr(sp, "is_active", False):
            raise AuthenticationFailed("Service principal is inactive.")
        return _ServicePrincipalProxy(sp=sp), api_token
