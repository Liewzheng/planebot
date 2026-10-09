# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: SP scope set endpoint (GET / PUT)."""

import uuid

import pytest
from rest_framework import status

from plane.db.models import Project, ProjectMember
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
def sp(db, workspace, create_user):
    return ServicePrincipal.objects.create(workspace=workspace, owner=create_user, name="scope-bot")


def sp_scopes_url(slug, pk):
    return f"/api/workspaces/{slug}/service-principals/{pk}/scopes/"


@pytest.mark.contract
@pytest.mark.django_db
class TestServicePrincipalScopes:
    def test_put_replaces_scopes(self, session_client, workspace, sp, project):
        response = session_client.put(
            sp_scopes_url(workspace.slug, sp.id),
            {
                "scopes": [
                    {"project": str(project.id), "resource_type": "work_item", "action": "read"},
                    {"project": None, "resource_type": "comment", "action": "create"},
                ]
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

    def test_second_put_replaces_instead_of_appending(
        self, session_client, workspace, sp, project
    ):
        session_client.put(
            sp_scopes_url(workspace.slug, sp.id),
            {
                "scopes": [
                    {"project": str(project.id), "resource_type": "work_item", "action": "read"}
                ]
            },
            format="json",
        )
        response = session_client.put(
            sp_scopes_url(workspace.slug, sp.id),
            {
                "scopes": [
                    {"project": None, "resource_type": "state", "action": "read"}
                ]
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["resource_type"] == "state"

    def test_workspace_wide_scope_uses_null_project(
        self, session_client, workspace, sp
    ):
        response = session_client.put(
            sp_scopes_url(workspace.slug, sp.id),
            {
                "scopes": [
                    {"project": None, "resource_type": "user", "action": "read"}
                ]
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data[0]["project"] is None

    def test_foreign_project_rejected(self, session_client, workspace, sp):
        # A project id that does not exist in this workspace
        response = session_client.put(
            sp_scopes_url(workspace.slug, sp.id),
            {
                "scopes": [
                    {
                        "project": str(uuid.uuid4()),
                        "resource_type": "work_item",
                        "action": "read",
                    }
                ]
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_invalid_action_rejected(self, session_client, workspace, sp):
        response = session_client.put(
            sp_scopes_url(workspace.slug, sp.id),
            {
                "scopes": [
                    {
                        "project": None,
                        "resource_type": "work_item",
                        "action": "nonsense",
                    }
                ]
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_returns_empty_for_new_sp(self, session_client, workspace, sp):
        response = session_client.get(sp_scopes_url(workspace.slug, sp.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data == []

    def test_get_returns_existing_scopes(self, session_client, workspace, sp):
        session_client.put(
            sp_scopes_url(workspace.slug, sp.id),
            {
                "scopes": [
                    {"project": None, "resource_type": "user", "action": "read"},
                ]
            },
            format="json",
        )
        response = session_client.get(sp_scopes_url(workspace.slug, sp.id))
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["resource_type"] == "user"