# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: v1 API endpoints route SP-authenticated requests through
``plane.core.authz.authorize``.

These tests pin the M8 wiring on real HTTP endpoints: the legacy ``User``
path is unchanged, but a ``plane_svc_`` token now triggers the four-step
authorize() chain (scope → grant → role_cap → owner intersection) so a
service principal's authority comes from its scope rows and project
grants, not from the human owner. Each scenario drives a real v1 URL.
"""

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import Project, ProjectMember, WorkspaceMember
from plane.db.models.api import APIToken
from plane.service_principals.constants import PrincipalType
from plane.service_principals.models import (
    ProjectGrant,
    ServicePrincipal,
    ServiceScope,
)


WORKSPACE_SLUG = "test-workspace"


def _issue_list_url(workspace_slug, project_id):
    return (
        f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/work-items/"
    )


def _make_sp(workspace, owner, name="bot-1", is_active=True):
    sp = ServicePrincipal.objects.create(
        workspace=workspace,
        owner=owner,
        name=name,
        is_active=is_active,
    )
    APIToken.objects.create(
        label=f"svc:{name}",
        token=f"plane_svc_token_{name}",
        user=owner,
        principal_type=PrincipalType.SERVICE,
        service_principal=sp,
        workspace=workspace,
    )
    return sp


def _scope(sp, resource_type, action, project=None):
    return ServiceScope.objects.create(
        service_principal=sp,
        project=project,
        resource_type=resource_type,
        action=action,
    )


def _grant(sp, project, role_cap=15, is_active=True):
    return ProjectGrant.objects.create(
        service_principal=sp,
        project=project,
        role_cap=role_cap,
        is_active=is_active,
    )


@pytest.fixture
def spv1_client():
    """APIClient helper that sends the ``X-Api-Key`` header."""
    return APIClient()


@pytest.mark.contract
@pytest.mark.django_db
class TestSPWorkItemAllowDenyMatrix:
    """The issue list endpoint (``work-items/`` list/create) is the canary
    surface: it's an annotated ``resource_type="work_item"`` view with a
    full read/create/delete matrix in M7's action-required-role table.
    """

    @pytest.fixture
    def project(self, db, workspace, create_user):
        proj = Project.objects.create(
            name="Test Project",
            identifier="TP",
            workspace=workspace,
            created_by=create_user,
            network=2,
        )
        ProjectMember.objects.create(
            project=proj, member=create_user, role=20, is_active=True
        )
        return proj

    def test_fully_granted_sp_can_list(
        self, workspace, create_user, project, spv1_client
    ):
        sp = _make_sp(workspace, create_user, name="allowed")
        _scope(sp, "work_item", "read", project=project)
        _grant(sp, project, role_cap=15)

        spv1_client.credentials(HTTP_X_API_KEY=f"plane_svc_token_allowed")
        response = spv1_client.get(_issue_list_url(workspace.slug, project.id))

        assert response.status_code == status.HTTP_200_OK

    def test_no_scope_denies_with_deny_scope_miss(
        self, workspace, create_user, project, spv1_client
    ):
        sp = _make_sp(workspace, create_user, name="no-scope")
        # Grant only, no scope row
        _grant(sp, project, role_cap=20)

        spv1_client.credentials(HTTP_X_API_KEY="plane_svc_token_no-scope")
        response = spv1_client.get(_issue_list_url(workspace.slug, project.id))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_no_grant_denies_with_deny_grant_miss(
        self, workspace, create_user, project, spv1_client
    ):
        sp = _make_sp(workspace, create_user, name="no-grant")
        _scope(sp, "work_item", "read", project=project)
        # No grant row

        spv1_client.credentials(HTTP_X_API_KEY="plane_svc_token_no-grant")
        response = spv1_client.get(_issue_list_url(workspace.slug, project.id))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_inactive_grant_denies(
        self, workspace, create_user, project, spv1_client
    ):
        sp = _make_sp(workspace, create_user, name="inactive-grant")
        _scope(sp, "work_item", "read", project=project)
        _grant(sp, project, role_cap=20, is_active=False)

        spv1_client.credentials(
            HTTP_X_API_KEY="plane_svc_token_inactive-grant"
        )
        response = spv1_client.get(_issue_list_url(workspace.slug, project.id))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_role_cap_below_required_denies(
        self, workspace, create_user, project, spv1_client
    ):
        """``create`` on work_item requires MEMBER (15); a GUEST-cap (5)
        grant must deny."""
        sp = _make_sp(workspace, create_user, name="low-cap")
        _scope(sp, "work_item", "create", project=project)
        _grant(sp, project, role_cap=5)

        spv1_client.credentials(HTTP_X_API_KEY="plane_svc_token_low-cap")
        # POST to issue a CREATE action
        response = spv1_client.post(
            _issue_list_url(workspace.slug, project.id),
            data={},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_role_cap_at_required_allows(
        self, workspace, create_user, project, spv1_client
    ):
        """Boundary: cap == required role allows."""
        sp = _make_sp(workspace, create_user, name="at-cap")
        _scope(sp, "work_item", "create", project=project)
        _grant(sp, project, role_cap=15)

        spv1_client.credentials(HTTP_X_API_KEY="plane_svc_token_at-cap")
        response = spv1_client.post(
            _issue_list_url(workspace.slug, project.id),
            data={},
            format="json",
        )
        # 201 because the request is allowed; a 4xx downstream (e.g. validation)
        # would mean the chain failed BEFORE the request body reached the view.
        assert response.status_code in (
            status.HTTP_201_CREATED,
            status.HTTP_400_BAD_REQUEST,  # serializer validation only
        )

    def test_owner_demoted_below_required_denies(
        self, workspace, create_user, project, spv1_client
    ):
        """When the SP's owner workspace role drops below the action
        floor, the chain denies (DENY_OWNER_ROLE)."""
        sp = _make_sp(workspace, create_user, name="demoted")
        _scope(sp, "work_item", "update", project=project)
        _grant(sp, project, role_cap=20)

        # Drop the owner from admin to guest — below UPDATE floor (MEMBER=15)
        WorkspaceMember.objects.filter(
            workspace=workspace, member=create_user
        ).update(role=5)

        spv1_client.credentials(HTTP_X_API_KEY="plane_svc_token_demoted")
        # PATCH to drive UPDATE
        from uuid import uuid4

        url = (
            f"/api/v1/workspaces/{workspace.slug}/projects/"
            f"{project.id}/work-items/{uuid4()}/"
        )
        response = spv1_client.patch(
            url, data={}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.contract
@pytest.mark.django_db
class TestSPWorkspaceLevelWriteBlocked:
    """Workspace-level resources (project, member, user, invite, estimate,
    label) reject writes per the Q4 guard. We exercise it via the
    Project endpoint which is one of those workspace-level types.
    """

    def test_create_project_denied_for_sp(
        self, workspace, create_user, spv1_client
    ):
        sp = _make_sp(workspace, create_user, name="wp-create")
        # SP owns a workspace-wide read scope + grant on... no grant,
        # workspace-level — there is no grant, the engine refuses.
        _scope(sp, "project", "create", project=None)

        spv1_client.credentials(HTTP_X_API_KEY="plane_svc_token_wp-create")
        response = spv1_client.post(
            f"/api/v1/workspaces/{workspace.slug}/projects/",
            data={},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_workspace_level_read_allowed(
        self, workspace, create_user, spv1_client
    ):
        """Workspace-wide READ scope on a workspace-level resource is the
        only allowed action (Q4). After M7's P2-2 fix a READ scope row
        satisfies list endpoints, so we exercise the collection endpoint."""
        sp = _make_sp(workspace, create_user, name="wp-read")
        _scope(sp, "project", "read", project=None)

        spv1_client.credentials(HTTP_X_API_KEY="plane_svc_token_wp-read")
        response = spv1_client.get(
            f"/api/v1/workspaces/{workspace.slug}/projects/"
        )

        assert response.status_code == status.HTTP_200_OK


@pytest.mark.contract
@pytest.mark.django_db
class TestSPMemberVisibilityInResponse:
    """Human users hit the workspace member endpoint and see real rows. For
    an SP the engine authorizes the same request through grant/scope,
    independently of the human owner's memberships.
    """

    @pytest.fixture
    def project(self, db, workspace, create_user):
        proj = Project.objects.create(
            name="Project M",
            identifier="PM",
            workspace=workspace,
            created_by=create_user,
            network=2,
        )
        ProjectMember.objects.create(
            project=proj, member=create_user, role=20, is_active=True
        )
        return proj

    def test_sp_with_member_scope_can_list_project_members(
        self, workspace, create_user, project, spv1_client
    ):
        sp = _make_sp(workspace, create_user, name="mem-scope")
        _scope(sp, "member", "read", project=project)
        _grant(sp, project, role_cap=15)

        spv1_client.credentials(HTTP_X_API_KEY="plane_svc_token_mem-scope")
        response = spv1_client.get(
            f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/members/"
        )

        assert response.status_code == status.HTTP_200_OK
