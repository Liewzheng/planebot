# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Regression tests for GHSA-g49r-p85q-qq2w / GHSA-ghcr-frqr-6pqr.

ProjectPagePermission verified that the caller was a member of the URL
project_id, but PageVersionEndpoint resolved the page (and its versions) by
workspace + page_id only. A member of one project could therefore read the
page versions of a public page belonging to a *different* project in the same
workspace via that project's URL.
"""

import base64
import uuid
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status

from plane.app.serializers import PageDetailSerializer

from plane.db.models import (
    Page,
    PageVersion,
    Project,
    ProjectMember,
    ProjectPage,
    User,
)


def _page_versions_url(slug, project_id, page_id, pk=None):
    base = f"/api/workspaces/{slug}/projects/{project_id}/pages/{page_id}/versions/"
    return f"{base}{pk}/" if pk else base


def _make_project(workspace, identifier):
    return Project.objects.create(
        name=f"Project {identifier}",
        identifier=identifier,
        workspace=workspace,
    )


def _make_page(workspace, project, owner, access=Page.PUBLIC_ACCESS):
    page = Page.objects.create(
        workspace=workspace,
        owned_by=owner,
        access=access,
        name="Secret page",
    )
    ProjectPage.objects.create(workspace=workspace, project=project, page=page)
    return page


def _make_version(workspace, page, owner):
    return PageVersion.objects.create(
        workspace=workspace,
        page=page,
        owned_by=owner,
        description_html="<p>secret</p>",
    )


@pytest.mark.contract
class TestPageVersionProjectScope:
    """The attacker (create_user / session_client) is an active member of
    project_a only. Victim owns a public page in project_b."""

    def _setup(self, workspace, attacker):
        victim = User.objects.create(email="victim@plane.so", username=f"victim_{uuid.uuid4().hex[:8]}")

        project_a = _make_project(workspace, "PRJA")
        project_b = _make_project(workspace, "PRJB")

        # Attacker is an active member of project A only.
        ProjectMember.objects.create(workspace=workspace, project=project_a, member=attacker, role=20)

        # Public page + version living in project B (attacker is NOT a member).
        page_b = _make_page(workspace, project_b, victim)
        version_b = _make_version(workspace, page_b, victim)

        return victim, project_a, project_b, page_b, version_b

    @pytest.mark.django_db
    def test_cross_project_version_list_denied(self, session_client, workspace, create_user):
        """Listing another project's page versions via a project the attacker
        belongs to must be denied (was a 200 leak)."""
        _, project_a, _, page_b, _ = self._setup(workspace, create_user)

        response = session_client.get(_page_versions_url(workspace.slug, project_a.id, page_b.id))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.django_db
    def test_cross_project_version_detail_denied(self, session_client, workspace, create_user):
        """Reading a single cross-project page version must be denied."""
        _, project_a, _, page_b, version_b = self._setup(workspace, create_user)

        response = session_client.get(
            _page_versions_url(workspace.slug, project_a.id, page_b.id, pk=version_b.id)
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.django_db
    def test_same_project_public_page_versions_allowed(self, session_client, workspace, create_user):
        """A public page that genuinely belongs to the attacker's project is
        still readable, and its versions are returned."""
        victim, project_a, _, _, _ = self._setup(workspace, create_user)

        # Public page owned by the victim but linked to project A (attacker is a
        # member of A). Exercises the public-page access branch (not owner).
        page_a = _make_page(workspace, project_a, victim)
        version_a = _make_version(workspace, page_a, victim)

        response = session_client.get(_page_versions_url(workspace.slug, project_a.id, page_a.id))

        assert response.status_code == status.HTTP_200_OK
        returned_ids = {str(item["id"]) for item in response.json()}
        assert str(version_a.id) in returned_ids

    @pytest.mark.django_db
    def test_revoked_project_link_denied(self, session_client, workspace, create_user):
        """A page whose ProjectPage link to the attacker's project was
        soft-deleted (page removed from the project) must be denied, even
        though the attacker is a member of that project."""
        victim, project_a, _, _, _ = self._setup(workspace, create_user)

        page = Page.objects.create(
            workspace=workspace, owned_by=victim, access=Page.PUBLIC_ACCESS, name="Removed page"
        )
        # Link exists but is soft-deleted → the page no longer belongs to A.
        ProjectPage.objects.create(
            workspace=workspace, project=project_a, page=page, deleted_at=timezone.now()
        )
        _make_version(workspace, page, victim)

        response = session_client.get(_page_versions_url(workspace.slug, project_a.id, page.id))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.django_db
    def test_cross_project_version_list_not_a_member_anywhere(self, session_client, workspace, create_user):
        """Sanity: a project the attacker is not a member of is denied outright."""
        _, _, project_b, page_b, _ = self._setup(workspace, create_user)

        response = session_client.get(_page_versions_url(workspace.slug, project_b.id, page_b.id))

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.contract
class TestCollaborativeRevertGuard:
    """A collaborative store must not move a page back to a revision it left.

    A client holding a stale copy does exactly that, and it silently reverted
    content written through the API (PLANE-76). The guard only applies to the
    live server's own writes, so explicit API writes and "restore version" keep
    working.
    """

    def _setup(self, workspace, session_client, create_user):
        project = _make_project(workspace, "REV")
        ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
        page = _make_page(workspace, project, create_user)
        _make_version(workspace, page, create_user)  # the older revision
        newer = PageVersion.objects.create(
            workspace=workspace,
            page=page,
            owned_by=create_user,
            description_html="<p>newer content</p>",
        )
        page.description_html = newer.description_html
        page.save()
        return project, page

    @staticmethod
    def _store_url(workspace, project, page):
        return f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{page.id}/description/"

    def test_a_store_may_not_revert_to_an_earlier_revision(self, workspace, session_client, create_user, settings):
        project, page = self._setup(workspace, session_client, create_user)
        settings.LIVE_INTERNAL_API_KEY = "internal-secret"

        response = session_client.patch(
            self._store_url(workspace, project, page),
            {"description_html": "<p>secret</p>"},
            HTTP_X_LIVE_INTERNAL_KEY="internal-secret",
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        page.refresh_from_db()
        assert page.description_html == "<p>newer content</p>"

    def test_an_explicit_api_write_may_still_restore_an_earlier_revision(
        self, workspace, session_client, create_user, settings
    ):
        project, page = self._setup(workspace, session_client, create_user)
        settings.LIVE_INTERNAL_API_KEY = "internal-secret"

        # no live internal key: a deliberate write (version restore, CLI upload)
        response = session_client.patch(
            self._store_url(workspace, project, page),
            {"description_html": "<p>secret</p>"},
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.description_html == "<p>secret</p>"

    def test_a_store_that_keeps_the_newest_revision_is_persisted(
        self, workspace, session_client, create_user, settings
    ):
        project, page = self._setup(workspace, session_client, create_user)
        settings.LIVE_INTERNAL_API_KEY = "internal-secret"

        response = session_client.patch(
            self._store_url(workspace, project, page),
            {"description_html": '<p class="editor-paragraph-block">newer content</p>'},
            HTTP_X_LIVE_INTERNAL_KEY="internal-secret",
        )

        assert response.status_code == status.HTTP_200_OK


@pytest.mark.contract
class TestCollaborativeReSerializationIsIgnored:
    """Opening a page must not count as editing it.

    The collaborative client rewrites the markup it received (node ids, entity
    encoding, text-node splitting), so a store lands without anyone editing. It
    used to overwrite the canonical html and attribute the change to the person
    who merely had the page open.
    """

    def _setup(self, workspace, create_user):
        project = _make_project(workspace, "RESER")
        ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
        page = _make_page(workspace, project, create_user)
        page.description_html = "<p>" + "正文内容 " * 200 + "说明 &quot;quoted&quot;</p>"
        page.save()
        return project, page

    @pytest.mark.django_db
    def test_a_re_serialized_store_is_dropped(self, workspace, session_client, create_user, settings):
        project, page = self._setup(workspace, create_user)
        settings.LIVE_INTERNAL_API_KEY = "internal-secret"
        before = page.description_html
        before_updated_by = page.updated_by_id

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{page.id}/description/",
            {"description_html": page.description_html.replace("&quot;", '"')},
            HTTP_X_LIVE_INTERNAL_KEY="internal-secret",
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.description_html == before
        assert page.updated_by_id == before_updated_by

    @pytest.mark.django_db
    def test_a_real_edit_is_saved(self, workspace, session_client, create_user, settings):
        project, page = self._setup(workspace, create_user)
        settings.LIVE_INTERNAL_API_KEY = "internal-secret"
        edited = page.description_html + "<p>新增一段真实编辑内容。</p>"

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{page.id}/description/",
            {"description_html": edited},
            HTTP_X_LIVE_INTERNAL_KEY="internal-secret",
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert "新增一段真实编辑内容" in page.description_html


@pytest.mark.contract
class TestEditorSave:
    """Page content is written by an explicit save, and by nothing else.

    The live server no longer stores the collaborative document (opening or
    editing a page wrote revisions nobody made, PLANE-76), so the editor's save
    button is the only writer left on the web side. It identifies itself with
    `save_source: editor`, which the API uses to keep the collaborative document
    in memory: the save came from that very document, so dropping it would force
    a reload on every save.
    """

    def _setup(self, workspace, create_user, body="<p>" + "正文内容 " * 200 + "说明 &quot;quoted&quot;</p>"):
        project = _make_project(workspace, "ESAVE")
        ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
        page = _make_page(workspace, project, create_user)
        page.description_html = body
        page.save()
        return project, page

    @staticmethod
    def _save_url(workspace, project, page):
        return f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{page.id}/description/"

    @pytest.mark.django_db
    def test_a_save_that_re_serializes_the_content_is_dropped(self, workspace, session_client, create_user):
        project, page = self._setup(workspace, create_user)
        before = page.description_html
        before_updated_by = page.updated_by_id
        before_updated_at = page.updated_at

        response = session_client.patch(
            self._save_url(workspace, project, page),
            {
                "description_html": page.description_html.replace("&quot;", '"'),
                "save_source": "editor",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["message"] == "No content change"
        page.refresh_from_db()
        assert page.description_html == before
        assert page.updated_by_id == before_updated_by
        assert page.updated_at == before_updated_at

    @pytest.mark.django_db
    def test_a_formatting_only_edit_is_saved(self, workspace, session_client, create_user):
        """The no-op test must not swallow a change a reader sees.

        A bold run keeps the text identical and only adds a tag, so an identity
        built from the visible text alone would drop a real edit.
        """
        project, page = self._setup(workspace, create_user, body="<p>一句话</p>")

        response = session_client.patch(
            self._save_url(workspace, project, page),
            {"description_html": "<p><strong>一句话</strong></p>", "save_source": "editor"},
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert "<strong>" in page.description_html

    @pytest.mark.django_db
    def test_a_publish_drops_the_collaborative_document(self, workspace, session_client, create_user):
        """The shared document holds the previous revision once editing is local.

        An author edits a copy of their own now, so publishing replaces content
        the live server still has. Dropping its document is what makes every
        client — the author included — load the published revision.
        """
        project, page = self._setup(workspace, create_user, body="<p>一句话</p>")

        with patch("plane.app.views.page.base.invalidate_live_document") as invalidate:
            response = session_client.patch(
                self._save_url(workspace, project, page),
                {
                    "description_html": "<p>改过的一句话</p>",
                    "description_binary": base64.b64encode(b"published-yjs-binary").decode(),
                    "save_source": "editor",
                },
            )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.description_html == "<p>改过的一句话</p>"
        invalidate.assert_called_once()

    @pytest.mark.django_db
    def test_a_save_that_reverts_a_revision_is_refused(self, workspace, session_client, create_user):
        project, page = self._setup(workspace, create_user, body="<p>older revision</p>")
        PageVersion.objects.create(
            workspace=workspace,
            page=page,
            owned_by=create_user,
            description_html="<p>older revision</p>",
        )
        newer = PageVersion.objects.create(
            workspace=workspace,
            page=page,
            owned_by=create_user,
            description_html="<p>newer revision</p>",
        )
        page.description_html = newer.description_html
        page.save()

        with patch("plane.app.views.page.base.invalidate_live_document") as invalidate:
            response = session_client.patch(
                self._save_url(workspace, project, page),
                {"description_html": "<p>older revision</p>", "save_source": "editor"},
            )

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data["error_code"] == "CONTENT_REVERTED"
        # the editor is told what it lost to
        assert response.data["conflict"]["saved_by"] == create_user.display_name
        page.refresh_from_db()
        assert page.description_html == "<p>newer revision</p>"
        # and every client reloads the revision that won
        invalidate.assert_called_once()

    @pytest.mark.django_db
    def test_a_write_without_the_save_source_still_invalidates(self, workspace, session_client, create_user):
        """The API / CLI path replaces content behind the live server's back."""
        project, page = self._setup(workspace, create_user, body="<p>一句话</p>")

        with patch("plane.app.views.page.base.invalidate_live_document") as invalidate:
            response = session_client.patch(
                self._save_url(workspace, project, page),
                {
                    "description_html": "<p>别的一句话</p>",
                    "description_binary": base64.b64encode(b"plane-yjs-binary-payload").decode(),
                },
            )

        assert response.status_code == status.HTTP_200_OK
        invalidate.assert_called_once()


