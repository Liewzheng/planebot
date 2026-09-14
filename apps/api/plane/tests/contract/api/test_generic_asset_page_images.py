# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for binding a public-API asset to a page (PLANE-65).

``POST /api/v1/workspaces/<slug>/assets/`` used to hardcode
``ISSUE_ATTACHMENT`` and accept no entity binding, so an API-token client could
not register an asset for a page — which is what the pbot CLI needs to upload a
page's inline images without a browser session. These tests pin the new
``entity_type`` / ``entity_identifier`` contract and the validation around it.
"""

from unittest import mock
from uuid import uuid4

import pytest
from rest_framework import status

from plane.db.models import FileAsset, Page, Project, ProjectMember, ProjectPage, Workspace, WorkspaceMember


def assets_url(workspace_slug):
    return f"/api/v1/workspaces/{workspace_slug}/assets/"


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="Page Images Project",
        identifier="PGI",
        workspace=workspace,
        created_by=create_user,
    )
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    return project


@pytest.fixture
def project_page(db, workspace, project, create_user):
    page = Page.objects.create(
        name="Page With Images",
        description_html="<p>body</p>",
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
    return page


@pytest.fixture
def workspace_page(db, workspace, create_user):
    """A workspace-level page: it has no ``ProjectPage`` row."""
    return Page.objects.create(
        name="Workspace Page",
        description_html="<p>body</p>",
        workspace=workspace,
        owned_by=create_user,
    )


@pytest.fixture
def foreign_page(db, create_user):
    """A page living in a workspace the API-key holder is not a member of."""
    other = Workspace.objects.create(name="Other Workspace", owner=create_user, slug="other-workspace")
    WorkspaceMember.objects.create(workspace=other, member=create_user, role=20)
    return Page.objects.create(
        name="Foreign Page",
        description_html="<p>body</p>",
        workspace=other,
        owned_by=create_user,
    )


def post_asset(client, slug, payload):
    """POST the register request with S3 stubbed out (no presign round-trip)."""
    with mock.patch("plane.api.views.asset.S3Storage") as mock_storage:
        mock_storage.return_value.generate_presigned_post.return_value = {
            "url": "https://signed.example/upload",
            "fields": {},
        }
        return client.post(assets_url(slug), payload, format="json")


@pytest.mark.contract
class TestGenericAssetPageBinding:
    @pytest.mark.django_db
    def test_registers_a_page_image_and_binds_it(self, api_key_client, workspace, project, project_page):
        response = post_asset(
            api_key_client,
            workspace.slug,
            {
                "name": "diagram.png",
                "type": "image/png",
                "size": 2048,
                "project_id": str(project.id),
                "entity_type": "PAGE_DESCRIPTION",
                "entity_identifier": str(project_page.id),
            },
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        asset = FileAsset.objects.get(id=response.data["asset_id"])
        assert asset.entity_type == FileAsset.EntityTypeContext.PAGE_DESCRIPTION
        assert asset.page_id == project_page.id
        assert asset.project_id == project.id
        # the web editor resolves a page image through the project-scoped URL
        assert asset.asset_url == f"/api/assets/v2/workspaces/{workspace.slug}/projects/{project.id}/{asset.id}/"

    @pytest.mark.django_db
    def test_page_project_is_inferred_when_project_id_is_omitted(
        self, api_key_client, workspace, project, project_page
    ):
        response = post_asset(
            api_key_client,
            workspace.slug,
            {
                "name": "diagram.png",
                "type": "image/png",
                "size": 2048,
                "entity_type": "PAGE_DESCRIPTION",
                "entity_identifier": str(project_page.id),
            },
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        asset = FileAsset.objects.get(id=response.data["asset_id"])
        assert asset.project_id == project.id

    @pytest.mark.django_db
    def test_workspace_level_page_stays_projectless(self, api_key_client, workspace, workspace_page):
        response = post_asset(
            api_key_client,
            workspace.slug,
            {
                "name": "diagram.png",
                "type": "image/png",
                "size": 2048,
                "entity_type": "PAGE_DESCRIPTION",
                "entity_identifier": str(workspace_page.id),
            },
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        asset = FileAsset.objects.get(id=response.data["asset_id"])
        assert asset.project_id is None
        assert asset.page_id == workspace_page.id
        assert asset.asset_url == f"/api/assets/v2/workspaces/{workspace.slug}/{asset.id}/"

    @pytest.mark.django_db
    def test_workspace_level_page_rejects_a_project_id(self, api_key_client, workspace, project, workspace_page):
        response = post_asset(
            api_key_client,
            workspace.slug,
            {
                "name": "diagram.png",
                "type": "image/png",
                "size": 2048,
                "project_id": str(project.id),
                "entity_type": "PAGE_DESCRIPTION",
                "entity_identifier": str(workspace_page.id),
            },
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
        assert FileAsset.objects.filter(page_id=workspace_page.id).count() == 0

    @pytest.mark.django_db
    def test_mismatched_project_id_is_rejected(self, api_key_client, workspace, create_user, project_page):
        other_project = Project.objects.create(
            name="Other Project",
            identifier="OTH",
            workspace=workspace,
            created_by=create_user,
        )

        response = post_asset(
            api_key_client,
            workspace.slug,
            {
                "name": "diagram.png",
                "type": "image/png",
                "size": 2048,
                "project_id": str(other_project.id),
                "entity_type": "PAGE_DESCRIPTION",
                "entity_identifier": str(project_page.id),
            },
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
        assert FileAsset.objects.filter(page_id=project_page.id).count() == 0

    @pytest.mark.django_db
    def test_page_from_another_workspace_is_not_reachable(self, api_key_client, workspace, foreign_page):
        response = post_asset(
            api_key_client,
            workspace.slug,
            {
                "name": "diagram.png",
                "type": "image/png",
                "size": 2048,
                "entity_type": "PAGE_DESCRIPTION",
                "entity_identifier": str(foreign_page.id),
            },
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND, response.data
        assert FileAsset.objects.filter(page_id=foreign_page.id).count() == 0

    @pytest.mark.django_db
    def test_non_uuid_entity_identifier_is_rejected(self, api_key_client, workspace):
        response = post_asset(
            api_key_client,
            workspace.slug,
            {
                "name": "diagram.png",
                "type": "image/png",
                "size": 2048,
                "entity_type": "PAGE_DESCRIPTION",
                "entity_identifier": "not-a-uuid",
            },
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data

    @pytest.mark.django_db
    def test_missing_entity_identifier_is_rejected(self, api_key_client, workspace):
        response = post_asset(
            api_key_client,
            workspace.slug,
            {"name": "diagram.png", "type": "image/png", "size": 2048, "entity_type": "PAGE_DESCRIPTION"},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data

    @pytest.mark.django_db
    def test_other_entity_types_are_rejected_for_now(self, api_key_client, workspace, project_page):
        response = post_asset(
            api_key_client,
            workspace.slug,
            {
                "name": "diagram.png",
                "type": "image/png",
                "size": 2048,
                "entity_type": "ISSUE_DESCRIPTION",
                "entity_identifier": str(project_page.id),
            },
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
        assert FileAsset.objects.count() == 0

    @pytest.mark.django_db
    def test_page_images_use_the_image_mime_allow_list(self, api_key_client, workspace, project_page):
        response = post_asset(
            api_key_client,
            workspace.slug,
            {
                "name": "notes.pdf",
                "type": "application/pdf",
                "size": 2048,
                "entity_type": "PAGE_DESCRIPTION",
                "entity_identifier": str(project_page.id),
            },
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
        assert FileAsset.objects.count() == 0

    @pytest.mark.django_db
    def test_caller_without_an_entity_type_keeps_the_old_behaviour(self, api_key_client, workspace, project):
        response = post_asset(
            api_key_client,
            workspace.slug,
            {"name": "attachment.pdf", "type": "application/pdf", "size": 1024, "project_id": str(project.id)},
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        asset = FileAsset.objects.get(id=response.data["asset_id"])
        assert asset.entity_type == FileAsset.EntityTypeContext.ISSUE_ATTACHMENT
        assert asset.page_id is None

    @pytest.mark.django_db
    def test_a_registered_page_image_can_be_confirmed_and_read_back(
        self, api_key_client, workspace, project, project_page
    ):
        register = post_asset(
            api_key_client,
            workspace.slug,
            {
                "name": "diagram.png",
                "type": "image/png",
                "size": 2048,
                "project_id": str(project.id),
                "entity_type": "PAGE_DESCRIPTION",
                "entity_identifier": str(project_page.id),
            },
        )
        asset_id = register.data["asset_id"]

        confirmed = api_key_client.patch(
            f"{assets_url(workspace.slug)}{asset_id}/", {"is_uploaded": True}, format="json"
        )
        assert confirmed.status_code == status.HTTP_204_NO_CONTENT, confirmed.data

        with mock.patch("plane.api.views.asset.S3Storage") as mock_storage:
            mock_storage.return_value.generate_presigned_url.return_value = "https://signed.example/get"
            fetched = api_key_client.get(f"{assets_url(workspace.slug)}{asset_id}/")

        assert fetched.status_code == status.HTTP_200_OK, fetched.data
        assert fetched.data["asset_url"] == "https://signed.example/get"

    @pytest.mark.django_db
    def test_unknown_page_id_returns_404(self, api_key_client, workspace):
        response = post_asset(
            api_key_client,
            workspace.slug,
            {
                "name": "diagram.png",
                "type": "image/png",
                "size": 2048,
                "entity_type": "PAGE_DESCRIPTION",
                "entity_identifier": str(uuid4()),
            },
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND, response.data
