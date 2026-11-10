# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for the v2 asset upload of the **shipped** entity types.

The app API of the test-case feature (M75) taught the v2 asset endpoint
two more entity types and made it resolve the project of a file. That
change broke two uploads that the web app has been doing all along:

* ``apps/web/core/components/core/image-picker-popover.tsx``,
  ``apps/web/core/components/project/form.tsx`` and
  ``apps/web/core/components/projects/create/root.tsx`` upload the cover
  image of a project (``entity_type=PROJECT_COVER``);
* ``apps/web/core/services/file.service.ts`` uploads the SVG of a
  mermaid diagram (``entity_type=MERMAID_DIAGRAM``).

Both entity types carry a ``project_id`` in
:meth:`WorkspaceFileAssetEndpoint.get_entity_id_field`, so passing the
project a second time — as the file of a test run needs — raised
``TypeError: got multiple values for keyword argument 'project_id'``
and the endpoint answered 500. Nothing in the suite covered those two
uploads, so the branch looked green.

The two tests below pin them: the upload answers 200 and the file row
carries the project it belongs to.
"""

from __future__ import annotations

import pytest
from rest_framework import status

from plane.db.models import FileAsset, Project, ProjectMember


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="Asset Upload Project",
        identifier="AUP",
        workspace=workspace,
        created_by=create_user,
        network=2,
    )
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    return project


def upload_url(slug):
    return f"/api/assets/v2/workspaces/{slug}/"


@pytest.mark.contract
@pytest.mark.django_db
class TestShippedUploads:
    def test_the_project_cover_upload_works(self, session_client, workspace, project):
        response = session_client.post(
            upload_url(workspace.slug),
            {
                "name": "cover.png",
                "type": "image/png",
                "size": 2048,
                "entity_type": FileAsset.EntityTypeContext.PROJECT_COVER,
                "entity_identifier": str(project.id),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        body = response.json()
        assert body["asset_id"]

        asset = FileAsset.objects.get(id=body["asset_id"])
        assert asset.project_id == project.id
        assert asset.entity_type == FileAsset.EntityTypeContext.PROJECT_COVER

    def test_the_mermaid_upload_works(self, session_client, workspace, project):
        response = session_client.post(
            upload_url(workspace.slug),
            {
                "name": "diagram.svg",
                "type": "image/svg+xml",
                "size": 1024,
                "entity_type": FileAsset.EntityTypeContext.MERMAID_DIAGRAM,
                "entity_identifier": str(project.id),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        body = response.json()
        assert body["asset_id"]

        asset = FileAsset.objects.get(id=body["asset_id"])
        assert asset.project_id == project.id
        assert asset.entity_type == FileAsset.EntityTypeContext.MERMAID_DIAGRAM

    def test_the_issue_attachment_upload_works(self, session_client, workspace, project):
        """The entity type without a project of its own."""
        from plane.db.models import Issue, State

        state = State.objects.create(
            name="Todo", project=project, workspace=workspace, group="backlog", default=True
        )
        issue = Issue.objects.create(
            name="An issue with a screenshot",
            project=project,
            workspace=workspace,
            state=state,
            created_by=project.created_by,
        )

        response = session_client.post(
            upload_url(workspace.slug),
            {
                "name": "shot.png",
                "type": "image/png",
                "size": 2048,
                "entity_type": FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
                "entity_identifier": str(issue.id),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        asset = FileAsset.objects.get(id=response.json()["asset_id"])
        assert asset.issue_id == issue.id
