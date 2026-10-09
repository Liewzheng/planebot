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
# Mirrors the action-role matrix already used by ``app.permissions.base``
# (allow_permission decorator) and ProjectMember checks across the codebase.
# Adding a new resource type? Add a row for each action here; nothing else
# needs to change.
#
# Q5 from the design review: this matrix is the only place role_cap semantics
# are defined. The integer values are the same as the Plane member roles, so
# role_cap (20/15/5) compares with ``>=`` against the action's required role.
ACTION_REQUIRED_ROLE = {
    # Workspace-level resources (read-only by grant — Q4).
    (Action.READ, "project"): ROLE_GUEST,
    (Action.LIST, "project"): ROLE_GUEST,
    (Action.CREATE, "project"): ROLE_ADMIN,
    (Action.UPDATE, "project"): ROLE_ADMIN,
    (Action.DELETE, "project"): ROLE_ADMIN,
    (Action.READ, "member"): ROLE_GUEST,
    (Action.LIST, "member"): ROLE_GUEST,
    (Action.CREATE, "member"): ROLE_ADMIN,
    (Action.UPDATE, "member"): ROLE_ADMIN,
    (Action.DELETE, "member"): ROLE_ADMIN,
    (Action.READ, "user"): ROLE_GUEST,
    (Action.LIST, "user"): ROLE_GUEST,
    (Action.READ, "invite"): ROLE_GUEST,
    (Action.LIST, "invite"): ROLE_GUEST,
    (Action.CREATE, "invite"): ROLE_ADMIN,
    (Action.UPDATE, "invite"): ROLE_ADMIN,
    (Action.DELETE, "invite"): ROLE_ADMIN,
    (Action.READ, "estimate"): ROLE_GUEST,
    (Action.LIST, "estimate"): ROLE_GUEST,
    (Action.CREATE, "estimate"): ROLE_ADMIN,
    (Action.UPDATE, "estimate"): ROLE_ADMIN,
    (Action.DELETE, "estimate"): ROLE_ADMIN,
    (Action.READ, "label"): ROLE_GUEST,
    (Action.LIST, "label"): ROLE_GUEST,
    (Action.CREATE, "label"): ROLE_MEMBER,
    (Action.UPDATE, "label"): ROLE_MEMBER,
    (Action.DELETE, "label"): ROLE_ADMIN,
    # Project-scoped resources (default-deny until a ProjectGrant exists).
    (Action.READ, "asset"): ROLE_GUEST,
    (Action.LIST, "asset"): ROLE_GUEST,
    (Action.CREATE, "asset"): ROLE_MEMBER,
    (Action.UPDATE, "asset"): ROLE_MEMBER,
    (Action.DELETE, "asset"): ROLE_ADMIN,
    (Action.READ, "cycle"): ROLE_GUEST,
    (Action.LIST, "cycle"): ROLE_GUEST,
    (Action.CREATE, "cycle"): ROLE_MEMBER,
    (Action.UPDATE, "cycle"): ROLE_MEMBER,
    (Action.DELETE, "cycle"): ROLE_ADMIN,
    (Action.READ, "module"): ROLE_GUEST,
    (Action.LIST, "module"): ROLE_GUEST,
    (Action.CREATE, "module"): ROLE_MEMBER,
    (Action.UPDATE, "module"): ROLE_MEMBER,
    (Action.DELETE, "module"): ROLE_ADMIN,
    (Action.READ, "sticky"): ROLE_GUEST,
    (Action.LIST, "sticky"): ROLE_GUEST,
    (Action.CREATE, "sticky"): ROLE_MEMBER,
    (Action.UPDATE, "sticky"): ROLE_MEMBER,
    (Action.DELETE, "sticky"): ROLE_MEMBER,
    (Action.READ, "intake"): ROLE_MEMBER,
    (Action.LIST, "intake"): ROLE_MEMBER,
    (Action.CREATE, "intake"): ROLE_MEMBER,
    (Action.UPDATE, "intake"): ROLE_MEMBER,
    (Action.DELETE, "intake"): ROLE_ADMIN,
    (Action.READ, "work_item"): ROLE_GUEST,
    (Action.LIST, "work_item"): ROLE_GUEST,
    (Action.CREATE, "work_item"): ROLE_MEMBER,
    (Action.UPDATE, "work_item"): ROLE_MEMBER,
    (Action.DELETE, "work_item"): ROLE_MEMBER,
    (Action.READ, "comment"): ROLE_GUEST,
    (Action.LIST, "comment"): ROLE_GUEST,
    (Action.CREATE, "comment"): ROLE_MEMBER,
    (Action.UPDATE, "comment"): ROLE_MEMBER,
    (Action.DELETE, "comment"): ROLE_MEMBER,
    (Action.READ, "state"): ROLE_GUEST,
    (Action.LIST, "state"): ROLE_GUEST,
    (Action.CREATE, "state"): ROLE_ADMIN,
    (Action.UPDATE, "state"): ROLE_MEMBER,
    (Action.DELETE, "state"): ROLE_ADMIN,
    (Action.READ, "page"): ROLE_GUEST,
    (Action.LIST, "page"): ROLE_GUEST,
    (Action.CREATE, "page"): ROLE_MEMBER,
    (Action.UPDATE, "page"): ROLE_MEMBER,
    (Action.DELETE, "page"): ROLE_MEMBER,
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