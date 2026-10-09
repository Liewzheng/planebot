# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Overwriting page content must drop the live server's in-memory document

A write that does not come from the live server itself (the web editor's
offline fallback, an API client) leaves live's in-memory copy stale; live would
keep serving it and connected clients would union-merge the old content back in.
The live server marks its own writes with a shared secret header.
"""

import base64

import pytest
from rest_framework import status

from plane.db.models import Page, Project, ProjectMember, ProjectPage

LIVE_KEY = "test-live-internal-key"


@pytest.fixture
def live_internal_key(settings):
    settings.LIVE_INTERNAL_API_KEY = LIVE_KEY
    return LIVE_KEY


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="Overwrite Project",
        identifier="OVW",
        workspace=workspace,
        created_by=create_user,
    )
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    return project


@pytest.fixture
def page(db, project, create_user):
    page = Page.objects.create(
        name="Overwrite page",
        description_html="<p>stored</p>",
        workspace=project.workspace,
        owned_by=create_user,
        updated_by=create_user,
    )
    ProjectPage.objects.create(
        project=project,
        workspace=project.workspace,
        page=page,
        created_by=create_user,
        updated_by=create_user,
    )
    return page


def description_url(workspace_slug, project_id, page_id):
    return f"/api/workspaces/{workspace_slug}/projects/{project_id}/pages/{page_id}/description/"


@pytest.mark.contract
class TestPageDescriptionOverwrite:
    @pytest.mark.django_db
    def test_external_write_invalidates_the_live_document(
        self, session_client, workspace, project, page, live_internal_key, monkeypatch
    ):
        invalidated = []
        monkeypatch.setattr(
            "plane.app.views.page.base.invalidate_live_document",
            lambda page_id, content_text_length=None: invalidated.append(page_id),
        )

        response = session_client.patch(
            description_url(workspace.slug, project.id, page.id),
            {
                "description_html": "<p>overwritten</p>",
                "description_binary": base64.b64encode(b"yjs-binary").decode(),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.description_html == "<p>overwritten</p>"
        assert invalidated == [str(page.id)]

    @pytest.mark.django_db
    def test_every_content_write_drops_the_collaborative_document(
        self, session_client, workspace, project, page, live_internal_key, monkeypatch
    ):
        """The live server no longer stores, so it is never the writer.

        Editing happens in the client's own document, and every write therefore
        replaces content the live server still holds — including a write that
        carries the live internal key. Dropping the document is what makes the
        next load read the stored revision.
        """
        invalidated = []
        monkeypatch.setattr(
            "plane.app.views.page.base.invalidate_live_document",
            lambda page_id, content_text_length=None: invalidated.append(page_id),
        )

        response = session_client.patch(
            description_url(workspace.slug, project.id, page.id),
            {
                "description_html": "<p>published elsewhere</p>",
                "description_binary": base64.b64encode(b"yjs-binary").decode(),
            },
            format="json",
            HTTP_X_LIVE_INTERNAL_KEY=live_internal_key,
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.description_html == "<p>published elsewhere</p>"
        assert invalidated == [str(page.id)]

    @pytest.mark.django_db
    def test_html_only_write_rebuilds_the_binary(
        self, session_client, workspace, project, page, live_internal_key, monkeypatch
    ):
        calls = []

        def fake_sync(page_instance, force=False):
            calls.append((str(page_instance.id), force))
            return True

        monkeypatch.setattr("plane.app.views.page.base.sync_page_description_formats", fake_sync)
        monkeypatch.setattr(
            "plane.app.views.page.base.invalidate_live_document", lambda page_id, content_text_length=None: None
        )

        response = session_client.patch(
            description_url(workspace.slug, project.id, page.id),
            {"description_html": "<p>html only</p>"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        # html without a binary must rebuild json + binary (and invalidate) rather
        # than leaving the formats diverged
        assert calls == [(str(page.id), True)]

    @pytest.mark.django_db
    def test_a_wrong_live_key_is_treated_as_external(
        self, session_client, workspace, project, page, live_internal_key, monkeypatch
    ):
        invalidated = []
        monkeypatch.setattr(
            "plane.app.views.page.base.invalidate_live_document",
            lambda page_id, content_text_length=None: invalidated.append(page_id),
        )

        response = session_client.patch(
            description_url(workspace.slug, project.id, page.id),
            {
                "description_html": "<p>spoofed</p>",
                "description_binary": base64.b64encode(b"yjs-binary").decode(),
            },
            format="json",
            HTTP_X_LIVE_INTERNAL_KEY="not-the-key",
        )

        assert response.status_code == status.HTTP_200_OK
        assert invalidated == [str(page.id)]
