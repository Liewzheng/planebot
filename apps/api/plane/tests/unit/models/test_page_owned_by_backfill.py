# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Unit tests for the M62 ``Page.owned_by`` bot-to-owner backfill.

Pins the M62 contract:
* Bot-owned **live** pages get re-pointed at the workspace's human owner.
* Bot-owned **soft-deleted** pages get re-pointed too — ``deleted_at`` is
  not part of the predicate; a restored page must not come back bot-owned.
* Human-owned pages are untouched — and the assertion **matters**: the
  page fixture's owner is a *different* human from ``workspace.owner``,
  so a widened ``Page.objects.all()`` migration would silently repoint
  the row, and the test would go red.
* The forward pass is idempotent — running it twice yields the same
  state and does not silently change non-bot rows.
* The migration does not flip ``PageVersion.owned_by``,
  ``PageDraft.owned_by``, ``Page.created_by`` or ``Page.access``.

Important — these tests exercise the migration through the **historical**
app registry (``historical_apps``), not the live one. The live
``Page.objects`` is ``SoftDeletionManager`` (filters ``deleted_at``); the
historical ``Page.objects`` is a plain ``Manager`` because Django drops
managers lacking ``use_in_migrations=True`` from migration state. Running
the migration against the live registry would let 69 of the 142 bot-
owned rows slip past the harness and so fail to pin the most important
behavioural difference between this migration and a hand-rolled
``manage.py shell`` UPDATE. The same shape is the established pattern in
``plane/tests/contract/app/test_service_scope_list_retirement.py``.
"""

from importlib import import_module

import pytest
from django.apps import apps as django_apps
from django.db.migrations.state import ProjectState

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


@pytest.fixture
def historical_apps():
    """The app registry a migration receives under ``manage.py migrate``.

    Mirrors the fixture in
    ``plane/tests/contract/app/test_service_scope_list_retirement.py``.
    Returns models whose default manager is plain ``Manager``, so the
    migration's ``Page.objects.filter(...)`` is unfiltered and matches
    what ``migrate`` actually sees.
    """
    return ProjectState.from_apps(django_apps).apps


def _run_migration(historical_apps):
    mig = import_module(MIGRATION_MODULE)
    mig.backfill_page_owned_by(historical_apps, None)


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
def workspace_owner(db):
    """The workspace's owner — a distinct human from every page's
    real owner, so a widened predicate would visibly flip things."""
    user = User.objects.create(
        email="ws-owner-m62@plane.so",
        username="ws_owner_m62",
        first_name="Workspace",
        last_name="Owner",
    )
    user.set_password("human@123")
    user.save()
    return user


@pytest.fixture
def page_author(db):
    """A second human who actually authored test pages. Distinct from
    ``workspace_owner`` on purpose: production pages are usually owned
    by someone other than the workspace owner, and the test must
    discriminate a "repoint every page" bug from the correct "repoint
    only bot-owned pages" shape."""
    user = User.objects.create(
        email="page-author-m62@plane.so",
        username="page_author_m62",
        first_name="Page",
        last_name="Author",
    )
    user.set_password("human@123")
    user.save()
    return user


@pytest.fixture
def workspace(db, workspace_owner):
    ws = Workspace.objects.create(
        name="M62 Workspace",
        slug="m62-workspace",
        owner=workspace_owner,
    )
    WorkspaceMember.objects.create(
        workspace=ws, member=workspace_owner, role=20, is_active=True
    )
    return ws


@pytest.fixture
def bot_owned_page(workspace, bot_user, page_author):
    """A live page mirroring the pre-M39 bug shape: bot is on
    ``owned_by`` and ``created_by`` simultaneously. Uses
    ``disable_auto_set_user=True`` because pytest has no request
    context, so ``BaseModel.save`` would otherwise overwrite
    ``created_by`` with ``get_current_user()`` (``None``)."""
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
def soft_deleted_bot_owned_page(workspace, bot_user, page_author):
    """A soft-deleted page still owned by a bot. The migration is
    expected to repoint this row too: ``deleted_at`` is not part of
    the predicate, and a future undelete would otherwise surface a
    bot-owned page back into the workspace."""
    page = Page(
        workspace=workspace,
        name="Soft-deleted bot-owned page",
        owned_by=bot_user,
        created_by=bot_user,
        access=0,
    )
    page.save(disable_auto_set_user=True)
    page.delete(soft=True)
    assert page.deleted_at is not None
    return page


@pytest.fixture
def human_owned_page(workspace, page_author):
    """A live page owned by ``page_author`` — a **different human** from
    the workspace owner. If the migration ever widens to
    ``Page.objects.all()`` this row gets silently flipped to
    ``workspace_owner``, and this test goes red; that is the
    discrimination the review pinned."""
    page = Page(
        workspace=workspace,
        name="Human-owned page",
        owned_by=page_author,
        created_by=page_author,
        access=0,
    )
    page.save(disable_auto_set_user=True)
    return page


@pytest.mark.django_db
class TestHistoricalManagerSemantics:
    """Pin the precondition that makes the rest of the suite faithful.

    If a future change flips ``SoftDeletionManager.use_in_migrations``
    to ``True`` (or otherwise tricks Django into keeping it on the
    historical model), the migration will start missing the soft-
    deleted rows and the test below catches it.
    """

    def test_migrations_see_a_plain_manager(self, historical_apps):
        from django.db.models import Manager

        historical_page = historical_apps.get_model("db", "Page")
        assert type(historical_page.objects) is Manager, (
            "Page.objects on the historical model must be the plain Manager — "
            "otherwise the migration will silently skip soft-deleted rows"
        )


