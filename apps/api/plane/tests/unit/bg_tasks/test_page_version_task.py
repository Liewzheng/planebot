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
