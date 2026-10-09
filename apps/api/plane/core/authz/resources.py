# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""ResourceType registry.

Every authorization decision names a resource type from this module. The
constant enum is the single source of truth: it replaces the v1
``URL_RESOURCE_MAP`` (URL-name keyed) with a declarative, semantic registry
keyed by the resource itself. New surfaces register by adding a member;
unrecognized values fail closed (default-deny).

Resource types are stable identifiers used both in the DB (``ServiceScope``
``resource_type`` column, ``RESOURCE_CHOICES`` on the model) and in the
in-memory call to :func:`authorize`. Keep the two in sync.
"""


class ResourceType:
    """Resource vocabulary the authorize() chain understands.

    ``ALL`` is a wildcard only honored at the scope layer; project visibility
    is decided separately by ``ProjectGrant``.
    """

    ALL = "all"
    PROJECT = "project"
    MEMBER = "member"
    USER = "user"
    ASSET = "asset"
    ESTIMATE = "estimate"
    CYCLE = "cycle"
    MODULE = "module"
    STICKY = "sticky"
    LABEL = "label"
    INTAKE = "intake"
    WORK_ITEM = "work_item"
    COMMENT = "comment"
    STATE = "state"
    PAGE = "page"
    INVITE = "invite"


# The complete, ordered set. Used by ``RESOURCE_CHOICES`` (Django) and by the
# service_principals migrations so the two never drift.
RESOURCE_CHOICES = (
    (ResourceType.ALL, "All"),
    (ResourceType.PROJECT, "Project"),
    (ResourceType.MEMBER, "Member"),
    (ResourceType.USER, "User"),
    (ResourceType.ASSET, "Asset"),
    (ResourceType.ESTIMATE, "Estimate"),
    (ResourceType.CYCLE, "Cycle"),
    (ResourceType.MODULE, "Module"),
    (ResourceType.STICKY, "Sticky"),
    (ResourceType.LABEL, "Label"),
    (ResourceType.INTAKE, "Intake"),
    (ResourceType.WORK_ITEM, "Work Item"),
    (ResourceType.COMMENT, "Comment"),
    (ResourceType.STATE, "State"),
    (ResourceType.PAGE, "Page"),
    (ResourceType.INVITE, "Invite"),
)


# Resource types that exist only at the workspace level — they have no
# project scope. authorize() routes these through workspace-wide grants and
# rejects writes on them outright (Q4 from the design review).
WORKSPACE_LEVEL_RESOURCES = frozenset(
    {
        ResourceType.PROJECT,
        ResourceType.MEMBER,
        ResourceType.USER,
        ResourceType.INVITE,
        ResourceType.ESTIMATE,
        ResourceType.LABEL,
    }
)


# Action names that are permitted on workspace-level resource types even
# though there is no project grant to attach them to. Writes go through a
# separate gate (see ``ALLOWED_WORKSPACE_LEVEL_ACTIONS`` below).
ACTION_NAMES = ("read", "create", "update", "delete", "list")


# Workspace-level resource types only accept read actions. Writes must land
# on a project-scoped resource via an explicit ProjectGrant. This is the
# Q4 enforcement point for workspace-wide grants.
ALLOWED_WORKSPACE_LEVEL_ACTIONS = frozenset({"read"})


def is_workspace_level(resource_type: str) -> bool:
    """True when ``resource_type`` lives at the workspace scope (no project).

    ``ALL`` is neither workspace- nor project-level; callers must not pass it
    here. The wildcard is resolved by the authorize() chain at the scope
    layer only.
    """
    return resource_type in WORKSPACE_LEVEL_RESOURCES


def is_registered(resource_type: str) -> bool:
    """True when ``resource_type`` matches one of the registered constants.

    Anything else triggers default-deny at :func:`authorize`. ``ALL`` is
    registered (wildcards are honored at the scope layer), so this returns
    True for it.
    """
    return resource_type in dict(RESOURCE_CHOICES)