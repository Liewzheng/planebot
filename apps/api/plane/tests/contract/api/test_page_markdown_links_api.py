# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Half-converted markdown links and images are folded on write"""

import pytest
from rest_framework import status

from plane.db.models import Page, Project, ProjectMember, ProjectPage

BROKEN_LINK = (
    '[官方文档](<a target="_blank" class="text-accent-secondary underline" '
    'href="https://example.com/doc" rel="noopener noreferrer">https://example.com/doc</a>)'
)

BROKEN_IMAGE = (
    '![示意图](<a target="_blank" class="text-accent-secondary underline" '
    'href="https://example.com/diagram.png" rel="noopener noreferrer">https://example.com/diagram.png</a>)'
)


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="Markdown Links Project",
        identifier="MDL",
        workspace=workspace,
        created_by=create_user,
    )
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    return project


def page_url(workspace_slug, project_id):
    return f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/pages/"


@pytest.mark.contract
class TestMarkdownLinkNormalizationAPI:
    @pytest.mark.django_db
    def test_create_folds_a_half_converted_link(self, api_key_client, workspace, project):
        html = f"<p>参考 {BROKEN_LINK}</p>"

        response = api_key_client.post(
            page_url(workspace.slug, project.id), {"name": "Links", "description_html": html}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        stored = Page.objects.get(id=response.data["id"]).description_html
        assert "](" not in stored
        assert 'href="https://example.com/doc"' in stored
        assert ">官方文档</a>" in stored

    @pytest.mark.django_db
    def test_create_folds_a_half_converted_image(self, api_key_client, workspace, project):
        html = f"<p>参考 {BROKEN_IMAGE}</p>"

        response = api_key_client.post(
            page_url(workspace.slug, project.id), {"name": "Images", "description_html": html}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        stored = Page.objects.get(id=response.data["id"]).description_html
        assert "](" not in stored
        assert '<img src="https://example.com/diagram.png" alt="示意图">' in stored

    @pytest.mark.django_db
    def test_update_folds_a_half_converted_link(self, api_key_client, workspace, project, create_user):
        page = Page.objects.create(
            name="Links",
            description_html="<p>before</p>",
            workspace=workspace,
            owned_by=create_user,
        )
        ProjectPage.objects.create(
            project=project,
            workspace=workspace,
            page=page,
            created_by=create_user,
            updated_by=create_user,
        )

        response = api_key_client.patch(
            f"{page_url(workspace.slug, project.id)}{page.id}/",
            {"description_html": f"<p>{BROKEN_LINK}</p>"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert "](" not in page.description_html
        assert ">官方文档</a>" in page.description_html

    @pytest.mark.django_db
    def test_ordinary_content_is_untouched(self, api_key_client, workspace, project):
        html = '<p>普通内容 <a href="https://example.com">链接</a></p>'

        response = api_key_client.post(
            page_url(workspace.slug, project.id), {"name": "Plain", "description_html": html}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert Page.objects.get(id=response.data["id"]).description_html == html
