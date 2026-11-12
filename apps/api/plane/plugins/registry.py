# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Plugin lifecycle service (design §5).

Every state transition lives here so that the "one transition = one audit
row" invariant is enforced in a single place, independent of the endpoint
that triggered it:

    install / import   (none)      -> installed
    enable             installed   -> enabled
    disable            enabled     -> disabled
    uninstall          enabled     -> disabled -> deleted (soft delete)
                       installed/disabled -> deleted
"""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone

from .audit import Actor, record_audit_event
from .constants import (
    AuditAction,
    ActorKind,
    DELETED_STATE,
    ErrorCode,
    PluginState,
)
from .contract import KERNEL_CONTRACT_HASH, hash_manifest
from .errors import PluginRegistryError, not_found
from .manifest import validate_manifest
from .models import Plugin


def get_plugin(workspace, key: str) -> Plugin:
    """The live (not soft-deleted) plugin row for ``workspace`` and ``key``."""
    plugin = Plugin.objects.filter(workspace=workspace, key=key).first()
    if plugin is None:
        raise not_found(key)
    return plugin


def mount_contributions(plugin: Plugin) -> None:
    """Mount hook — the single point where P2 attaches contributions.

    v0 mounts nothing: the contribution registry does not exist yet, so
    enabling a plugin records the state and nothing is callable. P2 fills
    this in for the two kinds the contract defines (``settings_pages`` and
    ``export_renderers``), keyed by each contribution ``id``. Contributions
    declaring ``runtime: "client"`` are still only declarations in v0 — the
    kernel loads no browser-side third-party code either (design §7.3).
    """


def unmount_contributions(plugin: Plugin) -> None:
    """Unmount hook — must run *before* the state flips to ``disabled``.

    Design §5: disabling removes every contribution first, then changes the
    state; data (manifest, config, audit) is never dropped by a disable.
    v0 has nothing mounted, so this is the documented hook P2 fills in.
    """


def install_manifest(
    *,
    workspace,
    manifest,
    actor: Actor,
    action: str = AuditAction.INSTALL,
    request_id: str = "",
) -> Plugin:
    """Validate a manifest and register it in ``installed`` state.

    ``action`` distinguishes a plain install from an ``import`` of a manifest
    sourced outside the kernel (same rules, different audit action, design
    §4.2).
    """
    normalized = validate_manifest(manifest)
    key = normalized["key"]

    existing = Plugin.all_objects.filter(workspace=workspace, key=key).first()
    if existing is not None:
        if existing.deleted_at is None:
            raise PluginRegistryError(
                ErrorCode.ALREADY_INSTALLED,
                f"plugin '{key}' is already installed in this workspace",
            )
        raise PluginRegistryError(
            ErrorCode.KEY_TAKEN,
            f"plugin key '{key}' is still reserved by a removed plugin",
        )

    manifest_hash_value = hash_manifest(normalized)
    with transaction.atomic():
        try:
            # The savepoint turns a racing duplicate insert into a registry
            # refusal instead of poisoning the surrounding transaction.
            with transaction.atomic():
                plugin = Plugin.objects.create(
                    workspace=workspace,
                    key=key,
                    name=normalized["name"],
                    description=normalized.get("description", ""),
                    version=normalized["version"],
                    kind=normalized["kind"],
                    state=PluginState.INSTALLED,
                    manifest=normalized,
                    manifest_hash=manifest_hash_value,
                    contract_hash=KERNEL_CONTRACT_HASH,
                    installed_by_id=(
                        actor.actor_id if actor.actor_kind == ActorKind.HUMAN else None
                    ),
                    installed_at=timezone.now(),
                )
        except IntegrityError as exc:
            raise PluginRegistryError(
                ErrorCode.ALREADY_INSTALLED,
                f"plugin '{key}' is already installed in this workspace",
            ) from exc

        record_audit_event(
            workspace_id=workspace.id,
            plugin_key=key,
            plugin_version=plugin.version,
            action=action,
            from_state="",
            to_state=PluginState.INSTALLED,
            actor=actor,
            manifest_hash=plugin.manifest_hash,
            contract_hash=plugin.contract_hash,
            request_id=request_id,
        )
    return plugin


def enable_plugin(plugin: Plugin, *, actor: Actor, request_id: str = "") -> Plugin:
    """installed / disabled -> enabled, mounting contributions."""
    if plugin.deleted_at is not None:
        raise not_found(plugin.key)
    if plugin.state == PluginState.ENABLED:
        raise PluginRegistryError(ErrorCode.INVALID_STATE, "plugin is already enabled")
    if plugin.state not in (PluginState.INSTALLED, PluginState.DISABLED):
        raise PluginRegistryError(
            ErrorCode.INVALID_STATE, f"cannot enable a plugin in state '{plugin.state}'"
        )
    if plugin.contract_drifted:
        # P1 detects and refuses; design §5's `contract_rematch` sweep is a
        # later mission, so today a drifted plugin stays exactly as it is and
        # an admin has to re-install/re-confirm it.
        raise PluginRegistryError(
            ErrorCode.CONTRACT_MISMATCH,
            "the stored contract hash no longer matches the current kernel contract; "
            "re-install the plugin before enabling it",
        )

    from_state = plugin.state
    with transaction.atomic():
        mount_contributions(plugin)
        plugin.state = PluginState.ENABLED
        plugin.save()
        record_audit_event(
            workspace_id=plugin.workspace_id,
            plugin_key=plugin.key,
            plugin_version=plugin.version,
            action=AuditAction.ENABLE,
            from_state=from_state,
            to_state=PluginState.ENABLED,
            actor=actor,
            manifest_hash=plugin.manifest_hash,
            contract_hash=plugin.contract_hash,
            request_id=request_id,
        )
    return plugin


def disable_plugin(plugin: Plugin, *, actor: Actor, request_id: str = "") -> Plugin:
    """enabled -> disabled, unmounting contributions first. Data is kept."""
    if plugin.deleted_at is not None:
        raise not_found(plugin.key)
    if plugin.state != PluginState.ENABLED:
        raise PluginRegistryError(
            ErrorCode.INVALID_STATE, f"cannot disable a plugin in state '{plugin.state}'"
        )

    with transaction.atomic():
        unmount_contributions(plugin)
        plugin.state = PluginState.DISABLED
        plugin.save()
        record_audit_event(
            workspace_id=plugin.workspace_id,
            plugin_key=plugin.key,
            plugin_version=plugin.version,
            action=AuditAction.DISABLE,
            from_state=PluginState.ENABLED,
            to_state=PluginState.DISABLED,
            actor=actor,
            manifest_hash=plugin.manifest_hash,
            contract_hash=plugin.contract_hash,
            request_id=request_id,
        )
    return plugin


def uninstall_plugin(
    plugin: Plugin,
    *,
    confirm: str,
    reason: str,
    actor: Actor,
    request_id: str = "",
) -> Plugin:
    """Soft delete, gated on ``confirm == key`` and a non-empty reason.

    An enabled plugin is disabled first (unmounting contributions); that is
    its own state transition and therefore gets its own audit row, so
    uninstalling an enabled plugin writes ``disable`` then ``uninstall`` in
    order. Every other starting state writes just the ``uninstall`` row.
    """
    if plugin.deleted_at is not None:
        raise not_found(plugin.key)
    if confirm != plugin.key:
        raise PluginRegistryError(
            ErrorCode.CONFIRMATION_REQUIRED,
            f"uninstall requires 'confirm' to repeat the plugin key '{plugin.key}'",
        )
    reason = (reason or "").strip()
    if not reason:
        raise PluginRegistryError(
            ErrorCode.REASON_REQUIRED, "uninstall requires a non-empty reason"
        )

    with transaction.atomic():
        from_state = plugin.state
        if plugin.state == PluginState.ENABLED:
            disable_plugin(plugin, actor=actor, request_id=request_id)
            from_state = PluginState.DISABLED
        plugin.deleted_at = timezone.now()
        plugin.save()
        record_audit_event(
            workspace_id=plugin.workspace_id,
            plugin_key=plugin.key,
            plugin_version=plugin.version,
            action=AuditAction.UNINSTALL,
            from_state=from_state,
            to_state=DELETED_STATE,
            actor=actor,
            manifest_hash=plugin.manifest_hash,
            contract_hash=plugin.contract_hash,
            reason=reason,
            request_id=request_id,
        )
    return plugin
