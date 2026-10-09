# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: SP project grant endpoint (GET / PUT).

ProjectGrant is the per-project opt-in: presence of an active row means the
SP may reach the project, with role_cap as the action-role upper bound.
Default-deny: a project with no grant means the SP cannot reach it.
"""

import uuid

import pytest
from rest_framework import status

from plane.db.models import Project, ProjectMember
from plane.service_principals.models import ProjectGrant, ServicePrincipal


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
def second_project(db, workspace, create_user):
    project = Project.objects.create(
        name="Second Project",
        identifier="SP",
        workspace=workspace,
        created_by=create_user,
    )
    ProjectMember.objects.create(
        project=project, member=create_user, role=20, is_active=True
    )
    return project


@pytest.fixture
def sp(db, workspace, create_user):
    return ServicePrincipal.objects.create(workspace=workspace, owner=create_user, name="grant-bot")


def sp_grants_url(slug, pk):
    return f"/api/workspaces/{slug}/service-principals/{pk}/grants/"


@pytest.mark.contract
@pytest.mark.django_db
class TestServicePrincipalGrants:
    def test_put_replaces_grants(self, session_client, workspace, sp, project):
        response = session_client.put(
            sp_grants_url(workspace.slug, sp.id),
            {
                "grants": [
                    {
                        "project": str(project.id),
                        "role_cap": 15,
                        "is_active": True,
                    }
                ]
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["role_cap"] == 15
        assert response.data[0]["is_active"] is True

    def test_put_replaces_rather_than_appends(
        self, session_client, workspace, sp, project, second_project
    ):
        session_client.put(
            sp_grants_url(workspace.slug, sp.id),
            {
                "grants": [
                    {"project": str(project.id), "role_cap": 15, "is_active": True}
                ]
            },
            format="json",
        )
        response = session_client.put(
            sp_grants_url(workspace.slug, sp.id),
            {
                "grants": [
                    {
                        "project": str(second_project.id),
                        "role_cap": 5,
                        "is_active": True,
                    }
                ]
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["role_cap"] == 5
        # Old project grant is gone
        assert not ProjectGrant.objects.filter(
            service_principal=sp, project=project
        ).exists()

    def test_role_cap_accepts_three_levels(
        self, session_client, workspace, sp, project
    ):
        for cap in (20, 15, 5):
            response = session_client.put(
                sp_grants_url(workspace.slug, sp.id),
                {
                    "grants": [
                        {"project": str(project.id), "role_cap": cap, "is_active": True}
                    ]
                },
                format="json",
            )
            assert response.status_code == status.HTTP_200_OK
            assert response.data[0]["role_cap"] == cap

    def test_role_cap_rejects_invalid_value(
        self, session_client, workspace, sp, project
    ):
        response = session_client.put(
            sp_grants_url(workspace.slug, sp.id),
            {
                "grants": [
                    {"project": str(project.id), "role_cap": 99, "is_active": True}
                ]
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_foreign_project_rejected(self, session_client, workspace, sp):
        response = session_client.put(
            sp_grants_url(workspace.slug, sp.id),
            {
                "grants": [
                    {"project": str(uuid.uuid4()), "role_cap": 15, "is_active": True}
                ]
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_empty_for_new_sp(self, session_client, workspace, sp):
        response = session_client.get(sp_grants_url(workspace.slug, sp.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data == []

    def test_grants_listed_in_detail(self, session_client, workspace, sp, project):
        session_client.put(
            sp_grants_url(workspace.slug, sp.id),
            {
                "grants": [
                    {"project": str(project.id), "role_cap": 20, "is_active": True}
                ]
            },
            format="json",
        )
        response = session_client.get(f"/api/workspaces/{workspace.slug}/service-principals/{sp.id}/")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["grants"]) == 1
        assert response.data["grants"][0]["role_cap"] == 20