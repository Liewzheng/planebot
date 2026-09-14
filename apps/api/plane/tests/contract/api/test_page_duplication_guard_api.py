# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""A body that looks like a union-merged (duplicated) page must not be persisted"""

import pytest
from rest_framework import status

from plane.db.models import Page, Project, ProjectMember, ProjectPage


def paragraph(text: str) -> str:
    return f'<p class="editor-paragraph-block">{text}</p>'


CLEAN_BODY = "".join(
    paragraph(f"第 {index} 节：这是一段用于重复度守卫测试的较长正文，唯一标记 guard-{index}-{'y' * 20}")
    for index in range(1, 131)
)
DUPLICATED_BODY = CLEAN_BODY + CLEAN_BODY


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="Guard Project",
        identifier="GRD",
        workspace=workspace,
        created_by=create_user,
    )
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    return project


@pytest.fixture
def page(db, project, create_user):
    page = Page.objects.create(
        name="Guarded page",
        description_html="<p>initial</p>",
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
class TestPageDuplicationGuardAPI:
    def v1_url(self, workspace_slug, project_id, page_id):
        return f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/pages/{page_id}/"

    def description_url(self, workspace_slug, project_id, page_id):
        return f"/api/workspaces/{workspace_slug}/projects/{project_id}/pages/{page_id}/description/"

    @pytest.mark.django_db
    def test_v1_update_repairs_a_duplicated_body(self, api_key_client, workspace, project, page):
        """A duplicated body is folded back to a single copy instead of stored"""
        response = api_key_client.patch(
            self.v1_url(workspace.slug, project.id, page.id),
            {"description_html": DUPLICATED_BODY},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.description_html == CLEAN_BODY

    @pytest.mark.django_db
    def test_update_is_rejected_when_the_body_cannot_be_repaired(
        self, api_key_client, workspace, project, page, monkeypatch
    ):
        """When the duplication cannot be rebuilt losslessly, refuse the write"""
        from plane.utils.page_duplication import CannotDeduplicate

        def raise_cannot_deduplicate(_html):
            raise CannotDeduplicate("PAGE_CONTENT_DUPLICATED: forced for the test")

        monkeypatch.setattr(
            "plane.api.serializers.page.repair_duplicated_html", raise_cannot_deduplicate
        )
        response = api_key_client.patch(
            self.v1_url(workspace.slug, project.id, page.id),
            {"description_html": DUPLICATED_BODY},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "PAGE_CONTENT_DUPLICATED" in str(response.data)
        page.refresh_from_db()
        assert page.description_html == "<p>initial</p>"

    @pytest.mark.django_db
    def test_description_endpoint_rejects_a_duplicated_body(self, session_client, workspace, project, page):
        response = session_client.patch(
            self.description_url(workspace.slug, project.id, page.id),
            {"description_html": DUPLICATED_BODY},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        page.refresh_from_db()
        assert page.description_html == "<p>initial</p>"

    @pytest.mark.django_db
    def test_long_but_clean_body_is_accepted(self, api_key_client, workspace, project, page):
        response = api_key_client.patch(
            self.v1_url(workspace.slug, project.id, page.id),
            {"description_html": CLEAN_BODY},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.description_html == CLEAN_BODY
