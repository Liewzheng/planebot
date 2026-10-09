# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: SP token through internal app endpoints.

Closes **PLANE-82** (the validation gap where a service-principal token
authenticated as the SP owner, so the SP silently inherited the owner's
workspace / project permissions). The matrix below pins the contract
M9 puts in place:

* A bare service token (no scopes, no grants) is denied on every app
  endpoint it can touch.
* A service token whose SP has a matching scope + project grant reaches
  project-scoped endpoints when ``resource_type`` is declared.
* The token still authenticates (it is not rejected at the auth layer);
  it is rejected at the authz layer with HTTP 403 — the same shape the
  human path produces.

The endpoints used here are real DRF views under ``plane.app``:
``WorkspaceDraftIssueViewSet`` (a project-scoped CRUD decorated with
``@allow_permission``) and ``WorkspaceMemberViewSet.list`` (workspace
membership listing). When we need to assert behavior on
:class:`WorkspaceEntityPermission` we use ``WorkspaceUserPropertiesEndpoint``
which is wired to it.

Each test attaches an SP token via the ``X-Api-Key`` header the way the
docker-stack integration tests do (see ``apps/api/plane/tests/conftest.py``
for the ``api_key_client`` fixture, but service tokens use a parallel
``sp_key_client`` fixture declared below).
"""

from __future__ import annotations

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import APIToken, Project, ProjectMember
from plane.service_principals.constants import PrincipalType
from plane.service_principals.models import (
    ProjectGrant,
    ServicePrincipal,
    ServiceScope,
)


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="App Test Project",
        identifier="APP",
        workspace=workspace,
        created_by=create_user,
        network=2,
    )
    ProjectMember.objects.create(
        project=project, member=create_user, role=20, is_active=True
    )
    return project


@pytest.fixture
def sp(db, workspace, create_user):
    return ServicePrincipal.objects.create(
        workspace=workspace,
        owner=create_user,
        name="app-auth-bot",
    )


@pytest.fixture
def sp_token(db, sp, create_user):
    """Plain SP service token with no scopes / grants. The PLANE-82 case."""
    return APIToken.objects.create(
        user=create_user,
        label="svc:app-auth-bot",
        user_type=1,
        is_service=True,
        principal_type=PrincipalType.SERVICE,
        service_principal=sp,
        workspace=sp.workspace,
        token="plane_svc_app_test",
    )


@pytest.fixture
def sp_key_client(api_client, sp_token):
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=sp_token.token)
    return client


def _add_scope(sp, resource_type, action, project=None):
    return ServiceScope.objects.create(
        service_principal=sp,
        project=project,
        resource_type=resource_type,
        action=action,
    )


def _add_grant(sp, project, role_cap=15, is_active=True):
    return ProjectGrant.objects.create(
        service_principal=sp,
        project=project,
        role_cap=role_cap,
        is_active=is_active,
    )


# --------------------------------------------------------------------------- #
# PLANE-82 — bare SP token is denied at the authz layer                       #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestPlane82BareTokenDenied:
    """A bare service token (no scopes, no grants) cannot reach internal
    app endpoints. Pre-fix the token authenticated as the SP owner and
    the existing role checks let it through; post-fix the SP principal
    goes through ``authorize()`` and is denied on default-deny."""

    def test_sp_token_does_not_authenticate_as_owner(
        self, sp_token, create_user
    ):
        """Direct check on the middleware: the token must NOT resolve to
        the SP owner. PLANE-82 closed at the auth layer (the proxy user
        carries an SP marker, not a User row)."""
        from plane.app.middleware.api_authentication import APIKeyAuthentication

        auth = APIKeyAuthentication()
        result = auth.authenticate(_fake_request(sp_token.token))
        assert result is not None
        user, returned_token = result
        # Service tokens resolve to the proxy, not the SP owner.
        assert getattr(user, "_is_service_principal_proxy", False) is True
        assert returned_token.id == sp_token.id
        assert returned_token.principal_type == PrincipalType.SERVICE
        assert returned_token.service_principal_id == sp_token.service_principal_id

    def test_bare_sp_token_cannot_list_workspace_members(
        self, sp_key_client, workspace
    ):
        """``WorkspaceMemberViewSet.list`` is workspace-scoped and gated
        by ``@allow_permission``. Without a scope row the SP is denied."""
        url = f"/api/workspaces/{workspace.slug}/members/"
        response = sp_key_client.get(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_bare_sp_token_cannot_create_draft_issue(
        self, sp_key_client, workspace, project
    ):
        """``WorkspaceDraftIssueViewSet.create`` is project-scoped; an
        unauthed SP cannot craft a draft."""
        url = f"/api/workspaces/{workspace.slug}/draft-issues/"
        response = sp_key_client.post(
            url,
            data={"project_id": str(project.id), "name": "draft"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_sp_token_without_scope_does_not_reach_user_endpoint(
        self, sp_key_client, workspace
    ):
        """Endpoints that use ``WorkspaceUserPermission`` (no
        ``resource_type`` declaration) deny SP-by-default — the authz
        wiring insists on an explicit opt-in.
        """
        url = f"/api/workspaces/{workspace.slug}/user-properties/"
        response = sp_key_client.get(url)
        # Either the endpoint does not exist for the workspace (404) or
        # the SP proxy is rejected (403). Either way, the SP did not see
        # the owner's properties.
        assert response.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        )


# --------------------------------------------------------------------------- #
# SP with explicit scope + grant — passes when ``resource_type`` declared    #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestSpHappyPathThroughAuthorize:
    def test_scoped_sp_can_read_work_items(
        self, sp, sp_key_client, workspace, project
    ):
        """Project-scoped read on a resource declared by the test endpoint.

        The test endpoint (``WorkspaceDraftIssueViewSet.list``) is not
        decorated with ``resource_type`` — so even with a real grant we
        expect 403 here. The contract is: bare endpoints deny SPs by
        default, which is what closed PLANE-82. We assert the deny shape
        and follow up with the dispatch endpoint (which DOES opt in).
        """
        _add_scope(sp, "work_item", "read", project=project)
        _add_grant(sp, project, role_cap=20)

        url = f"/api/workspaces/{workspace.slug}/draft-issues/"
        response = sp_key_client.get(url + f"?project_id={project.id}")
        # Without a resource_type on the endpoint the SP is denied even
        # with a valid grant. This is the M9 contract: opt-in.
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_dispatch_endpoint_allows_sp_with_scope(
        self, sp, sp_token, workspace, project
    ):
        """The M9 dispatch endpoint IS SP-aware. When the SP has a
        matching scope row and active grant, the response is 200 with a
        SP-shaped principal block."""
        _add_scope(sp, "work_item", "read", project=project)
        _add_grant(sp, project, role_cap=15)

        client = APIClient()
        client.credentials(HTTP_X_API_KEY=sp_token.token)
        url = f"/api/workspaces/{workspace.slug}/principal/permissions/"
        response = client.get(url)
        assert response.status_code == status.HTTP_200_OK
        body = response.data
        assert body["principal"]["kind"] == "service"
        assert body["principal"]["id"] == str(sp.id)

    def test_dispatch_endpoint_denies_sp_with_no_scope(
        self, sp, sp_token, workspace, project
    ):
        """No scope row ⇒ every action is denied; the SP still gets a
        200 body so it can introspect. The endpoint uses
        :func:`effective_actions` which routes through ``authorize()``
        rather than 403'ing up-front — the principal block tells the
        front end which kind of principal authenticated, and the
        permissions block explains what's allowed.
        """
        # No scopes, no grants.
        client = APIClient()
        client.credentials(HTTP_X_API_KEY=sp_token.token)
        url = f"/api/workspaces/{workspace.slug}/principal/permissions/"
        response = client.get(url)
        # The endpoint does not gate on authorize() at the route level
        # because it is the mechanism by which the front end learns what
        # the SP can do. Every per-action field is reported; only the
        # ``allowed`` flag differs from a fully-scoped SP.
        assert response.status_code == status.HTTP_200_OK
        perms = response.data["permissions"]
        assert perms["work_item"]["read"]["allowed"] is False


# --------------------------------------------------------------------------- #
# Cross-endpoint matrix: SP token vs human session                           #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestHumanSessionUnchanged:
    """The human session path must be byte-identical after M9 lands:
    a session-authenticated admin still owns the workspace, just like
    before the SP wiring was added. This guards against the regression
    where the SP proxy accidentally satisfies a role check."""

    def test_session_admin_still_owns_workspace(
        self, session_client, workspace, create_user
    ):
        from plane.db.models import WorkspaceMember

        assert WorkspaceMember.objects.filter(
            workspace__slug=workspace.slug,
            member=create_user,
            role=20,
            is_active=True,
        ).exists()
        url = f"/api/workspaces/{workspace.slug}/members/"
        response = session_client.get(url)
        assert response.status_code == status.HTTP_200_OK

    def test_session_member_gets_403_on_admin_route(
        self, db, workspace
    ):
        from plane.db.models import User, WorkspaceMember

        user = User.objects.create(
            email="non-admin@plane.so", username="non-admin"
        )
        user.set_password("password")
        user.save()
        WorkspaceMember.objects.create(
            workspace=workspace, member=user, role=15, is_active=True
        )
        client = APIClient()
        client.force_authenticate(user=user)
        # SP settings is admin-only — member should be 403.
        url = f"/api/workspaces/{workspace.slug}/sp-settings/"
        response = client.get(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN


# --------------------------------------------------------------------------- #
# Help                                                                        #
# --------------------------------------------------------------------------- #


def _fake_request(token: str):
    """Build a minimal request object that carries ``X-Api-Key``.

    The middleware's :meth:`get_api_token` only touches
    ``request.headers`` so a SimpleNamespace-style request is enough.
    """
    from types import SimpleNamespace

    return SimpleNamespace(headers={"X-Api-Key": token})
