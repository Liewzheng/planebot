# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Retire the unreachable ``action='list'`` ServiceScope rows.

``list`` is not a member of ``service_principals.constants.ACTION_CHOICES``,
yet legacy rows carrying it exist: ``migrate_ai_accounts_to_service_principals``
used to copy ``AIScopePolicy.action`` verbatim (an ORM write, bypassing the
serializer), and the legacy AI-account policy vocabulary did include ``list``.
Those rows break the SP management modal — it echoes the rows it loaded back to
the scope PUT endpoint, ``ServiceScopeInputSerializer.action``
(``serializers.py``) rejects ``list``, and the whole PUT answers 400.

Deleting them is permission-equivalent, for four reasons:

1. ``core/authz/engine.py::_scope_action_set`` maps a ``list`` request to
   ``[list, read, all]``, so a ``read`` row already satisfies every ``list``
   request, while the SP management API only ever exposes ``read`` — a scope
   row keyed on ``list`` is unreachable in practice.
2. ``_scope_action_set('read')`` is ``[read, all]``, so a ``list`` row does
   *not* satisfy a ``read`` request: dropping one narrows nothing as long as
   the ``read`` sibling stays, and it widens nothing either.
3. Measured on the self-host database right before this migration: all 28
   active ``list`` rows had a non-deleted ``read`` sibling on the same
   ``(service_principal_id, resource_type, project_id)`` — zero orphans — so
   every row that exists in practice is deletable without changing reach.
4. Nothing regenerates them: the serializer is the only write path and it
   rejects ``list``, so the action vocabulary stays clean once these rows are
   gone (the fix is *not* to add ``list`` to ``ACTION_CHOICES``, which would
   legitimize dead data the UI can never create).

Guardrail: a ``list`` row is deleted **only** when a non-deleted ``read`` row
exists on the same ``(service_principal_id, resource_type, project_id)``. A
``list`` row without such a sibling is the account's only grant for that
resource, so it is left untouched and the count is logged at WARNING level for
a human to reconcile (widen it to ``read`` or drop it deliberately) — this
migration does not guess. On the self-host instance that leftover count was 0.

The sibling match is deliberately exact on ``read``. An ``action='all'``
wildcard row would also satisfy a ``list`` request (see reason 1) and so would
be an equally sound cover, but no live row has that shape — 28 out of 28 have a
``read`` sibling — and keeping the rule this narrow makes the equivalence
argument auditable at a glance. A ``list`` row left behind because only an
``all`` sibling exists shows up in the WARNING above instead of being deleted
silently.

The deletion is a hard ``DELETE`` on purpose. Migrations receive the
*historical* model, whose ``objects`` manager is a plain ``Manager`` (Django
only carries custom managers into migration state when
``use_in_migrations = True``), so there is no soft-delete filtering on read and
no soft-delete override on ``delete()``; the reads below therefore filter
``deleted_at__isnull=True`` explicitly. Tombstoning the rows would also risk
colliding with ``ServiceScope``'s ``unique_together`` (which includes
``deleted_at``) when two identical ``list`` rows are retired in one batch.

Reverse is an intentional no-op (``migrations.RunPython.noop``): the removed
rows granted nothing their ``read`` siblings do not already grant, and
re-creating them would re-introduce exactly the 400 this migration fixes.
"""

import logging

from django.db import migrations

logger = logging.getLogger(__name__)

LEGACY_LIST_ACTION = "list"
READ_ACTION = "read"


def retire_unreachable_list_scope_rows(apps, schema_editor):
    """Delete active ``action='list'`` scope rows that have a ``read`` sibling.

    See the module docstring for the equivalence argument and the guardrail.
    """
    ServiceScope = apps.get_model("service_principals", "ServiceScope")
    db_alias = schema_editor.connection.alias
    scopes = ServiceScope.objects.using(db_alias)

    list_rows = list(
        scopes.filter(action=LEGACY_LIST_ACTION, deleted_at__isnull=True).values_list(
            "id", "service_principal_id", "resource_type", "project_id"
        )
    )
    if not list_rows:
        return

    # Keys covered by a live ``read`` row. ``project_id`` is NULL for
    # workspace-wide scopes, and tuple equality handles that correctly —
    # which matters here, because every ``list`` row seen in practice is
    # workspace-wide.
    read_keys = set(
        scopes.filter(action=READ_ACTION, deleted_at__isnull=True).values_list(
            "service_principal_id", "resource_type", "project_id"
        )
    )

    retirable_ids = [
        row_id
        for row_id, sp_id, resource_type, project_id in list_rows
        if (sp_id, resource_type, project_id) in read_keys
    ]
    if retirable_ids:
        scopes.filter(id__in=retirable_ids).delete()

    orphan_count = len(list_rows) - len(retirable_ids)
    if orphan_count:
        logger.warning(
            "service_principals: kept %s of %s active action='list' scope "
            "row(s) — no 'read' sibling on the same (service_principal, "
            "resource_type, project). Widen each to 'read' or drop it "
            "deliberately by hand.",
            orphan_count,
            len(list_rows),
        )


class Migration(migrations.Migration):

    dependencies = [
        ("service_principals", "0002_alter_servicescope_action_and_more"),
    ]

    operations = [
        migrations.RunPython(
            retire_unreachable_list_scope_rows,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
