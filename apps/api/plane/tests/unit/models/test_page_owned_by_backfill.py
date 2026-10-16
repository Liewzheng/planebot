# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Unit tests for the M62 ``Page.owned_by`` bot-to-owner backfill.

Pins the M62 contract:
* Bot-owned pages get re-pointed at the workspace's human owner.
* Human-owned pages are untouched.
* The forward pass is idempotent — running it twice yields the same
  state and does not silently change non-bot rows.
* The migration does not flip ``PageVersion.owned_by``,
  ``PageDraft.owned_by``, ``Page.created_by`` or ``Page.access``.
"""

from importlib import import_module

import pytest
from django.apps import apps as django_apps

from plane.db.models import (
    Page,
    PageDraft,
    PageVersion,
    User,
    Workspace,
    WorkspaceMember,
)


pytestmark = pytest.mark.unit


MIGRATION_MODULE = "plane.db.migrations.0129_backfill_page_owned_by"


def _human_owned_pages(qs):
    return qs.filter(owned_by__is_bot=False)


def _bot_owned_pages(qs):
    return qs.filter(owned_by__is_bot=True)


@pytest.fixture
def bot_user(db):
    user = User.objects.create(
        email="bot-m62@plane.so",
        username="bot_m62",
        first_name="Bot",
        last_name="M62",
        is_bot=True,
    )
    user.set_password("bot@123")
    user.save()
    return user


@pytest.fixture
def human_user(db):
    user = User.objects.create(
        email="human-m62@plane.so",
        username="human_m62",
        first_name="Human",
        last_name="M62",
    )
    user.set_password("human@123")
    user.save()
    return user


@pytest.fixture
def workspace(db, human_user):
    ws = Workspace.objects.create(
        name="M62 Workspace",
        slug="m62-workspace",
        owner=human_user,
    )
    WorkspaceMember.objects.create(
        workspace=ws, member=human_user, role=20, is_active=True
    )
    return ws


@pytest.fixture
def bot_owned_page(workspace, bot_user, human_user):
    """Page mirroring the pre-M39 bug shape: bot carried ``owned_by``
    and ``created_by`` simultaneously. Uses ``created_by_id=`` and
    ``disable_auto_set_user=True`` because ``BaseModel.save`` will
    otherwise overwrite ``created_by`` with ``get_current_user()``,
    which is ``None`` in a pytest request."""
    page = Page(
        workspace=workspace,
        name="Bot-owned page",
        owned_by=bot_user,
        created_by=bot_user,
        access=0,
    )
    page.save(disable_auto_set_user=True)
    return page


@pytest.fixture
def human_owned_page(workspace, human_user):
    page = Page(
        workspace=workspace,
        name="Human-owned page",
        owned_by=human_user,
        created_by=human_user,
        access=0,
    )
    page.save(disable_auto_set_user=True)
    return page


def _run_migration():
    mig = import_module(MIGRATION_MODULE)
    mig.backfill_page_owned_by(django_apps, None)


class TestPageOwnedByBackfill:
    @pytest.mark.django_db
    def test_bot_owned_page_is_repointed_to_workspace_owner(
        self, workspace, bot_user, human_user, bot_owned_page
    ):
        """The forward pass flips ``owned_by`` from the bot to the
        workspace owner; ``created_by`` is intentionally preserved
        because the authz engine reads it as the attribution column."""
        assert bot_owned_page.owned_by_id == bot_user.id
        assert bot_owned_page.created_by_id == bot_user.id

        _run_migration()

        bot_owned_page.refresh_from_db()
        assert bot_owned_page.owned_by_id == human_user.id, (
            "backfill did not repoint bot-owned page to workspace owner"
        )
        assert bot_owned_page.created_by_id == bot_user.id, (
            "backfill must not modify Page.created_by"
        )
        assert bot_owned_page.access == 0, (
            "backfill must not modify Page.access"
        )

    @pytest.mark.django_db
    def test_human_owned_page_is_left_alone(
        self, workspace, human_user, human_owned_page
    ):
        snapshot = (
            human_owned_page.owned_by_id,
            human_owned_page.created_by_id,
            human_owned_page.access,
        )

        _run_migration()

        human_owned_page.refresh_from_db()
        assert (
            human_owned_page.owned_by_id,
            human_owned_page.created_by_id,
            human_owned_page.access,
        ) == snapshot

    @pytest.mark.django_db
    def test_idempotent_repeat_run_is_noop(
        self, workspace, bot_user, human_user,
        bot_owned_page, human_owned_page,
    ):
        """Run #1 does the work; run #2 sees zero bot-owned rows and
        leaves the rows from run #1 alone."""
        _run_migration()
        bot_owned_page.refresh_from_db()
        after_first = bot_owned_page.owned_by_id
        assert after_first == human_user.id

        # No bot-owned rows remain, so a second run must leave them
        # alone (and must not silently mutate the human-owned row).
        pre_second = list(
            Page.objects.order_by("id").values_list(
                "id", "owned_by_id", "created_by_id", "access"
            )
        )
        _run_migration()
        post_second = list(
            Page.objects.order_by("id").values_list(
                "id", "owned_by_id", "created_by_id", "access"
            )
        )

        assert pre_second == post_second

        bot_owned_page.refresh_from_db()
        assert bot_owned_page.owned_by_id == after_first

    @pytest.mark.django_db
    def test_left_untouched_columns_remain_intact(
        self, workspace, bot_user, human_user, bot_owned_page
    ):
        """Pin the do-not-touch list: ``created_by``, ``access``,
        ``PageVersion.owned_by``, ``PageDraft.owned_by``. The mission
        forbids touching any of these even if they happened to be
        bot-owned elsewhere."""
        PageVersion.objects.create(
            workspace=workspace,
            page=bot_owned_page,
            owned_by=bot_user,
            description_html="<p></p>",
            last_saved_at=bot_owned_page.created_at,
        )
        PageDraft.objects.create(
            workspace=workspace,
            page=bot_owned_page,
            owned_by=bot_user,
            description_html="<p></p>",
        )

        _run_migration()

        bot_owned_page.refresh_from_db()
        assert bot_owned_page.created_by_id == bot_user.id
        assert bot_owned_page.access == 0

        assert PageVersion.objects.filter(
            page=bot_owned_page, owned_by=bot_user
        ).exists(), "PageVersion.owned_by must not be touched"
        assert PageDraft.objects.filter(
            page=bot_owned_page, owned_by=bot_user
        ).exists(), "PageDraft.owned_by must not be touched"

    @pytest.mark.django_db
    def test_zero_bot_owned_rows_is_a_noop(
        self, workspace, human_user, human_owned_page
    ):
        """Idempotency hold: even when there is nothing to do, the
        function must succeed and leave the row exactly as it was."""
        _run_migration()
        human_owned_page.refresh_from_db()
        assert human_owned_page.owned_by_id == human_user.id
        assert _bot_owned_pages(Page.objects.all()).count() == 0
