# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Append-only audit plumbing for ``PluginAuditEvent`` (design §4.2).

Two independent layers enforce "no UPDATE, no DELETE" on the audit trail:

1. **Model layer** (this module): every write path the ORM offers refuses
   an in-place change — ``QuerySet.update``/``QuerySet.delete`` raise, and an
   instance ``save`` of an already-persisted row (plus its ``delete``) raises
   :class:`AuditAppendOnlyError`.
2. **Database layer** (``append_only.py`` + migration 0001): a Postgres
   ``BEFORE UPDATE OR DELETE`` trigger raises ``insufficient_privilege`` for
   the row, so a direct SQL statement (or a future code path that bypasses
   the ORM) cannot rewrite history either.

Insertion is the only allowed operation; the only supported way to add a
row is :func:`record_audit_event`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from django.db import models
from django.utils import timezone

from .constants import ActorKind


class AuditAppendOnlyError(Exception):
    """Raised when something tries to modify or remove an audit row."""


class AppendOnlyQuerySet(models.QuerySet):
    """Refuses the two bulk mutation paths; reads are unaffected."""

    def update(self, **kwargs):
        raise AuditAppendOnlyError(
            "plugin audit events are append-only: QuerySet.update() is refused"
        )

    def delete(self, *args, **kwargs):
        raise AuditAppendOnlyError(
            "plugin audit events are append-only: QuerySet.delete() is refused"
        )


class AppendOnlyManager(models.Manager.from_queryset(AppendOnlyQuerySet)):
    """Default manager for the audit model."""


@dataclass(frozen=True)
class Actor:
    """The principal a state change is attributed to."""

    actor_id: uuid.UUID | None
    actor_kind: str = ActorKind.SYSTEM


def actor_from_request(request) -> Actor:
    """Resolve the acting principal of a management call.

    Service-principal requests are marked on the proxy user by
    ``APIKeyAuthentication``; they are refused by the view layer today (design
    §9: plugins are a human-admin surface), but the attribution stays correct
    if that default is ever opened up.
    """
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return Actor(None, ActorKind.SYSTEM)
    if getattr(user, "_is_service_principal_proxy", False):
        return Actor(getattr(user, "service_principal_id", None), ActorKind.SERVICE)
    return Actor(getattr(user, "id", None), ActorKind.HUMAN)


def record_audit_event(
    *,
    workspace_id,
    plugin_key: str,
    action: str,
    from_state: str = "",
    to_state: str = "",
    actor: Actor | None = None,
    plugin_version: str = "",
    manifest_hash: str = "",
    contract_hash: str = "",
    reason: str = "",
    request_id: str = "",
    at=None,
):
    """Append exactly one audit row for exactly one state transition.

    The import is local so ``models`` can import this module for the
    manager/guards without a cycle at module load time.
    """
    from .models import PluginAuditEvent

    actor = actor or Actor(None, ActorKind.SYSTEM)
    return PluginAuditEvent.objects.create(
        workspace_id=workspace_id,
        plugin_key=plugin_key,
        plugin_version=plugin_version,
        action=action,
        from_state=from_state,
        to_state=to_state,
        actor_id=actor.actor_id,
        actor_kind=actor.actor_kind,
        at=at or timezone.now(),
        manifest_hash=manifest_hash,
        contract_hash=contract_hash,
        reason=reason,
        request_id=request_id,
    )
