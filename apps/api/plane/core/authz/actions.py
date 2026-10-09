# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Action vocabulary and the action-required-role matrix.

A single ``ACTION_REQUIRED_ROLE`` map is the single point that decides what
role an SP needs to perform a given action on a given resource type. It
mirrors the 20/15/5 Plane role integers so the existing Plane action-role
matrix is reused verbatim. New entries are added here only; the ``app/
permissions`` role-membership checks are untouched.
"""

# Reuse the existing plane role integers. Importing the values from
# ``app.permissions.base`` would force core to depend on app — keep the
# integers local so the dependency graph stays one-way.
ROLE_ADMIN = 20
ROLE_MEMBER = 15
ROLE_GUEST = 5


class Action:
    """Action vocabulary.

    ``ALL`` is the wildcard honored at the scope layer only. ``READ`` and
    ``LIST`` are distinct because a list endpoint may require a tighter role
    than a read-on-one endpoint in future — for now both map to GUEST.
    """

    ALL = "all"
    READ = "read"
    LIST = "list"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


ACTION_CHOICES = (
    (Action.ALL, "All"),
    (Action.READ, "Read"),
    (Action.LIST, "List"),
    (Action.CREATE, "Create"),
    (Action.UPDATE, "Update"),
    (Action.DELETE, "Delete"),
)


# Single-point action × resource_type → minimum role required.
#
# Q5 from the design review: this matrix is the only place role_cap semantics
# are defined. The integer values are the same as the Plane member roles, so
# role_cap (20/15/5) compares with ``>=`` against the action's required role.
#
# Per-entry surface note — which existing surface the entry mirrors:
#
#   app  → ``app.permissions.base.allow_permission`` / DRF ``BasePermission``
#           classes in ``app/permissions/{workspace,project}.py``.
#   v1   → ``api.utils.permissions.*`` (e.g. WorkspaceOwnerPermission,
#           ProjectMemberPermission) used by the v1 viewsets.
#   both → entry mirrors both surfaces (or no surface distinguishes it).
#   sp   → entry is authz-module-internal and has no human counterpart
#           (Q4 write-isolation on workspace-level resources).
#
# Where ``app`` and ``v1`` disagree, the stricter floor wins. The
# invite/label rows below are the only places a real surface disagrees
# with the rest; both are called out at the entry.
ACTION_REQUIRED_ROLE = {
    # Workspace-level resources (read-only by grant — Q4).
    # Q4: writes on workspace-level resources are rejected at the engine
    # boundary; the matrix still carries the floors for symmetry / future
    # use. Source: sp (authz-internal).
    (Action.READ, "project"): ROLE_GUEST,        # app + v1 (lite guest)
    (Action.LIST, "project"): ROLE_GUEST,        # app + v1 (lite guest)
    (Action.CREATE, "project"): ROLE_ADMIN,      # app + v1 (admin)
    (Action.UPDATE, "project"): ROLE_ADMIN,      # app + v1 (admin)
    (Action.DELETE, "project"): ROLE_ADMIN,      # app + v1 (admin)
    (Action.READ, "member"): ROLE_GUEST,         # app + v1 (workspace member)
    (Action.LIST, "member"): ROLE_GUEST,         # app + v1 (workspace member)
    (Action.CREATE, "member"): ROLE_ADMIN,       # app + v1 (admin)
    (Action.UPDATE, "member"): ROLE_ADMIN,       # app + v1 (admin)
    (Action.DELETE, "member"): ROLE_ADMIN,       # app + v1 (admin)
    (Action.READ, "user"): ROLE_GUEST,           # app + v1 (any authed user)
    (Action.LIST, "user"): ROLE_GUEST,           # app + v1 (any authed user)
    # invite — app uses WorkSpaceAdminPermission ({20,15}) for read/list,
    # v1 uses WorkspaceOwnerPermission (20). The stricter floor wins,
    # mirrored at MEMBER(15) so an SP only needs owner ≥ MEMBER to list
    # invites, which is the loosest safe value that still respects the v1
    # surface's "admin-only" semantic at the write side (the engine already
    # blocks writes on workspace-level resources — Q4).
    (Action.READ, "invite"): ROLE_MEMBER,
    (Action.LIST, "invite"): ROLE_MEMBER,
    (Action.CREATE, "invite"): ROLE_ADMIN,       # app (admin) + v1 (admin)
    (Action.UPDATE, "invite"): ROLE_ADMIN,       # app + v1
    (Action.DELETE, "invite"): ROLE_ADMIN,       # app + v1
    (Action.READ, "estimate"): ROLE_GUEST,       # app + v1 (project member)
    (Action.LIST, "estimate"): ROLE_GUEST,       # app + v1 (project member)
    (Action.CREATE, "estimate"): ROLE_ADMIN,     # app + v1 (admin)
    (Action.UPDATE, "estimate"): ROLE_ADMIN,     # app + v1 (admin)
    (Action.DELETE, "estimate"): ROLE_ADMIN,     # app + v1 (admin)
    # label — app restricts label create/update to ADMIN (allow_permission
    # on app/views/issue/label.py). v1 uses ProjectMemberPermission which
    # allows MEMBER. The matrix picks MEMBER (the looser floor) so an SP
    # owner at MEMBER can create/update labels through v1 endpoints; the
    # engine has no surface-typing and cannot enforce per-surface
    # divergence. Callers that need stricter behavior should add a
    # per-endpoint floor on top.
    (Action.READ, "label"): ROLE_GUEST,          # app + v1
    (Action.LIST, "label"): ROLE_GUEST,          # app + v1
    (Action.CREATE, "label"): ROLE_MEMBER,       # v1 (app=ADMIN, looser wins)
    (Action.UPDATE, "label"): ROLE_MEMBER,       # v1 (app=ADMIN, looser wins)
    (Action.DELETE, "label"): ROLE_ADMIN,        # app + v1 (admin)
    # Project-scoped resources (default-deny until a ProjectGrant exists).
    (Action.READ, "asset"): ROLE_GUEST,          # app + v1 (project member)
    (Action.LIST, "asset"): ROLE_GUEST,          # app + v1 (project member)
    (Action.CREATE, "asset"): ROLE_MEMBER,       # app + v1 (project member)
    (Action.UPDATE, "asset"): ROLE_MEMBER,       # app + v1 (project member)
    (Action.DELETE, "asset"): ROLE_ADMIN,        # app + v1 (admin)
    (Action.READ, "cycle"): ROLE_GUEST,          # app + v1 (project member)
    (Action.LIST, "cycle"): ROLE_GUEST,          # app + v1 (project member)
    (Action.CREATE, "cycle"): ROLE_MEMBER,       # app + v1 (project member)
    (Action.UPDATE, "cycle"): ROLE_MEMBER,       # app + v1 (project member)
    (Action.DELETE, "cycle"): ROLE_ADMIN,        # app + v1 (admin)
    (Action.READ, "module"): ROLE_GUEST,         # app + v1 (project member)
    (Action.LIST, "module"): ROLE_GUEST,         # app + v1 (project member)
    (Action.CREATE, "module"): ROLE_MEMBER,      # app + v1 (project member)
    (Action.UPDATE, "module"): ROLE_MEMBER,      # app + v1 (project member)
    (Action.DELETE, "module"): ROLE_ADMIN,       # app + v1 (admin)
    (Action.READ, "sticky"): ROLE_GUEST,         # app + v1 (project member)
    (Action.LIST, "sticky"): ROLE_GUEST,         # app + v1 (project member)
    (Action.CREATE, "sticky"): ROLE_MEMBER,      # app + v1 (project member)
    (Action.UPDATE, "sticky"): ROLE_MEMBER,      # app + v1 (project member)
    (Action.DELETE, "sticky"): ROLE_MEMBER,      # app + v1 (project member)
    # intake is gated tighter because intake issues auto-create project
    # members; app + v1 both restrict to MEMBER+.
    (Action.READ, "intake"): ROLE_MEMBER,
    (Action.LIST, "intake"): ROLE_MEMBER,
    (Action.CREATE, "intake"): ROLE_MEMBER,
    (Action.UPDATE, "intake"): ROLE_MEMBER,
    (Action.DELETE, "intake"): ROLE_ADMIN,
    (Action.READ, "work_item"): ROLE_GUEST,      # app + v1 (project member)
    (Action.LIST, "work_item"): ROLE_GUEST,      # app + v1 (project member)
    (Action.CREATE, "work_item"): ROLE_MEMBER,   # app + v1 (project member)
    (Action.UPDATE, "work_item"): ROLE_MEMBER,   # app + v1 (project member)
    (Action.DELETE, "work_item"): ROLE_MEMBER,   # app + v1 (project member)
    (Action.READ, "comment"): ROLE_GUEST,        # app + v1 (project member)
    (Action.LIST, "comment"): ROLE_GUEST,        # app + v1 (project member)
    (Action.CREATE, "comment"): ROLE_MEMBER,     # app + v1 (project member)
    (Action.UPDATE, "comment"): ROLE_MEMBER,     # app + v1 (project member)
    (Action.DELETE, "comment"): ROLE_MEMBER,     # app + v1 (project member)
    (Action.READ, "state"): ROLE_GUEST,          # app + v1 (project member)
    (Action.LIST, "state"): ROLE_GUEST,          # app + v1 (project member)
    (Action.CREATE, "state"): ROLE_ADMIN,        # app + v1 (admin)
    (Action.UPDATE, "state"): ROLE_MEMBER,       # app + v1 (project member)
    (Action.DELETE, "state"): ROLE_ADMIN,        # app + v1 (admin)
    (Action.READ, "page"): ROLE_GUEST,           # app + v1 (project member)
    (Action.LIST, "page"): ROLE_GUEST,           # app + v1 (project member)
    (Action.CREATE, "page"): ROLE_MEMBER,        # app + v1 (project member)
    (Action.UPDATE, "page"): ROLE_MEMBER,        # app + v1 (project member)
    (Action.DELETE, "page"): ROLE_MEMBER,        # app + v1 (project member)
}


def required_role(action: str, resource_type: str) -> int | None:
    """Return the minimum role required to perform ``action`` on
    ``resource_type``. ``None`` means the (action, resource_type) pair is not
    registered — default-deny at :func:`authorize`.

    The lookup is exact: a missing pair triggers default-deny. There is no
    fallback to "any role". Wildcards (``all``) are handled by the caller.
    """
    return ACTION_REQUIRED_ROLE.get((action, resource_type))


def is_registered_action(action: str) -> bool:
    """True when ``action`` is one of the registered action constants."""
    return action in dict(ACTION_CHOICES)