@pytest.mark.contract
class TestRestoreVersion:
    """Restoring a revision is a write.

    It used to be applied to the collaborative document from the browser and
    persisted by the live server's store. The store no longer persists anything
    (PLANE-76), so restoring has its own endpoint: it takes the revision's html,
    rebuilds the page's document formats from it and drops the in-memory
    document so every client reloads the restored revision.
    """

    def _setup(self, workspace, create_user):
        project = _make_project(workspace, "REST")
        ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
        page = _make_page(workspace, project, create_user)
        older = PageVersion.objects.create(
            workspace=workspace,
            page=page,
            owned_by=create_user,
            description_html="<p>the first revision</p>",
        )
        newer = PageVersion.objects.create(
            workspace=workspace,
            page=page,
            owned_by=create_user,
            description_html="<p>the second revision</p>",
        )
        page.description_html = newer.description_html
        page.save()
        return project, page, older

    @pytest.mark.django_db
    def test_a_revision_can_be_restored(self, workspace, session_client, create_user):
        project, page, older = self._setup(workspace, create_user)

        with patch("plane.app.views.page.version.invalidate_live_document"):
            response = session_client.post(
                f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{page.id}"
                f"/versions/{older.id}/restore/"
            )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.description_html == "<p>the first revision</p>"

    @pytest.mark.django_db
    def test_restoring_the_current_revision_writes_nothing(self, workspace, session_client, create_user):
        """A needless restore would rebuild the document binary for nothing.

        A rebuilt binary is a new set of Yjs identities, and every client still
        holding the old one merges that as a second copy of the whole page.
        """
        project, page, _older = self._setup(workspace, create_user)
        newest = PageVersion.objects.filter(page_id=page.id).order_by("-created_at").first()
        before_updated_at = page.updated_at

        with patch("plane.app.views.page.version.invalidate_live_document") as invalidate:
            response = session_client.post(
                f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{page.id}"
                f"/versions/{newest.id}/restore/"
            )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["message"] == "Page already holds this version"
        page.refresh_from_db()
        assert page.updated_at == before_updated_at
        invalidate.assert_not_called()

    @pytest.mark.django_db
    def test_restoring_a_locked_page_is_refused(self, workspace, session_client, create_user):
        project, page, older = self._setup(workspace, create_user)
        page.is_locked = True
        page.save()

        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{page.id}/versions/{older.id}/restore/"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        page.refresh_from_db()
        assert page.description_html == "<p>the second revision</p>"


