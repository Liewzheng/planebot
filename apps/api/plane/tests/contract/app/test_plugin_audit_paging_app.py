# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: the audit trail is read newest-first and cursor-paginated.

Before this change the listing sliced the **oldest** rows: a per-plugin panel
that passed no limit saw the first 200 events, so the newest transitions (the
latest disable, the last uninstall) were the first to disappear, and the
workspace-wide "removed plugins" inventory silently under-reported past 500
events. The trail is evidence, so neither is acceptable.

Pinned here:

* newest-first ordering by default and with ``?key=``;
* a trail longer than the default limit returns a cursor and the next page
  continues without gaps or repeats, even when a row is appended between two
  pages;
* the per-plugin case that was broken (newest transition on the first page);
* an over-large ``limit`` is clamped;
* the ``?action=uninstall&distinct=key`` inventory answers "which keys were
  ever uninstalled" in bounded, cursor-paged pages and never truncates;
* the body stays the plain JSON list it was (compatibility);
* cross-workspace isolation and the admin-only read still hold.
"""

from __future__ import annotations

import pytest
from rest_framework import status

from plane.db.models import User, Workspace, WorkspaceMember
from plane.plugins.constants import AuditAction, PluginState
from plane.plugins.models import PluginAuditEvent
from plane.tests.contract.app.test_plugin_registry_app import (
    PLUGIN_KEY,
    build_manifest,
    plugin_url,
    plugins_url,
)

NEXT_CURSOR_HEADER = "X-Audit-Next-Cursor"
HAS_MORE_HEADER = "X-Audit-Has-More"


def audit_url(slug):
    return f"{plugins_url(slug)}audit/"


def install_key(client, slug, key, **overrides):
    response = client.post(
        plugins_url(slug),
        {"manifest": build_manifest(key=key, name=key, **overrides)},
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED, response.data
    return response


def uninstall_key(client, slug, key):
    response = client.post(
        plugin_url(slug, key, "uninstall/"),
        {"confirm": key, "reason": f"retired {key}"},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    return response


def seed_trail(workspace, count, key="bulk-plugin", action=AuditAction.ENABLE):
    """Append ``count`` rows directly; returns their (monotonic) ids.

    ``bulk_create`` is an INSERT-only path, so it is allowed on the append-only
    trail, and it keeps the "long trail" tests fast.
    """
    events = [
        PluginAuditEvent(
            workspace_id=workspace.id,
            plugin_key=key,
            plugin_version="1.0.0",
            action=action,
            from_state=PluginState.INSTALLED,
            to_state=PluginState.ENABLED,
            actor_kind="system",
        )
        for _ in range(count)
    ]
    PluginAuditEvent.objects.bulk_create(events)
    return [event.id for event in events]


def walk(url, client):
    """Follow ``X-Audit-Next-Cursor`` to the end; returns every page body."""
    pages = []
    cursor = None
    while True:
        page_url = url if cursor is None else f"{url}&before_id={cursor}"
        response = client.get(page_url)
        assert response.status_code == status.HTTP_200_OK, response.data
        pages.append(response)
        if response.headers[HAS_MORE_HEADER] == "false":
            return pages
        cursor = response.headers[NEXT_CURSOR_HEADER]


@pytest.fixture
def member_user(db, workspace):
    user = User.objects.create(email="member-audit@plane.so", username="audit-member")
    user.set_password("password")
    user.save()
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=15, is_active=True)
    return user


@pytest.fixture
def installed(session_client, workspace):
    install_key(session_client, workspace.slug, PLUGIN_KEY)


@pytest.mark.contract
@pytest.mark.django_db
class TestAuditNewestFirst:
    def test_default_listing_is_newest_first(self, session_client, workspace, installed):
        session_client.post(plugin_url(workspace.slug, PLUGIN_KEY, "enable/"))

        response = session_client.get(audit_url(workspace.slug))
        assert response.status_code == status.HTTP_200_OK
        # The envelope is unchanged: still a plain list of the same row shape.
        assert isinstance(response.data, list)
        assert [row["action"] for row in response.data] == [
            AuditAction.ENABLE,
            AuditAction.INSTALL,
        ]
        ids = [row["id"] for row in response.data]
        assert ids == sorted(ids, reverse=True)

    def test_key_filter_keeps_newest_first(self, session_client, workspace, installed):
        session_client.post(plugin_url(workspace.slug, PLUGIN_KEY, "enable/"))
        session_client.post(plugin_url(workspace.slug, PLUGIN_KEY, "disable/"))

        response = session_client.get(f"{audit_url(workspace.slug)}?key={PLUGIN_KEY}")
        assert response.status_code == status.HTTP_200_OK
        assert [row["action"] for row in response.data] == [
            AuditAction.DISABLE,
            AuditAction.ENABLE,
            AuditAction.INSTALL,
        ]

    def test_per_plugin_trail_shows_the_newest_event_first(self, session_client, workspace, installed):
        """The exact broken case: a per-plugin trail longer than the default
        limit used to page the *oldest* rows, so the newest transition — the
        latest disable — was not in the response at all."""
        seed_trail(workspace, 300, key=PLUGIN_KEY)
        session_client.post(plugin_url(workspace.slug, PLUGIN_KEY, "enable/"))
        session_client.post(plugin_url(workspace.slug, PLUGIN_KEY, "disable/"))

        response = session_client.get(f"{audit_url(workspace.slug)}?key={PLUGIN_KEY}")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 200  # AUDIT_DEFAULT_LIMIT
        assert [row["action"] for row in response.data[:2]] == [
            AuditAction.DISABLE,
            AuditAction.ENABLE,
        ]
        # The old defect: with the oldest slice these rows were absent.
        assert response.headers[HAS_MORE_HEADER] == "true"
        assert response.headers[NEXT_CURSOR_HEADER]


@pytest.mark.contract
@pytest.mark.django_db
class TestAuditCursorPaging:
    def test_long_trail_pages_without_gaps_or_repeats(self, session_client, workspace):
        all_ids = seed_trail(workspace, 520)

        pages = walk(f"{audit_url(workspace.slug)}?limit=100", session_client)
        assert [len(page.data) for page in pages] == [100, 100, 100, 100, 100, 20]

        seen = [row["id"] for page in pages for row in page.data]
        assert seen == sorted(seen, reverse=True)
        assert set(seen) == set(all_ids)
        assert len(seen) == len(all_ids)  # no repeat

        # The last page announces exhaustion and carries no cursor.
        assert pages[-1].headers[HAS_MORE_HEADER] == "false"
        assert NEXT_CURSOR_HEADER not in pages[-1].headers

    def test_row_appended_between_pages_lands_above_the_cursor(self, session_client, workspace):
        """An append-only trail only grows at the top: a row written between
        two pages shows up in a fresh first page, never inside the older
        suffix, so the walk neither repeats nor skips."""
        original = seed_trail(workspace, 30)

        first = session_client.get(f"{audit_url(workspace.slug)}?limit=10")
        cursor = first.headers[NEXT_CURSOR_HEADER]

        appended = seed_trail(workspace, 1, key="late-plugin")[0]

        second = session_client.get(f"{audit_url(workspace.slug)}?limit=10&before_id={cursor}")
        assert appended not in [row["id"] for row in second.data]

        fresh = session_client.get(f"{audit_url(workspace.slug)}?limit=10")
        assert fresh.data[0]["id"] == appended

        # Walking the original trail still yields exactly the original rows.
        pages = walk(f"{audit_url(workspace.slug)}?limit=10", session_client)
        seen = [row["id"] for page in pages for row in page.data]
        assert set(seen) == set(original + [appended])
        assert len(seen) == len(set(seen))

    def test_over_large_limit_is_clamped(self, session_client, workspace):
        seed_trail(workspace, 520)

        response = session_client.get(f"{audit_url(workspace.slug)}?limit=1000000")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 500  # AUDIT_MAX_LIMIT
        assert response.headers[HAS_MORE_HEADER] == "true"

        for raw_limit in ("0", "-5"):
            response = session_client.get(f"{audit_url(workspace.slug)}?limit={raw_limit}")
            assert response.status_code == status.HTTP_200_OK
            assert len(response.data) == 1  # clamped to 1

    def test_paging_arguments_are_validated(self, session_client, workspace):
        base = audit_url(workspace.slug)
        for query in (
            "?limit=abc",
            "?before_id=abc",
            "?before_id=0",
            "?before_id=-3",
            "?action=not-an-action",
            "?distinct=plugin",
        ):
            response = session_client.get(f"{base}{query}")
            assert response.status_code == status.HTTP_400_BAD_REQUEST, query


@pytest.mark.contract
@pytest.mark.django_db
class TestRemovedPluginInventory:
    """``?action=uninstall&distinct=key`` is the server-side answer to "which
    keys were ever uninstalled".

    Chosen over a client-side scan because the aggregate is computed by the
    database over the whole workspace trail and the cursor applies to that
    aggregate: the inventory is paged, not sliced, so its size is unbounded and
    it cannot silently stop at a fixed number of events.
    """

    def test_lists_one_row_per_removed_key_newest_first(self, session_client, workspace):
        install_key(session_client, workspace.slug, "alpha-plugin")
        uninstall_key(session_client, workspace.slug, "alpha-plugin")
        install_key(session_client, workspace.slug, "beta-plugin")
        uninstall_key(session_client, workspace.slug, "beta-plugin")

        response = session_client.get(f"{audit_url(workspace.slug)}?action=uninstall&distinct=key")
        assert response.status_code == status.HTTP_200_OK
        keys = [row["plugin_key"] for row in response.data]
        assert keys == ["beta-plugin", "alpha-plugin"]  # newest removal first
        assert response.headers[HAS_MORE_HEADER] == "false"
        # The row still carries what the UI shows: version, actor, time, reason.
        assert response.data[0]["plugin_version"] == "1.0.0"
        assert response.data[0]["reason"] == "retired beta-plugin"

    def test_keys_that_were_never_uninstalled_are_absent(self, session_client, workspace):
        install_key(session_client, workspace.slug, "gamma-plugin")
        session_client.post(plugin_url(workspace.slug, "gamma-plugin", "enable/"))

        response = session_client.get(f"{audit_url(workspace.slug)}?action=uninstall&distinct=key")
        assert response.data == []

    def test_inventory_pages_past_any_size(self, session_client, workspace):
        """40 removed keys, paged ten at a time: every key appears exactly once
        and the walk ends — no fixed 500-row slice to under-report."""
        keys = [f"gone-{index:02d}" for index in range(40)]
        for key in keys:
            # One install + one uninstall row per key, seeded as raw append-only
            # inserts to keep this fast; the aggregate only looks at uninstalls.
            PluginAuditEvent.objects.bulk_create(
                [
                    PluginAuditEvent(
                        workspace_id=workspace.id,
                        plugin_key=key,
                        action=AuditAction.INSTALL,
                        to_state=PluginState.INSTALLED,
                    ),
                    PluginAuditEvent(
                        workspace_id=workspace.id,
                        plugin_key=key,
                        action=AuditAction.UNINSTALL,
                        from_state=PluginState.DISABLED,
                        to_state="deleted",
                    ),
                ]
            )

        url = f"{audit_url(workspace.slug)}?action=uninstall&distinct=key&limit=10"
        pages = walk(url, session_client)
        assert [len(page.data) for page in pages] == [10, 10, 10, 10]

        seen = [row["plugin_key"] for page in pages for row in page.data]
        assert len(seen) == 40
        assert set(seen) == set(keys)
        assert len(set(seen)) == len(seen)  # one row per key, never repeated

    def test_distinct_without_action_keeps_the_newest_event_per_key(self, session_client, workspace):
        install_key(session_client, workspace.slug, "gamma-plugin")
        session_client.post(plugin_url(workspace.slug, "gamma-plugin", "enable/"))

        response = session_client.get(f"{audit_url(workspace.slug)}?distinct=key")
        assert response.status_code == status.HTTP_200_OK
        assert [row["action"] for row in response.data] == [AuditAction.ENABLE]

    def test_a_removed_key_stays_a_single_row(self, session_client, workspace):
        """A key can be installed once per workspace and stays reserved after
        removal (``plugin_key_taken``), so "uninstalled twice" is not reachable
        through the API. The inventory would still collapse it to the newest
        uninstall row if it ever were."""
        install_key(session_client, workspace.slug, "alpha-plugin")
        uninstall_key(session_client, workspace.slug, "alpha-plugin")

        again = session_client.post(
            plugins_url(workspace.slug),
            {"manifest": build_manifest(key="alpha-plugin", name="alpha-plugin")},
            format="json",
        )
        assert again.status_code == status.HTTP_409_CONFLICT

        response = session_client.get(f"{audit_url(workspace.slug)}?action=uninstall&distinct=key")
        assert [row["plugin_key"] for row in response.data] == ["alpha-plugin"]

    def test_uninstall_events_stay_newest_first_without_distinct(self, session_client, workspace):
        install_key(session_client, workspace.slug, "alpha-plugin")
        uninstall_key(session_client, workspace.slug, "alpha-plugin")
        install_key(session_client, workspace.slug, "beta-plugin")
        uninstall_key(session_client, workspace.slug, "beta-plugin")

        response = session_client.get(f"{audit_url(workspace.slug)}?action=uninstall&limit=50")
        assert [row["plugin_key"] for row in response.data] == [
            "beta-plugin",
            "alpha-plugin",
        ]


@pytest.mark.contract
@pytest.mark.django_db
class TestAuditBoundaries:
    def test_other_workspace_events_do_not_leak(self, session_client, workspace, create_user):
        other = Workspace.objects.create(name="Other", owner=create_user, slug="other-audit")
        WorkspaceMember.objects.create(workspace=other, member=create_user, role=20, is_active=True)
        seed_trail(workspace, 2, key="mine")
        PluginAuditEvent.objects.create(
            workspace_id=other.id,
            plugin_key="theirs",
            action=AuditAction.UNINSTALL,
            from_state=PluginState.DISABLED,
            to_state="deleted",
        )

        response = session_client.get(audit_url(other.slug))
        assert [row["plugin_key"] for row in response.data] == ["theirs"]

        response = session_client.get(f"{audit_url(other.slug)}?action=uninstall&distinct=key")
        assert [row["plugin_key"] for row in response.data] == ["theirs"]

    def test_distinct_inventory_requires_admin(self, api_client, workspace, installed, member_user):
        api_client.force_authenticate(user=member_user)
        response = api_client.get(f"{audit_url(workspace.slug)}?action=uninstall&distinct=key")
        assert response.status_code == status.HTTP_403_FORBIDDEN
