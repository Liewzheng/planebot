# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: ``plane_svc_`` token resolution in ``APIKeyAuthentication``.

M8 wiring closes the reviewer-m6 vuln: a service principal token used to
authenticate as the SP's owner. These tests prove the new branch returns
the SP principal (stashed on ``request._sp_principal``) while keeping the
legacy user-token path (``plane_api_``) untouched and that bad / inactive
SP tokens fail closed.
"""

import pytest
from django.test import RequestFactory
from django.utils import timezone
from rest_framework import status
from rest_framework.request import Request
from rest_framework.test import APIClient

from plane.api.middleware.api_authentication import APIKeyAuthentication
from plane.db.models import APIToken
from plane.service_principals.constants import PrincipalType
from plane.service_principals.models import ServicePrincipal


USERS_ME_URL = "/api/v1/users/me/"


def _sp_token_for(sp, *, label="svc:bot", token_value=None):
    """Create a ``plane_svc_`` token bound to ``sp``.

    The ``principal_type=SERVICE`` row carries the credential layer bit
    that ``APIKeyAuthentication`` branches on. ``token_value`` is exposed so
    the test can reproduce the prefix-detection boundary directly.
    """
    return APIToken.objects.create(
        label=label,
        token=token_value or "plane_svc_testtoken123",
        user=sp.owner,
        principal_type=PrincipalType.SERVICE,
        service_principal=sp,
        workspace=sp.workspace,
    )


@pytest.mark.contract
@pytest.mark.django_db
class TestAPIKeyAuthenticationServicePrincipal:
    def test_service_token_resolves_to_sp_principal(
        self, workspace, create_user, api_client
    ):
        """A plane_svc_ token must not authenticate as the owner user — the
        SP principal path is the only valid outcome (closes M6 / reviewer-m6
        fail-open behavior)."""
        sp = ServicePrincipal.objects.create(
            workspace=workspace, owner=create_user, name="bot-auth"
        )
        token_value = "plane_svc_resolve123"
        _sp_token_for(sp, token_value=token_value)
        api_client.credentials(HTTP_X_API_KEY=token_value)

        response = api_client.get(USERS_ME_URL)

        # The ``/users/me/`` endpoint has no ``resource_type`` so an SP
        # request must hit the default-deny branch and produce a 403,
        # NOT a 200. A 200 here means the SP path leaked the owner user.
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_inactive_token_fails_closed(
        self, workspace, create_user, api_client
    ):
        sp = ServicePrincipal.objects.create(
            workspace=workspace, owner=create_user, name="bot-inactive"
        )
        token_value = "plane_svc_inactive123"
        _sp_token_for(sp, token_value=token_value).delete()  # soft-deletes

        api_client.credentials(HTTP_X_API_KEY=token_value)
        response = api_client.get(USERS_ME_URL)
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_inactive_service_principal_fails_closed(
        self, workspace, create_user, api_client
    ):
        sp = ServicePrincipal.objects.create(
            workspace=workspace,
            owner=create_user,
            name="bot-deact-sp",
            is_active=False,
        )
        token_value = "plane_svc_deact123"
        _sp_token_for(sp, token_value=token_value)

        api_client.credentials(HTTP_X_API_KEY=token_value)
        response = api_client.get(USERS_ME_URL)
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_unknown_prefix_does_not_branch_to_sp_path(
        self, api_client
    ):
        """Only tokens starting with ``plane_svc_`` enter the SP branch;
        every other prefix goes through the legacy user-token path which
        fails closed if the credential is not a real APIToken row."""
        api_client.credentials(HTTP_X_API_KEY="not_a_real_prefix_xyz")
        response = api_client.get(USERS_ME_URL)
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_legacy_user_token_still_authenticates_as_user(
        self, api_key_client
    ):
        """The historical ``plane_api_`` token path is untouched: legacy
        users keep working through ``APIKeyAuthentication`` without any
        SP-side branch firing."""
        response = api_key_client.get(USERS_ME_URL)
        assert response.status_code == status.HTTP_200_OK

    def test_service_token_for_user_typed_principal_is_rejected(
        self, workspace, create_user, api_client
    ):
        """A plane_svc_ token whose APIToken is principal_type=USER (not
        SERVICE) must not authenticate as the owner. Defends against
        accidental label collisions where a user token starts with the
        service prefix by accident."""
        token_value = "plane_svc_collides123"
        APIToken.objects.create(
            label="legacy-but-svc-prefix",
            token=token_value,
            user=create_user,
            principal_type=PrincipalType.USER,
            workspace=workspace,
        )
        api_client.credentials(HTTP_X_API_KEY=token_value)
        response = api_client.get(USERS_ME_URL)
        # Either rejected at the auth layer (no SP branch match) or
        # surfaced as a default-deny 403 (the token resolved to a USER
        # principal, the SP branch then denied). Either way, no leak.
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_inactive_owner_fails_closed(
        self, workspace, create_user, api_client
    ):
        sp = ServicePrincipal.objects.create(
            workspace=workspace, owner=create_user, name="bot-owner-off"
        )
        token_value = "plane_svc_owneroff123"
        _sp_token_for(sp, token_value=token_value)
        create_user.is_active = False
        create_user.save()

        api_client.credentials(HTTP_X_API_KEY=token_value)
        response = api_client.get(USERS_ME_URL)
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )


@pytest.mark.contract
@pytest.mark.django_db
class TestAPIKeyAuthenticationServicePrincipalInternal:
    """Direct coverage of the authenticate() helper to pin the principal
    stash contract that ``BaseAPIView._enforce_sp_permission`` depends on.
    """

    def _authenticate(self, token_value):
        factory = RequestFactory()
        django_request = factory.get("/", HTTP_X_API_KEY=token_value)
        drf_request = Request(django_request)
        APIKeyAuthentication().authenticate(drf_request)
        return drf_request

    def test_plane_svc_token_stashes_sp_principal(
        self, workspace, create_user
    ):
        sp = ServicePrincipal.objects.create(
            workspace=workspace, owner=create_user, name="bot-stash"
        )
        token_value = "plane_svc_stash123"
        _sp_token_for(sp, token_value=token_value)
        request = self._authenticate(token_value)

        assert request._sp_principal is not None
        assert request._sp_principal.kind == "service"
        assert request._sp_principal.service_principal == sp

    def test_legacy_user_token_does_not_stash_sp_principal(self, api_token):
        request = self._authenticate(api_token.token)

        assert getattr(request, "_sp_principal", None) is None


@pytest.mark.contract
@pytest.mark.django_db
class TestAPIKeyAuthenticationExpiredAt:
    """The SP token path must honor ``APIToken.expired_at`` the same way
    the legacy user-token path does (reviewer-m8 P2-3)."""

    def test_expired_service_token_fails_closed(
        self, workspace, create_user, api_client
    ):
        from datetime import timedelta

        sp = ServicePrincipal.objects.create(
            workspace=workspace, owner=create_user, name="exp-bot"
        )
        APIToken.objects.create(
            label="svc:exp-bot",
            token="plane_svc_expired123",
            user=create_user,
            principal_type=PrincipalType.SERVICE,
            service_principal=sp,
            workspace=workspace,
            expired_at=timezone.now() - timedelta(days=1),
        )
        api_client.credentials(HTTP_X_API_KEY="plane_svc_expired123")
        response = api_client.get("/api/v1/users/me/")
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_unexpired_service_token_still_authenticates(
        self, workspace, create_user, api_client
    ):
        from datetime import timedelta

        sp = ServicePrincipal.objects.create(
            workspace=workspace, owner=create_user, name="exp-bot-future"
        )
        APIToken.objects.create(
            label="svc:exp-bot-future",
            token="plane_svc_futureexpiry",
            user=create_user,
            principal_type=PrincipalType.SERVICE,
            service_principal=sp,
            workspace=workspace,
            expired_at=timezone.now() + timedelta(days=30),
        )
        api_client.credentials(HTTP_X_API_KEY="plane_svc_futureexpiry")
        # ``/users/me/`` has no ``resource_type`` so SP request default-
        # deny -> 403 (the token authenticated, but the view rejects).
        response = api_client.get("/api/v1/users/me/")
        assert response.status_code == status.HTTP_403_FORBIDDEN
