# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Constants for the plugin registry (design v0: registry + audit + lifecycle).

The plugin system is one audit-bearing plane, not one switch per feature:
a plugin is installed/enabled/disabled/removed as a whole, and every state
change writes one audit row. Nothing here carries a per-feature boolean.
"""

#: Manifest schema version the kernel accepts. A manifest whose
#: ``manifest_version`` is not this value is ``manifest_invalid``.
MANIFEST_SCHEMA_VERSION = "0.1"

#: Kernel contract version. A manifest's ``kernel_contract`` is a version
#: range that has to contain this value (see ``contract.py``).
KERNEL_CONTRACT_VERSION = "0.1"


class PluginKind:
    #: Shipped with the repo, reviewed together with the kernel.
    BUILTIN = "builtin"
    #: Third-party code. v0 has no runtime, so installing one is refused
    #: with ``runtime_unavailable`` (it also covers a package's client code).
    PACKAGE = "package"


KIND_CHOICES = (
    (PluginKind.BUILTIN, "Built-in"),
    (PluginKind.PACKAGE, "Package"),
)


class PluginState:
    #: Registered only — nothing is mounted (install != active).
    INSTALLED = "installed"
    #: Contributions are mounted and callable.
    ENABLED = "enabled"
    #: Contributions unmounted first; all data kept.
    DISABLED = "disabled"


STATE_CHOICES = (
    (PluginState.INSTALLED, "Installed"),
    (PluginState.ENABLED, "Enabled"),
    (PluginState.DISABLED, "Disabled"),
)

#: Terminal state of a soft-deleted plugin. Deliberately NOT a value of
#: ``Plugin.state`` — the row keeps the last real state and ``deleted_at``
#: marks the removal. It only ever appears in an audit row's ``to_state``.
DELETED_STATE = "deleted"


class AuditAction:
    INSTALL = "install"
    ENABLE = "enable"
    DISABLE = "disable"
    UNINSTALL = "uninstall"
    UPGRADE = "upgrade"
    IMPORT = "import"
    CONTRACT_REMATCH = "contract_rematch"


AUDIT_ACTION_CHOICES = (
    (AuditAction.INSTALL, "Install"),
    (AuditAction.ENABLE, "Enable"),
    (AuditAction.DISABLE, "Disable"),
    (AuditAction.UNINSTALL, "Uninstall"),
    (AuditAction.UPGRADE, "Upgrade"),
    (AuditAction.IMPORT, "Import"),
    (AuditAction.CONTRACT_REMATCH, "Contract rematch"),
)


class ActorKind:
    HUMAN = "human"
    SERVICE = "service"
    #: Written by the kernel itself (e.g. a contract rematch sweep).
    SYSTEM = "system"


ACTOR_KIND_CHOICES = (
    (ActorKind.HUMAN, "Human"),
    (ActorKind.SERVICE, "Service"),
    (ActorKind.SYSTEM, "System"),
)

#: Contribution kinds the v0 contract defines (design §7). Client-runtime
#: contributions use the same kinds with ``runtime: "client"`` (§7.3) —
#: there is no separate client tree.
CONTRIBUTION_KINDS = ("settings_pages", "export_renderers")

#: Every contribution declares where it runs. The kernel only validates and
#: stores this in v0; it executes neither side (no third-party code runs).
CONTRIBUTION_RUNTIMES = ("server", "client")

#: Scope strings the workspace grants a plugin by default. A manifest scope
#: outside this set is refused with ``scope_not_granted``.
#:
#: v0 placeholder: there is no per-workspace scope-grant table yet (the
#: token/scope machinery of PLANE-82 lands later). Until then the kernel's
#: default grant set *is* the workspace grant, and it is part of the
#: kernel contract hash — widening it is a contract change.
WORKSPACE_GRANTED_SCOPES = frozenset(
    {
        "projects.test_cases:read",
        "pages.content:read",
        "workspaces.files:read",
        "workspaces.files:write",
    }
)


class ErrorCode:
    """Management-surface error codes (design §10)."""

    NOT_FOUND = "plugin_not_found"
    ALREADY_INSTALLED = "plugin_already_installed"
    KEY_TAKEN = "plugin_key_taken"
    MANIFEST_INVALID = "manifest_invalid"
    CONTRACT_MISMATCH = "contract_mismatch"
    SCOPE_NOT_GRANTED = "scope_not_granted"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    PLUGIN_DISABLED = "plugin_disabled"
    #: Not in design §10: an lifecycle call that the state machine forbids
    #: (enable an enabled plugin, disable a disabled one).
    INVALID_STATE = "invalid_state"
    #: Not in design §10: uninstall without ``confirm`` equal to the key.
    CONFIRMATION_REQUIRED = "confirmation_required"
    #: Not in design §10: uninstall without a non-empty reason.
    REASON_REQUIRED = "reason_required"


ERROR_CODES = {
    name: getattr(ErrorCode, name)
    for name in dir(ErrorCode)
    if not name.startswith("_") and isinstance(getattr(ErrorCode, name), str)
}

#: HTTP status per error code. Everything that is a client mistake is 400;
#: conflicts (the key already exists in some form) are 409.
ERROR_HTTP_STATUS = {
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.ALREADY_INSTALLED: 409,
    ErrorCode.KEY_TAKEN: 409,
    ErrorCode.INVALID_STATE: 409,
    ErrorCode.PLUGIN_DISABLED: 409,
    ErrorCode.MANIFEST_INVALID: 400,
    ErrorCode.CONTRACT_MISMATCH: 400,
    ErrorCode.SCOPE_NOT_GRANTED: 400,
    ErrorCode.RUNTIME_UNAVAILABLE: 400,
    ErrorCode.CONFIRMATION_REQUIRED: 400,
    ErrorCode.REASON_REQUIRED: 400,
}
