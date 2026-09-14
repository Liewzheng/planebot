# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from rest_framework import status

from plane.db.models import Label, Page, Project, ProjectMember, ProjectPage


@pytest.fixture
def project(db, workspace, create_user):
    """Create a test project with the user as an admin member"""
    project = Project.objects.create(
        name="Frontmatter Project",
        identifier="FM",
        workspace=workspace,
        created_by=create_user,
    )
    ProjectMember.objects.create(
        project=project,
        member=create_user,
        role=20,
        is_active=True,
    )
    return project


def yaml_block(body: str) -> str:
    """What the markdown clients send: frontmatter as a leading yaml code block"""
    return f'<pre><code class="language-yaml">{body}\n</code></pre>'


@pytest.mark.contract
class TestPageFrontmatterAPI:
    """Frontmatter is stripped from the body and its tags become page labels"""

    def get_page_url(self, workspace_slug, project_id):
        return f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/pages/"

    @pytest.mark.django_db
    def test_create_page_strips_frontmatter_and_maps_tags(self, api_key_client, workspace, project, create_user):
        html = (
            yaml_block('title: "Incident write-up"\ntags: ["plane", "yjs"]') + "<h1>Incident write-up</h1><p>body</p>"
        )
        url = self.get_page_url(workspace.slug, project.id)

        response = api_key_client.post(url, {"name": "Incident write-up", "description_html": html}, format="json")

        assert response.status_code == status.HTTP_201_CREATED

        page = Page.objects.get(id=response.data["id"])
        # the frontmatter block is gone from the stored body
        assert page.description_html == "<h1>Incident write-up</h1><p>body</p>"
        assert "language-yaml" not in page.description_html
        assert "tags" not in (page.description_stripped or "")

        # tags became project labels attached to the page
        labels = {label.name: label for label in Label.objects.filter(project=project)}
        assert set(labels) == {"plane", "yjs"}
        assert Label.objects.get(name="plane").workspace_id == workspace.id
        assert set(response.data["label_ids"]) == {str(labels["plane"].id), str(labels["yjs"].id)}
        assert page_label_ids(page) == set(response.data["label_ids"])

    @pytest.mark.django_db
    def test_existing_label_is_reused(self, api_key_client, workspace, project):
        label = Label.objects.create(name="yjs", project=project, workspace=workspace)
        html = yaml_block("tags: [yjs]") + "<p>body</p>"
        url = self.get_page_url(workspace.slug, project.id)

        response = api_key_client.post(url, {"name": "Reuse", "description_html": html}, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert Label.objects.filter(project=project).count() == 1
        assert response.data["label_ids"] == [str(label.id)]

    @pytest.mark.django_db
    def test_page_without_frontmatter_is_unchanged(self, api_key_client, workspace, project):
        html = "<p>plain body</p>"
        url = self.get_page_url(workspace.slug, project.id)

        response = api_key_client.post(url, {"name": "Plain", "description_html": html}, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        page = Page.objects.get(id=response.data["id"])
        assert page.description_html == html
        assert Label.objects.filter(project=project).count() == 0

    @pytest.mark.django_db
    def test_leading_yaml_code_sample_stays_in_the_body(self, api_key_client, workspace, project):
        html = yaml_block("kind: Deployment\nmetadata: {}") + "<p>k8s sample</p>"
        url = self.get_page_url(workspace.slug, project.id)

        response = api_key_client.post(url, {"name": "Sample", "description_html": html}, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        page = Page.objects.get(id=response.data["id"])
        assert "kind: Deployment" in page.description_html
        assert Label.objects.filter(project=project).count() == 0

    @pytest.mark.django_db
    def test_update_strips_frontmatter_and_adds_tags(self, api_key_client, workspace, project, create_user):
        page = Page.objects.create(
            name="Doc",
            description_html="<p>old</p>",
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
        html = yaml_block("tags: [frontmatter]") + "<p>new</p>"
        url = f"{self.get_page_url(workspace.slug, project.id)}{page.id}/"

        response = api_key_client.patch(url, {"description_html": html}, format="json")

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.description_html == "<p>new</p>"
        assert str(Label.objects.get(name="frontmatter", project=project).id) in response.data["label_ids"]


def page_label_ids(page) -> set[str]:
    """Label ids attached to the page through PageLabel"""
    return {str(label_id) for label_id in page.page_labels.values_list("label_id", flat=True)}


@pytest.mark.contract
class TestPageFrontmatterMetadata:
    """Parsed frontmatter is stored on the page and returned by the API"""

    def get_page_url(self, workspace_slug, project_id):
        return f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/pages/"

    @pytest.mark.django_db
    def test_metadata_is_persisted_and_returned(self, api_key_client, workspace, project):
        html = (
            yaml_block('title: "Doc"\nstatus: "verified"\ntags: ["fm"]')
            + "<h1>Doc</h1><p>body</p>"
        )
        url = self.get_page_url(workspace.slug, project.id)

        response = api_key_client.post(url, {"name": "Doc", "description_html": html}, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        page = Page.objects.get(id=response.data["id"])
        assert page.frontmatter["title"] == "Doc"
        assert page.frontmatter["status"] == "verified"
        assert page.frontmatter["tags"] == ["fm"]
        # created is filled from the page's own creation date
        assert page.frontmatter["created"] == page.created_at.date().isoformat()
        assert response.data["frontmatter"] == page.frontmatter

    @pytest.mark.django_db
    def test_an_explicit_created_date_is_kept(self, api_key_client, workspace, project):
        html = yaml_block("created: 2020-01-02") + "<p>body</p>"
        url = self.get_page_url(workspace.slug, project.id)

        response = api_key_client.post(url, {"name": "Dated", "description_html": html}, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert Page.objects.get(id=response.data["id"]).frontmatter["created"] == "2020-01-02"

    @pytest.mark.django_db
    def test_update_without_frontmatter_keeps_the_stored_metadata(
        self, api_key_client, workspace, project, create_user
    ):
        page = Page.objects.create(
            name="Kept",
            description_html="<p>old</p>",
            frontmatter={"status": "wip", "tags": ["keep"]},
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
        url = f"{self.get_page_url(workspace.slug, project.id)}{page.id}/"

        response = api_key_client.patch(url, {"description_html": "<p>new</p>"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.frontmatter["status"] == "wip"

    @pytest.mark.django_db
    def test_update_with_frontmatter_replaces_the_metadata(
        self, api_key_client, workspace, project, create_user
    ):
        page = Page.objects.create(
            name="Replaced",
            description_html="<p>old</p>",
            frontmatter={"status": "wip"},
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
        url = f"{self.get_page_url(workspace.slug, project.id)}{page.id}/"

        response = api_key_client.patch(
            url,
            {"description_html": yaml_block("status: verified") + "<p>new</p>"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.frontmatter["status"] == "verified"
