# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from rest_framework import status

from plane.db.models import Page, Project, ProjectMember, ProjectPage


@pytest.fixture
def project(db, workspace, create_user):
    """Create a test project with the user as an admin member"""
    project = Project.objects.create(
        name="Upload Idempotency",
        identifier="UPLD",
        workspace=workspace,
        created_by=create_user,
    )
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    return project


@pytest.mark.contract
class TestPageUploadIsIdempotent:
    """Re-uploading the same content must not move the page.

    The CLI/agent upload path is used over and over with the same source (a
    document that gets re-generated, an import that gets re-run). Every one of
    those used to write the row: it moved the revision, added a history entry
    nobody caused, and made every open editor reload. Content identity ignores
    how the markup was serialized, so only a change a reader can see is written.
    """

    @staticmethod
    def _page_url(workspace, project, page):
        return f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/pages/{page.id}/"

    def _make_page(self, workspace, project, create_user, html):
        page = Page.objects.create(
            workspace=workspace,
            owned_by=create_user,
            access=Page.PUBLIC_ACCESS,
            name="Re-uploaded page",
            description_html=html,
        )
        ProjectPage.objects.create(workspace=workspace, project=project, page=page)
        return page

    @pytest.mark.django_db
    def test_an_identical_upload_writes_nothing(self, api_key_client, workspace, project, create_user):
        html = '<p class="editor-paragraph-block">正文内容 &quot;quoted&quot;</p><p>第二段</p>'
        page = self._make_page(workspace, project, create_user, html)
        before_updated_at = page.updated_at

        response = api_key_client.patch(
            self._page_url(workspace, project, page),
            {"description_html": '<p>正文内容 "quoted"</p><p>第二段</p>'},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.description_html == html
        assert page.updated_at == before_updated_at

    @pytest.mark.django_db
    def test_a_real_change_is_written(self, api_key_client, workspace, project, create_user):
        page = self._make_page(workspace, project, create_user, "<p>第一版</p>")

        response = api_key_client.patch(
            self._page_url(workspace, project, page),
            {"description_html": "<p>第二版</p>"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.description_html == "<p>第二版</p>"

    @pytest.mark.django_db
    def test_a_styling_change_is_a_change(self, api_key_client, workspace, project, create_user):
        """Identity compares what a reader sees, so a new tag is a new revision."""
        page = self._make_page(workspace, project, create_user, "<p>一句话</p>")

        response = api_key_client.patch(
            self._page_url(workspace, project, page),
            {"description_html": "<h2>一句话</h2>"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.description_html == "<h2>一句话</h2>"