@pytest.mark.contract
class TestBalloonedSaveIsRefused:
    """A client whose local copy was ballooned must not persist the balloon.

    Page bodies balloon when a stale client's Yjs copy is merged as a union: the
    page ends up holding its own content twice, and every client then syncs that
    back. Deleting the row in the database cannot reach a copy that lives in the
    browser, so the save itself is refused (and the live document dropped) when
    the incoming body repeats a quarter of itself and the stored page does not.
    """

    def _setup(self, workspace, create_user, body):
        project = _make_project(workspace, "BALLOON")
        ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
        page = _make_page(workspace, project, create_user)
        page.description_html = body
        page.save()
        return project, page

    @pytest.mark.django_db
    def test_a_ballooned_save_is_refused(self, workspace, session_client, create_user):
        paragraph = "<p>" + "".join(f"第{i}段内容，记录一次实测的数据与结论。" for i in range(60)) + "</p>"
        project, page = self._setup(workspace, create_user, paragraph)
        before_updated_at = page.updated_at

        with patch("plane.app.views.page.base.invalidate_live_document") as invalidate:
            response = session_client.patch(
                f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{page.id}/description/",
                {"description_html": paragraph * 2, "save_source": "editor"},
            )

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data["error_code"] == "CONTENT_DUPLICATED"
        page.refresh_from_db()
        assert page.description_html == paragraph
        assert page.updated_at == before_updated_at
        # the client holding the balloon is told to reload the stored copy
        invalidate.assert_called_once()

    @pytest.mark.django_db
    def test_a_body_that_repeats_itself_stays_writable(self, workspace, session_client, create_user):
        """A page that repeats a sentence or a row on purpose is not a balloon."""
        paragraph = "<p>" + "".join(f"第{i}段内容，记录一次实测的数据与结论。" for i in range(60)) + "</p>"
        project, page = self._setup(workspace, create_user, paragraph)
        edited = paragraph + "<p>结论：裁剪只在 rkcif 一层。</p>" * 3

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{page.id}/description/",
            {"description_html": edited, "save_source": "editor"},
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.description_html == edited


