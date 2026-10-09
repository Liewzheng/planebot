# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: SP CRUD surface (list / create / detail / patch / delete)."""

import pytest
from rest_framework import status

from plane.db.models import APIToken, Project, ProjectMember, User, WorkspaceMember
from plane.service_principals.constants import PrincipalType
from plane.service_principals.models import ServicePrincipal


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="Test Project",
        identifier="TP",
        workspace=workspace,
        created_by=create_user,
    )
    ProjectMember.objects.create(
        project=project, member=create_user, role=20, is_active=True
    )
    return project


@pytest.fixture
def member_user(db):
    user = User.objects.create(email="member@plane.so", username="member-user")
    user.set_password("password")
    user.save()
    return user


def sp_url(slug):
    return f"/api/workspaces/{slug}/service-principals/"


@pytest.mark.contract
@pytest.mark.django_db
class TestServicePrincipalManagement:
    def test_create_returns_service_token_once(self, session_client, workspace):
        response = session_client.post(
            sp_url(workspace.slug),
            {"name": "review-bot", "description": "RENG reviewer"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        data = response.data
        assert data["token"].startswith("plane_svc_")

        sp = ServicePrincipal.objects.get(pk=data["id"])
        token = APIToken.objects.get(service_principal=sp)
        assert token.is_service is True
        assert token.principal_type == PrincipalType.SERVICE
        assert token.service_principal_id == sp.id

    def test_create_does_not_join_workspace_or_projects(
        self, session_client, workspace, project
    ):
        """SP must NOT appear in WorkspaceMember or ProjectMember."""
        create = session_client.post(
            sp_url(workspace.slug), {"name": "isolated"}, format="json"
        )
        sp = ServicePrincipal.objects.get(pk=create.data["id"])
        assert not WorkspaceMember.objects.filter(member_id=sp.id).exists()
        assert not ProjectMember.objects.filter(member_id=sp.id).exists()

    def test_create_forbidden_for_non_admin(self, api_client, workspace, member_user):
        WorkspaceMember.objects.create(
            workspace=workspace, member=member_user, role=15, is_active=True
        )
        api_client.force_authenticate(user=member_user)
        response = api_client.post(
            sp_url(workspace.slug), {"name": "x"}, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_list_and_detail(self, session_client, workspace):
        create = session_client.post(
            sp_url(workspace.slug), {"name": "bot-1"}, format="json"
        )
        sp_id = create.data["id"]

        response = session_client.get(sp_url(workspace.slug))
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        # Listing never exposes the token
        assert "token" not in response.data[0]

        response = session_client.get(f"{sp_url(workspace.slug)}{sp_id}/")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "bot-1"
        assert "token" not in response.data

    def test_patch_renames(self, session_client, workspace):
        create = session_client.post(
            sp_url(workspace.slug), {"name": "bot-2"}, format="json"
        )
        sp_id = create.data["id"]
        response = session_client.patch(
            f"{sp_url(workspace.slug)}{sp_id}/",
            {"name": "renamed", "description": "updated"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "renamed"
        assert response.data["description"] == "updated"

    def test_patch_deactivate_disables_token(self, session_client, workspace):
        create = session_client.post(
            sp_url(workspace.slug), {"name": "bot-deact"}, format="json"
        )
        sp = ServicePrincipal.objects.get(pk=create.data["id"])
        response = session_client.patch(
            f"{sp_url(workspace.slug)}{sp.id}/",
            {"is_active": False},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_active"] is False
        token = APIToken.objects.get(service_principal=sp)
        assert token.is_active is False

    def test_patch_avatar_stored(self, session_client, workspace):
        create = session_client.post(
            sp_url(workspace.slug), {"name": "bot-avatar"}, format="json"
        )
        sp_id = create.data["id"]
        response = session_client.patch(
            f"{sp_url(workspace.slug)}{sp_id}/",
            {"avatar": "https://example.com/sp.png"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["avatar"] == "https://example.com/sp.png"

    def test_delete_disables_everything(self, session_client, workspace):
        create = session_client.post(
            sp_url(workspace.slug), {"name": "bot-del"}, format="json"
        )
        sp_id = create.data["id"]
        response = session_client.delete(f"{sp_url(workspace.slug)}{sp_id}/")
        assert response.status_code == status.HTTP_204_NO_CONTENT
        # SP is soft-deleted (deleted_at set); look it up via all_objects
        sp = ServicePrincipal.all_objects.get(pk=sp_id)
        assert sp.deleted_at is not None
        token = APIToken.objects.get(service_principal=sp)
        assert token.is_active is False

    def test_delete_forbidden_for_non_admin(
        self, session_client, api_client, workspace, member_user
    ):
        create = session_client.post(
            sp_url(workspace.slug), {"name": "bot"}, format="json"
        )
        WorkspaceMember.objects.create(
            workspace=workspace, member=member_user, role=15, is_active=True
        )
        api_client.force_authenticate(user=member_user)
        response = api_client.delete(
            f"{sp_url(workspace.slug)}{create.data['id']}/"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_detail_404_for_foreign_workspace(self, session_client, workspace, create_user):
        # Create another workspace + admin
        from plane.db.models import Workspace

        other = Workspace.objects.create(
            name="Other", owner=create_user, slug="other-ws"
        )
        WorkspaceMember.objects.create(workspace=other, member=create_user, role=20)
        # Create SP in 'other' workspace via ORM (bypasses URL routing check)
        sp = ServicePrincipal.objects.create(workspace=other, owner=create_user, name="x")
        # Hit detail under original workspace slug
        response = session_client.get(f"{sp_url(workspace.slug)}{sp.id}/")
        assert response.status_code == status.HTTP_404_NOT_FOUND