# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from rest_framework import status

from plane.db.models import Page, Project, ProjectMember, ProjectPage


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="Repetition Project",
        identifier="REP",
        workspace=workspace,
        created_by=create_user,
    )
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    return project


@pytest.fixture
def page(db, project, create_user):
    page = Page.objects.create(
        name="自动曝光算法溯源：formal_calibrate 的来龙去脉",
        description_html="<p>body</p>",
        workspace=project.workspace,
        owned_by=create_user,
    )
    ProjectPage.objects.create(
        project=project,
        workspace=project.workspace,
        page=page,
        created_by=create_user,
        updated_by=create_user,
    )
    return page


@pytest.mark.contract
class TestPageNameRepetitionAPI:
    """A self-repeating name (stale-merge artifact) is folded on write"""

    def get_page_url(self, workspace_slug, project_id, page_id):
        return f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/pages/{page_id}/"

    @pytest.mark.django_db
    def test_update_does_not_persist_a_doubled_name(self, api_key_client, workspace, project, page):
        doubled = page.name + page.name
        url = self.get_page_url(workspace.slug, project.id, page.id)

        response = api_key_client.patch(url, {"name": doubled}, format="json")

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.name == "自动曝光算法溯源：formal_calibrate 的来龙去脉"

    @pytest.mark.django_db
    def test_create_does_not_persist_a_doubled_name(self, api_key_client, workspace, project):
        doubled = "Incident write-upIncident write-up"
        url = f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/pages/"

        response = api_key_client.post(url, {"name": doubled, "description_html": "<p>body</p>"}, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert Page.objects.get(id=response.data["id"]).name == "Incident write-up"

    @pytest.mark.django_db
    def test_ordinary_name_is_kept(self, api_key_client, workspace, project, page):
        url = self.get_page_url(workspace.slug, project.id, page.id)

        response = api_key_client.patch(url, {"name": "新的标题（2026-09-14）"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.name == "新的标题（2026-09-14）"
