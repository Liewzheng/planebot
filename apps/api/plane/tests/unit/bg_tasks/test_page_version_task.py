# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Page history: a content save must produce a page version

Regression cover for the task that never worked: it read `page.description`
(the field is `description_json`), raised AttributeError and was swallowed by
`log_exception`, so `page_versions` stayed empty no matter how often a page was
edited.
"""

import json

import pytest
from django.utils import timezone

from plane.bgtasks.page_version_task import PAGE_VERSION_TASK_TIMEOUT, track_page_version
from plane.db.models import Page, PageVersion, Project, ProjectMember, ProjectPage
from plane.utils.page_content import (
    DUPLICATED_BLOCK_MIN_LENGTH,
    duplicated_block_length,
    page_content_fingerprint,
    page_content_is_re_serialization,
    page_content_text_length,
)


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="Version Project",
        identifier="VER",
        workspace=workspace,
        created_by=create_user,
    )
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    return project


@pytest.fixture
def page(db, project, create_user):
    page = Page.objects.create(
        name="Versioned page",
        description_html="<p>v1</p>",
        description_json={"type": "doc", "content": []},
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


def run_task(page, old_html, user_id):
    """Call the task body synchronously (it is a celery task)."""
    track_page_version(
        page_id=str(page.id),
        existing_instance=json.dumps({"description_html": old_html}),
        user_id=str(user_id),
    )


@pytest.mark.unit
class TestTrackPageVersion:
    @pytest.mark.django_db
    def test_creates_a_version_when_the_content_changed(self, page, create_user):
        page.description_html = "<p>v2</p>"
        page.description_json = {"type": "doc", "content": [{"type": "paragraph"}]}
        page.save()

        run_task(page, "<p>v1</p>", create_user.id)

        version = PageVersion.objects.get(page=page)
        assert version.description_html == "<p>v2</p>"
        # the regression: the json column used to be read from a missing field
        assert version.description_json == {"type": "doc", "content": [{"type": "paragraph"}]}
        assert version.owned_by_id == create_user.id
        assert version.description_stripped == "v2"

    @pytest.mark.django_db
    def test_does_not_create_a_version_when_nothing_changed(self, page, create_user):
        run_task(page, "<p>v1</p>", create_user.id)

        assert PageVersion.objects.filter(page=page).count() == 0

    @pytest.mark.django_db
    def test_same_user_saves_within_the_window_share_one_version(self, page, create_user):
        page.description_html = "<p>v2</p>"
        page.save()
        run_task(page, "<p>v1</p>", create_user.id)

        page.description_html = "<p>v3</p>"
        page.save()
        run_task(page, "<p>v2</p>", create_user.id)

        versions = PageVersion.objects.filter(page=page)
        assert versions.count() == 1
        assert versions.first().description_html == "<p>v3</p>"

    @pytest.mark.django_db
    def test_another_user_starts_a_new_version(self, page, create_user, create_bot_user):
        page.description_html = "<p>v2</p>"
        page.save()
        run_task(page, "<p>v1</p>", create_user.id)

        page.description_html = "<p>v3</p>"
        page.save()
        run_task(page, "<p>v2</p>", create_bot_user.id)

        assert PageVersion.objects.filter(page=page).count() == 2

    @pytest.mark.django_db
    def test_a_save_after_the_window_starts_a_new_version(self, page, create_user):
        page.description_html = "<p>v2</p>"
        page.save()
        run_task(page, "<p>v1</p>", create_user.id)

        old = PageVersion.objects.get(page=page)
        old.last_saved_at = timezone.now() - timezone.timedelta(seconds=PAGE_VERSION_TASK_TIMEOUT + 60)
        old.save(update_fields=["last_saved_at"])

        page.description_html = "<p>v3</p>"
        page.save()
        run_task(page, "<p>v2</p>", create_user.id)

        assert PageVersion.objects.filter(page=page).count() == 2

    @pytest.mark.django_db
    def test_oldest_versions_are_trimmed(self, page, create_user):
        for index in range(25):
            page.description_html = f"<p>v{index}</p>"
            page.save()
            run_task(page, f"<p>v{index - 1}</p>", create_user.id)
            # keep each save outside the aggregation window so a new row is made
            latest = PageVersion.objects.filter(page=page).order_by("-last_saved_at").first()
            PageVersion.objects.filter(id=latest.id).update(
                last_saved_at=timezone.now() - timezone.timedelta(seconds=PAGE_VERSION_TASK_TIMEOUT + 60)
            )

        assert PageVersion.objects.filter(page=page).count() <= 20

    @pytest.mark.django_db
    def test_missing_actor_falls_back_to_the_page_owner(self, page, create_user):
        page.description_html = "<p>v2</p>"
        page.save()

        track_page_version(
            page_id=str(page.id),
            existing_instance=json.dumps({"description_html": "<p>v1</p>"}),
            user_id=None,
        )

        assert PageVersion.objects.get(page=page).owned_by_id == create_user.id

    @pytest.mark.django_db
    def test_a_re_serialization_of_the_same_content_is_not_a_version(self, page, create_user):
        """Re-storing a page must not invent history.

        The live server stores the document on connect and the editor's markup
        carries presentation attributes the import path does not, so the bytes
        differ while the content does not. Opening a page used to add a version.
        """
        page.description_html = "<p>v1</p>"
        page.save()
        run_task(page, "<p>v0</p>", create_user.id)
        assert PageVersion.objects.filter(page=page).count() == 1

        # the same content in the editor's flavour
        page.description_html = '<p class="editor-paragraph-block">\n  v1\n</p>'
        page.save()
        run_task(page, "<p>v1</p>", create_user.id)

        assert PageVersion.objects.filter(page=page).count() == 1

    @pytest.mark.django_db
    def test_a_real_change_after_a_re_serialization_is_still_recorded(self, page, create_user):
        page.description_html = '<p class="x">v1</p>'
        page.save()
        run_task(page, "<p>v1</p>", create_user.id)

        page.description_html = '<p class="x">v1 plus more</p>'
        page.save()
        run_task(page, '<p class="x">v1</p>', create_user.id)

        version = PageVersion.objects.get(page=page)
        assert version.description_html == '<p class="x">v1 plus more</p>'


class TestPageContentFingerprint:
    """The fingerprint decides whether a save is worth a history entry."""

    def test_presentation_attributes_do_not_change_it(self):
        plain = "<h2>标题</h2><p>正文</p>"
        editor = '<h2 class="editor-heading-block">标题</h2><p class="editor-paragraph-block">\n正文\n</p>'
        assert page_content_fingerprint(plain) == page_content_fingerprint(editor)

    def test_text_changes_it(self):
        assert page_content_fingerprint("<p>v1</p>") != page_content_fingerprint("<p>v2</p>")

    def test_structure_changes_it(self):
        assert page_content_fingerprint("<h2>标题</h2>") != page_content_fingerprint("<p>标题</p>")

    def test_an_image_target_changes_it(self):
        one = '<img src="asset-one" alt="fig">'
        other = '<img src="asset-two" alt="fig">'
        assert page_content_fingerprint(one) != page_content_fingerprint(other)

    def test_it_is_stable_for_empty_content(self):
        assert page_content_fingerprint(None) == page_content_fingerprint("")


class TestPageContentTextLength:
    """The live server's shrink guard compares against the same measure."""

    def test_it_counts_visible_text_only(self):
        assert page_content_text_length("<p>abc</p>") == 3
        assert page_content_text_length('<p class="editor-paragraph-block">\n  a b c\n</p>') == 3

    def test_it_is_zero_for_empty_content(self):
        assert page_content_text_length(None) == 0
        assert page_content_text_length("") == 0