@pytest.mark.django_db
class TestPageOwnedByBackfill:
    def test_bot_owned_page_is_repointed_to_workspace_owner(
        self, historical_apps, workspace, bot_user, workspace_owner,
        page_author, bot_owned_page,
    ):
        """A live bot-owned page is repointed to ``workspace.owner``;
        ``created_by`` is intentionally preserved because the authz
        engine reads it as the attribution column."""
        assert bot_owned_page.owned_by_id == bot_user.id
        assert bot_owned_page.created_by_id == bot_user.id

        _run_migration(historical_apps)

        bot_owned_page.refresh_from_db()
        assert bot_owned_page.owned_by_id == workspace_owner.id, (
            "backfill did not repoint bot-owned page to workspace owner"
        )
        assert bot_owned_page.created_by_id == bot_user.id, (
            "backfill must not modify Page.created_by"
        )
        assert bot_owned_page.access == 0, (
            "backfill must not modify Page.access"
        )

    def test_soft_deleted_bot_owned_page_is_also_repointed(
        self, historical_apps, workspace, bot_user, workspace_owner,
        soft_deleted_bot_owned_page,
    ):
        """The migration's predicate ignores ``deleted_at``; this pins
        the soft-deleted half of the production footprint (69 of 142)
        so a future re-introduction of the ``SoftDeletionManager`` on
        the historical model would go red here."""
        # Sanity: the soft-deleted row is invisible to the live
        # SoftDeletionManager (so the harness matches production).
        assert Page.objects.filter(
            id=soft_deleted_bot_owned_page.id
        ).count() == 0
        assert Page.all_objects.filter(
            id=soft_deleted_bot_owned_page.id
        ).count() == 1

        _run_migration(historical_apps)

        # Pull the row back via all_objects — it must now be owned by
        # the human workspace owner.
        row = Page.all_objects.get(id=soft_deleted_bot_owned_page.id)
        assert row.owned_by_id == workspace_owner.id, (
            "soft-deleted bot-owned page must be repointed — a future "
            "undelete must not surface a bot-owned page"
        )
        assert row.deleted_at is not None, "soft-delete state must survive"

    def test_human_owned_page_is_left_alone(
        self, historical_apps, workspace, workspace_owner, page_author,
        human_owned_page,
    ):
        """The page is owned by ``page_author`` (NOT ``workspace_owner``).
        With the correct predicate the row stays on ``page_author``;
        a widened predicate would silently flip it to ``workspace_owner``,
        which this test pins."""
        assert human_owned_page.owned_by_id == page_author.id
        assert human_owned_page.owned_by_id != workspace_owner.id, (
            "fixture sanity: page author and workspace owner must "
            "differ for this discrimination to be meaningful"
        )

        snapshot = (
            human_owned_page.owned_by_id,
            human_owned_page.created_by_id,
            human_owned_page.access,
        )

        _run_migration(historical_apps)

        human_owned_page.refresh_from_db()
        assert (
            human_owned_page.owned_by_id,
            human_owned_page.created_by_id,
            human_owned_page.access,
        ) == snapshot, (
            "human-owned page must remain on its human author — the "
            "discriminator between 'repoint bot-owned' and 'repoint all'"
        )

    def test_idempotent_repeat_run_is_noop(
        self, historical_apps, workspace, bot_user, workspace_owner,
        page_author, bot_owned_page, human_owned_page,
    ):
        """Run #1 does the work; run #2 sees zero bot-owned rows and
        leaves the rows from run #1 alone. Idempotency is structurally
        guaranteed by the ``owned_by__is_bot=True`` predicate, but the
        test still proves the contract from the user side."""
        _run_migration(historical_apps)
        bot_owned_page.refresh_from_db()
        after_first = bot_owned_page.owned_by_id
        assert after_first == workspace_owner.id

        # No bot-owned rows remain, so a second run must leave them
        # alone (and must not silently mutate the human-owned row).
        pre_second = list(
            Page.all_objects.order_by("id").values_list(
                "id", "owned_by_id", "created_by_id", "access"
            )
        )
        _run_migration(historical_apps)
        post_second = list(
            Page.all_objects.order_by("id").values_list(
                "id", "owned_by_id", "created_by_id", "access"
            )
        )

        assert pre_second == post_second

        bot_owned_page.refresh_from_db()
        assert bot_owned_page.owned_by_id == after_first

        human_owned_page.refresh_from_db()
        assert human_owned_page.owned_by_id == page_author.id, (
            "human-owned page must survive a second migration run"
        )

    def test_left_untouched_columns_remain_intact(
        self, historical_apps, workspace, bot_user, workspace_owner,
        page_author, bot_owned_page,
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

        _run_migration(historical_apps)

        bot_owned_page.refresh_from_db()
        assert bot_owned_page.created_by_id == bot_user.id
        assert bot_owned_page.access == 0

        assert PageVersion.objects.filter(
            page=bot_owned_page, owned_by=bot_user
        ).exists(), "PageVersion.owned_by must not be touched"
        assert PageDraft.objects.filter(
            page=bot_owned_page, owned_by=bot_user
        ).exists(), "PageDraft.owned_by must not be touched"

    def test_zero_bot_owned_rows_is_a_noop(
        self, historical_apps, workspace, workspace_owner, page_author,
        human_owned_page,
    ):
        """Idempotency hold: even when there is nothing to do, the
        function must succeed and leave the row exactly as it was."""
        _run_migration(historical_apps)
        human_owned_page.refresh_from_db()
        assert human_owned_page.owned_by_id == page_author.id
        assert Page.all_objects.filter(
            owned_by__is_bot=True
        ).count() == 0
