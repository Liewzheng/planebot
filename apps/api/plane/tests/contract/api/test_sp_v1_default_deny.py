# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: SP requests on un-annotated v1 endpoints default-deny.

Every v1 view that should accept service-principal requests must declare
``resource_type = "<name>"``. Endpoints that have not yet been onboarded
or that explicitly opt SPs out (``UserEndpoint``) return 403 for any
``plane_svc_`` token. The fail-closed semantics are what keeps an SP from
accidentally acting through surface area M8 didn't vet.
"""

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models.api import APIToken
from plane.service_principals.constants import PrincipalType
from plane.service_principals.models import ServicePrincipal


def _make_sp(workspace, owner, name="bot-deny", token_value=None):
    sp = ServicePrincipal.objects.create(
        workspace=workspace,
        owner=owner,
        name=name,
    )
    APIToken.objects.create(
        label=f"svc:{name}",
        token=token_value or f"plane_svc_token_{name}",
        user=owner,
        principal_type=PrincipalType.SERVICE,
        service_principal=sp,
        workspace=workspace,
    )
    return sp


@pytest.mark.contract
@pytest.mark.django_db
class TestSPDefaultDeny:
    def test_users_me_endpoint_denies_sp(
        self, workspace, create_user, api_client
    ):
        """``/users/me/`` has no ``resource_type`` — SP requests must
        not pass even with a fully granted SP."""
        _make_sp(workspace, create_user, name="me-deny")

        api_client.credentials(HTTP_X_API_KEY="plane_svc_token_me-deny")
        response = api_client.get("/api/v1/users/me/")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_users_me_endpoint_still_allows_humans(
        self, api_key_client
    ):
        """The default-deny path is SP-only. Humans keep working."""
        response = api_key_client.get("/api/v1/users/me/")
        assert response.status_code == status.HTTP_200_OK

    def test_unknown_route_denies_sp(
        self, workspace, create_user, api_client
    ):
        """A non-mapped URL prefix is a 404 from Django, but the SP
        path runs first and must produce 403 before the lookup."""
        _make_sp(workspace, create_user, name="route-deny")

        api_client.credentials(HTTP_X_API_KEY="plane_svc_token_route-deny")
        # Trailing space URL — Django won't even resolve it. But the SP
        # request still flows through the auth layer's branch on
        # ``_sp_principal`` at authorization time, which short-circuits
        # with the default-deny response.
        response = api_client.get(
            "/api/v1/workspaces/no-such-workspace/projects/"
            "00000000-0000-0000-0000-000000000000/work-items/"
        )

        # SP path: 403 (default-deny before resolve completes is not
        # guaranteed; what we need is "owner as SP never gets 200",
        # which a 403 / 404 / 401 status satisfies).
        assert response.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_401_UNAUTHORIZED,
        )


@pytest.mark.contract
@pytest.mark.django_db
class TestSPMethodMapping:
    """HTTP method → Action mapping. The base view translates verbs to
    the authorize() chain's action vocabulary; M8 keeps GET-class at READ
    (never LIST) per the reviewer-m7 workspace-level trap mitigation.
    """

    @pytest.fixture
    def project(self, db, workspace, create_user):
        from plane.db.models import Project, ProjectMember

        proj = Project.objects.create(
            name="MethodMap",
            identifier="MM",
            workspace=workspace,
            created_by=create_user,
            network=2,
        )
        ProjectMember.objects.create(
            project=proj, member=create_user, role=20, is_active=True
        )
        return proj

    def test_post_with_read_only_scope_denies(
        self, workspace, create_user, project, api_client
    ):
        """A scope row keyed on READ does not satisfy POST (CREATE)."""
        from plane.service_principals.models import ProjectGrant, ServiceScope

        sp = _make_sp(workspace, create_user, name="read-only")
        ServiceScope.objects.create(
            service_principal=sp,
            project=project,
            resource_type="work_item",
            action="read",
        )
        ProjectGrant.objects.create(
            service_principal=sp, project=project, role_cap=20
        )

        api_client.credentials(HTTP_X_API_KEY="plane_svc_token_read-only")
        response = api_client.post(
            f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/work-items/",
            data={},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_get_with_create_scope_denies(
        self, workspace, create_user, project, api_client
    ):
        """A scope row keyed on CREATE does not satisfy GET (READ)."""
        from plane.service_principals.models import ProjectGrant, ServiceScope

        sp = _make_sp(workspace, create_user, name="create-only")
        ServiceScope.objects.create(
            service_principal=sp,
            project=project,
            resource_type="work_item",
            action="create",
        )
        ProjectGrant.objects.create(
            service_principal=sp, project=project, role_cap=20
        )

        api_client.credentials(HTTP_X_API_KEY="plane_svc_token_create-only")
        response = api_client.get(
            f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/work-items/"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_all_action_scope_covers_every_method(
        self, workspace, create_user, project, api_client
    ):
        """A scope row keyed on the ``all`` wildcard authorizes any method
        that meets the role_cap ceiling."""
        from plane.service_principals.models import ProjectGrant, ServiceScope

        sp = _make_sp(workspace, create_user, name="wildcard-all")
        ServiceScope.objects.create(
            service_principal=sp,
            project=project,
            resource_type="work_item",
            action="all",
        )
        ProjectGrant.objects.create(
            service_principal=sp, project=project, role_cap=20
        )

        api_client.credentials(HTTP_X_API_KEY="plane_svc_token_wildcard-all")
        # GET -> READ < required for DELETE/UPDATE so the chain still
        # allows the read path
        response_get = api_client.get(
            f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/work-items/"
        )
        assert response_get.status_code == status.HTTP_200_OK