class TestReSerialization:
    """Re-serializing the same content must not be recorded as an edit.

    Reported case: a page the user never touched showed up as modified by them,
    because the collaborative store re-encoded `&quot;` as `"` and split a text
    node around an emoji (21 characters of 5998).
    """

    def test_entity_encoding_and_text_splitting_is_not_a_change(self):
        import_path = '<p>SmolLM2-135M int16：✅ 21 tok/s</p><p>说明 &quot;quoted&quot;</p>'
        editor_round_trip = '<p>SmolLM2-135M int16：<span>✅</span> 21 tok/s</p><p>说明 "quoted"</p>'

        assert page_content_is_re_serialization(import_path, editor_round_trip)

    def test_a_real_edit_is_a_change(self):
        before = "<p>" + "正文内容 " * 200 + "</p>"
        after = "<p>" + "正文内容 " * 200 + "新增了一段说明文字。" * 10 + "</p>"

        assert not page_content_is_re_serialization(before, after)

    def test_swapping_an_image_is_a_change(self):
        before = '<p>图</p><img src="asset-one">'
        after = '<p>图</p><img src="asset-two">'

        assert not page_content_is_re_serialization(before, after)


class TestDuplicatedBlockLength:
    """Detects a body that holds its own content twice (a stale client's merge)."""

    def test_it_is_zero_for_a_normal_body(self):
        body = "<p>" + "".join(f"第{i}段内容，记录一次实测的数据与结论。" for i in range(80)) + "</p>"

        assert duplicated_block_length(body) == 0

    def test_it_is_zero_for_a_short_body(self):
        assert duplicated_block_length("<p>" + "短正文" * 20 + "</p>") == 0
        assert duplicated_block_length(None) == 0

    def test_it_repeats_a_sentence_without_flagging_it(self):
        """A phrase repeated on purpose is not the document duplicated."""
        body = "<p>" + "结论：裁剪只在 rkcif 一层。" * 6 + "</p>"

        assert duplicated_block_length(body) == 0

    def test_it_finds_the_block_when_the_body_holds_itself_twice(self):
        paragraph = "<p>" + "".join(f"第{i}段内容，记录一次实测的数据与结论。" for i in range(60)) + "</p>"
        ballooned = paragraph * 2

        assert duplicated_block_length(ballooned) >= DUPLICATED_BLOCK_MIN_LENGTH
        assert duplicated_block_length(paragraph) == 0
