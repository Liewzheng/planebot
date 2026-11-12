# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: the plugin audit trail is append-only.

Design §4.2 requires the audit to be immutable at the *database* level, not
just by convention. Two mechanisms back that up and both are pinned here:

* the model layer refuses an UPDATE / DELETE on an existing row
  (``AppendOnlyQuerySet`` + ``PluginAuditEvent.save/delete``);
* Postgres refuses the same operation through a ``BEFORE UPDATE OR DELETE``
  trigger installed by migration 0001. The test runs the trigger DDL itself
  because the pytest suite uses ``--nomigrations`` (the table is built from
  the models, not from the migration), and the DDL rolls back with the test
  transaction.
"""

from __future__ import annotations

import pytest
from django.db import DatabaseError, connection, transaction

from plane.db.models import Workspace, WorkspaceMember
from plane.plugins.append_only import (
    CREATE_AUDIT_APPEND_ONLY_SQL,
    DROP_AUDIT_APPEND_ONLY_SQL,
)
from plane.plugins.audit import AuditAppendOnlyError
from plane.plugins.models import PluginAuditEvent


def seed_event(workspace):
    return PluginAuditEvent.objects.create(
        workspace_id=workspace.id,
        plugin_key="pdf-templates",
        plugin_version="1.0.0",
        action="install",
        from_state="",
        to_state="installed",
        actor_kind="human",
        reason="seed",
    )


def install_trigger():
    with connection.cursor() as cursor:
        cursor.execute(CREATE_AUDIT_APPEND_ONLY_SQL)


def drop_trigger():
    with connection.cursor() as cursor:
        cursor.execute(DROP_AUDIT_APPEND_ONLY_SQL)


def raw_statement(sql, params):
    """Run one raw statement; the savepoint keeps the test transaction usable
    after the expected database error."""
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(sql, params)


@pytest.mark.contract
@pytest.mark.django_db
class TestAuditAppendOnlyModelLayer:
    def test_creating_a_row_is_allowed(self, workspace):
        event = seed_event(workspace)
        assert PluginAuditEvent.objects.filter(pk=event.pk).exists()

    def test_instance_save_on_existing_row_is_refused(self, workspace):
        event = seed_event(workspace)
        event.reason = "rewritten"
        with pytest.raises(AuditAppendOnlyError):
            event.save()

    def test_instance_delete_is_refused(self, workspace):
        event = seed_event(workspace)
        with pytest.raises(AuditAppendOnlyError):
            event.delete()

    def test_queryset_update_is_refused(self, workspace):
        event = seed_event(workspace)
        with pytest.raises(AuditAppendOnlyError):
            PluginAuditEvent.objects.filter(pk=event.pk).update(reason="rewritten")

    def test_queryset_delete_is_refused(self, workspace):
        seed_event(workspace)
        with pytest.raises(AuditAppendOnlyError):
            PluginAuditEvent.objects.all().delete()

    def test_no_soft_delete_manager(self, workspace):
        """The audit model deliberately carries no ``deleted_at`` /
        ``all_objects`` pair — there is no soft-delete escape hatch."""
        assert not hasattr(PluginAuditEvent, "all_objects")
        assert "deleted_at" not in {field.name for field in PluginAuditEvent._meta.fields}


@pytest.mark.contract
@pytest.mark.django_db
class TestAuditAppendOnlyDatabaseLayer:
    def test_trigger_refuses_raw_update(self, workspace):
        event = seed_event(workspace)
        install_trigger()
        try:
            with pytest.raises(DatabaseError):
                raw_statement(
                    "UPDATE plugin_audit_events SET reason = %s WHERE id = %s",
                    ["rewritten in SQL", event.pk],
                )
        finally:
            drop_trigger()

    def test_trigger_refuses_raw_delete(self, workspace):
        event = seed_event(workspace)
        install_trigger()
        try:
            with pytest.raises(DatabaseError):
                raw_statement("DELETE FROM plugin_audit_events WHERE id = %s", [event.pk])
        finally:
            drop_trigger()
        assert PluginAuditEvent.objects.filter(pk=event.pk).exists()

    def test_trigger_still_allows_insert(self, workspace):
        install_trigger()
        try:
            event = seed_event(workspace)
            assert PluginAuditEvent.objects.filter(pk=event.pk).exists()
        finally:
            drop_trigger()


@pytest.mark.contract
@pytest.mark.django_db
class TestAuditWorkspaceIndependence:
    def test_audit_survives_workspace_row_removal(self, workspace, create_user):
        """``workspace_id`` is a plain UUID column, not a FK: an audit row
        must outlive the workspace row it names (an append-only trail cannot
        be cascaded away)."""
        event = seed_event(workspace)
        field = PluginAuditEvent._meta.get_field("workspace_id")
        assert field.is_relation is False

        other = Workspace.objects.create(name="Doomed", owner=create_user, slug="doomed-ws")
        WorkspaceMember.objects.create(workspace=other, member=create_user, role=20, is_active=True)
        other_event = PluginAuditEvent.objects.create(
            workspace_id=other.id,
            plugin_key="mermaid",
            action="install",
            to_state="installed",
            actor_kind="human",
        )
        Workspace.all_objects.filter(pk=other.pk).delete()
        assert PluginAuditEvent.objects.filter(pk=other_event.pk).exists()
        assert PluginAuditEvent.objects.filter(pk=event.pk).exists()
