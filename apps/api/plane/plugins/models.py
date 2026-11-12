# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Plugin registry models (design §4).

``Plugin`` is the registry row (workspace-scoped, one per key), ``PluginAuditEvent``
is the append-only trail of every state change, and ``PluginConfig`` holds a
validated plugin config. There is deliberately no per-feature boolean
anywhere: to turn a capability off you disable or uninstall the plugin.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from plane.db.models import BaseModel
from plane.db.mixins import TimeAuditModel

from .audit import (
    ActorKind,
    AppendOnlyManager,
    AuditAppendOnlyError,
)
from .constants import (
    ACTOR_KIND_CHOICES,
    AUDIT_ACTION_CHOICES,
    KIND_CHOICES,
    PluginState,
    STATE_CHOICES,
)
from .contract import KERNEL_CONTRACT_HASH
from .manifest import validate_config


class Plugin(BaseModel):
    """A plugin installed into one workspace.

    ``key`` is unique per workspace and deliberately **not** recycled: the
    uniqueness constraint spans soft-deleted rows too, so a removed key stays
    reserved for the retention period and cannot be re-registered under a
    new owner (design §6, error code ``plugin_key_taken``).
    """

    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="plugins",
    )
    key = models.CharField(max_length=32)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    version = models.CharField(max_length=64)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    #: Registry state only. ``deleted`` is not a value here — removal is
    #: ``deleted_at`` on a soft-deleted row, and the audit records it.
    state = models.CharField(
        max_length=20, choices=STATE_CHOICES, default=PluginState.INSTALLED
    )
    #: The manifest exactly as validated at install time — read-only audit
    #: evidence, including the ``runtime`` each contribution declared.
    manifest = models.JSONField(default=dict)
    manifest_hash = models.CharField(max_length=64)
    #: Hash of the kernel contract at install time. Compared against the
    #: current ``KERNEL_CONTRACT_HASH`` to detect drift (design §5).
    contract_hash = models.CharField(max_length=64)
    installed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="installed_plugins",
    )
    installed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Plugin"
        verbose_name_plural = "Plugins"
        db_table = "plugins"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "key"],
                name="plugin_unique_key_per_workspace",
            )
        ]

    def __str__(self):
        return f"{self.key}@{self.version} ({self.state})"

    @property
    def contract_drifted(self) -> bool:
        """True when the stored contract hash no longer matches the kernel.

        A changed kernel contract (any part of ``KERNEL_CONTRACT_DESCRIPTOR``)
        changes ``KERNEL_CONTRACT_HASH``, which shows up here on every
        installed plugin. P1 only detects and records: enabling a drifted
        plugin is refused with ``contract_mismatch`` (design §5's
        ``contract_rematch`` sweep is a later mission).
        """
        return self.contract_hash != KERNEL_CONTRACT_HASH


class PluginAuditEvent(models.Model):
    """One append-only row per plugin state transition (design §4.2).

    Identity, ordering and immutability:

    * ``id`` is a monotonic big integer, so the trail sorts without a
      timestamp tie-break (a UUID primary key could not guarantee that).
    * ``workspace_id`` is a plain UUID column, **not** a foreign key: an
      audit row must outlive the workspace row it names. A FK would either
      cascade the audit away with the workspace or block the retention
      task's hard delete — both destroy evidence.
    * ``actor_id`` is stored by value for the same reason (no FK to User).
    * There is no ``deleted_at`` / ``all_objects`` pair, no ``created_by`` /
      ``updated_by`` bookkeeping and no state that a caller may change:
      the only legal operation is INSERT (guarded by
      :class:`AppendOnlyQuerySet` and the trigger in ``append_only.py``).
    """

    id = models.BigAutoField(primary_key=True)
    workspace_id = models.UUIDField(db_index=True)
    plugin_key = models.CharField(max_length=32, db_index=True)
    plugin_version = models.CharField(max_length=64, blank=True, default="")
    action = models.CharField(max_length=20, choices=AUDIT_ACTION_CHOICES)
    from_state = models.CharField(max_length=20, blank=True, default="")
    to_state = models.CharField(max_length=20, blank=True, default="")
    actor_id = models.UUIDField(null=True, blank=True)
    actor_kind = models.CharField(
        max_length=10, choices=ACTOR_KIND_CHOICES, default=ActorKind.SYSTEM
    )
    at = models.DateTimeField(default=timezone.now, db_index=True)
    manifest_hash = models.CharField(max_length=64, blank=True, default="")
    contract_hash = models.CharField(max_length=64, blank=True, default="")
    reason = models.TextField(blank=True, default="")
    request_id = models.CharField(max_length=64, blank=True, default="")

    objects = AppendOnlyManager()

    class Meta:
        verbose_name = "Plugin Audit Event"
        verbose_name_plural = "Plugin Audit Events"
        db_table = "plugin_audit_events"
        ordering = ("id",)

    def __str__(self):
        return f"#{self.id} {self.action} {self.plugin_key} {self.from_state}->{self.to_state}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AuditAppendOnlyError(
                "plugin audit events are append-only: an existing row cannot be saved again"
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AuditAppendOnlyError(
            "plugin audit events are append-only: delete() is refused"
        )


class PluginConfig(TimeAuditModel):
    """A plugin's config, validated against the manifest's ``config_schema``.

    ``plugin_key`` is intentionally not a FK to ``Plugin``: the config belongs
    to (workspace, key), survives a disable (data is always kept) and is only
    validated against a *live* plugin row at write time.
    """

    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="plugin_configs",
    )
    plugin_key = models.CharField(max_length=32)
    config = models.JSONField(default=dict)

    class Meta:
        verbose_name = "Plugin Config"
        verbose_name_plural = "Plugin Configs"
        db_table = "plugin_configs"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "plugin_key"],
                name="plugin_config_unique_per_workspace",
            )
        ]

    def __str__(self):
        return f"PluginConfig({self.plugin_key})"

    def manifest_config_schema(self) -> dict:
        plugin = Plugin.objects.filter(
            workspace_id=self.workspace_id, key=self.plugin_key
        ).first()
        if plugin is None:
            raise ValidationError(
                f"plugin '{self.plugin_key}' is not installed in this workspace"
            )
        return plugin.manifest.get("config_schema") or {"type": "object", "properties": {}}

    def save(self, *args, **kwargs):
        validate_config(self.config, self.manifest_config_schema())
        super().save(*args, **kwargs)