@pytest.mark.contract
class TestPageDraft:
    """An unpublished revision is private to its author (PLANE-77).

    A draft is kept beside the page: it never reaches `description_html`, the
    collaborative document, the version history or the public API, and every
    query is scoped to the caller — so another account can neither read a draft
    nor overwrite it, and the page stays exactly as everyone else sees it.
    """

    def _setup(self, workspace, create_user):
        project = _make_project(workspace, "DRAFT")
        ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
        page = _make_page(workspace, project, create_user)
        page.description_html = "<p>published</p>"
        page.save()
        return project, page

    @staticmethod
    def _draft_url(workspace, project, page):
        return f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{page.id}/draft/"

    @pytest.mark.django_db
    def test_a_draft_does_not_touch_the_page(self, workspace, session_client, create_user):
        project, page = self._setup(workspace, create_user)
        before_updated_at = page.updated_at

        response = session_client.patch(
            self._draft_url(workspace, project, page),
            {"description_html": "<p>unpublished work</p>"},
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.description_html == "<p>published</p>"
        assert page.updated_at == before_updated_at
        assert PageVersion.objects.filter(page_id=page.id).count() == 0

    @pytest.mark.django_db
    def test_the_author_reads_the_draft_back(self, workspace, session_client, create_user):
        project, page = self._setup(workspace, create_user)
        session_client.patch(self._draft_url(workspace, project, page), {"description_html": "<p>mine</p>"})

        response = session_client.get(self._draft_url(workspace, project, page))

        assert response.status_code == status.HTTP_200_OK
        assert response.data["description_html"] == "<p>mine</p>"

    @pytest.mark.django_db
    def test_no_draft_is_a_normal_answer(self, workspace, session_client, create_user):
        project, page = self._setup(workspace, create_user)

        response = session_client.get(self._draft_url(workspace, project, page))

        assert response.status_code == status.HTTP_200_OK
        assert response.data["description_html"] is None

    @pytest.mark.django_db
    def test_another_account_cannot_read_or_overwrite_a_draft(self, workspace, session_client, create_user):
        project, page = self._setup(workspace, create_user)
        session_client.patch(self._draft_url(workspace, project, page), {"description_html": "<p>mine</p>"})

        # a second member of the same project
        other = User.objects.create(email="other@plane.so", username=f"other_{uuid.uuid4().hex[:8]}")
        ProjectMember.objects.create(project=project, member=other, role=20, is_active=True)
        session_client.force_authenticate(user=other)

        read = session_client.get(self._draft_url(workspace, project, page))
        assert read.status_code == status.HTTP_200_OK
        assert read.data["description_html"] is None

        session_client.patch(self._draft_url(workspace, project, page), {"description_html": "<p>theirs</p>"})

        # the first author's draft is untouched
        session_client.force_authenticate(user=create_user)
        mine = session_client.get(self._draft_url(workspace, project, page))
        assert mine.data["description_html"] == "<p>mine</p>"

    @pytest.mark.django_db
    def test_a_draft_can_be_discarded_and_written_again(self, workspace, session_client, create_user):
        project, page = self._setup(workspace, create_user)
        url = self._draft_url(workspace, project, page)
        session_client.patch(url, {"description_html": "<p>first try</p>"})

        assert session_client.delete(url).status_code == status.HTTP_204_NO_CONTENT
        assert session_client.get(url).data["description_html"] is None

        # the unique constraint only covers live rows
        assert session_client.patch(url, {"description_html": "<p>second try</p>"}).status_code == status.HTTP_200_OK
        assert session_client.get(url).data["description_html"] == "<p>second try</p>"


@pytest.mark.contract
class TestPublishBaseRevision:
    """A publish is refused when the page moved on since the author started.

    Editing happens in a document of the author's own, seeded from the published
    revision; the publish sends that revision's `updated_at` back as the base.
    If the page's *content* changed meanwhile (someone published, restored a
    version, re-uploaded), accepting the publish would silently overwrite the
    newer revision — so it is refused, the author's work is kept as a draft, and
    the clients reload what won. A property-only change (rename, access, logo)
    must not refuse the publish: it does not replace the content the author is
    editing, and the author's own title edit lands on the page row right before
    publishing.
    """

    def _setup(self, workspace, create_user):
        project = _make_project(workspace, "BASER")
        ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
        page = _make_page(workspace, project, create_user)
        page.description_html = "<p>first publish</p>"
        page.save()
        return project, page

    @staticmethod
    def _save_url(workspace, project, page):
        return f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{page.id}/description/"

    @staticmethod
    def _properties_url(workspace, project, page):
        return f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{page.id}/"

    @staticmethod
    def _current_revision(page):
        """What the client sends back: the page detail serializer's updated_at."""
        return PageDetailSerializer(page).data["updated_at"]

    @pytest.mark.django_db
    def test_a_publish_from_a_stale_revision_is_refused(self, workspace, session_client, create_user):
        project, page = self._setup(workspace, create_user)
        base = self._current_revision(page)

        # someone else publishes first
        session_client.patch(
            self._save_url(workspace, project, page),
            {"description_html": "<p>second publish</p>", "save_source": "editor"},
        )
        page.refresh_from_db()
        assert page.description_html == "<p>second publish</p>"

        with patch("plane.app.views.page.base.invalidate_live_document") as invalidate:
            response = session_client.patch(
                self._save_url(workspace, project, page),
                {
                    "description_html": "<p>the stale author's edit</p>",
                    "description_binary": base64.b64encode(b"stale-yjs-binary").decode(),
                    "save_source": "editor",
                    "base_updated_at": base,
                },
            )

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data["error_code"] == "PAGE_VERSION_CONFLICT"
        # the winner is named, and the clients reload it
        assert response.data["updated_by"] == create_user.display_name
        invalidate.assert_called_once()
        page.refresh_from_db()
        assert page.description_html == "<p>second publish</p>"

    @pytest.mark.django_db
    def test_a_publish_on_the_current_revision_succeeds(self, workspace, session_client, create_user):
        project, page = self._setup(workspace, create_user)
        base = self._current_revision(page)

        response = session_client.patch(
            self._save_url(workspace, project, page),
            {
                "description_html": "<p>my edit</p>",
                "description_binary": base64.b64encode(b"my-yjs-binary").decode(),
                "save_source": "editor",
                "base_updated_at": base,
            },
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert response.data["updated_at"] == page.updated_at
        assert page.description_html == "<p>my edit</p>"

    @pytest.mark.django_db
    def test_a_publish_after_a_property_only_change_succeeds(self, workspace, session_client, create_user):
        """A rename (or any property change) moves updated_at but not the content
        revision: publishing must not be refused for it."""
        project, page = self._setup(workspace, create_user)
        base = self._current_revision(page)
        updated_before_rename = page.updated_at

        # only the name moves: auto_now bumps updated_at, the body is untouched
        response = session_client.patch(
            self._properties_url(workspace, project, page), {"name": "renamed"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.name == "renamed"
        assert page.updated_at > updated_before_rename

        response = session_client.patch(
            self._save_url(workspace, project, page),
            {
                "description_html": "<p>my edit after a rename</p>",
                "description_binary": base64.b64encode(b"my-yjs-binary").decode(),
                "save_source": "editor",
                "base_updated_at": base,
            },
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.description_html == "<p>my edit after a rename</p>"

    @pytest.mark.django_db
    def test_a_publish_without_a_base_is_not_checked(self, workspace, session_client, create_user):
        """API/CLI writers do not carry a base; they are deliberate overwrites."""
        project, page = self._setup(workspace, create_user)

        response = session_client.patch(
            self._save_url(workspace, project, page),
            {"description_html": "<p>overwrite</p>", "save_source": "editor"},
        )

        assert response.status_code == status.HTTP_200_OK
        page.refresh_from_db()
        assert page.description_html == "<p>overwrite</p>"
