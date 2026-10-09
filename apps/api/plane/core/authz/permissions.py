# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Effective-permission computation for the front end.

These helpers compute, per-(principal, resource_type, project), which
actions the principal is allowed to perform. The front end consumes the
output of :func:`effective_actions` instead of self-computing permission
flags — keeps the picker / detail / button states consistent with what
the back end will actually do.

For human principals this reduces to the existing role-based check
(workspace role >= required role, or project role >= required role).
For SPs this is a batched :func:`authorize` over the standard action set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

from . import actions, resources
from .decision import Decision
from .engine import AuthzContext, authorize
from .principal import Principal, ServicePrincipal_, UserPrincipal

if TYPE_CHECKING:
    from plane.db.models import Project, Workspace
    from plane.service_principals.models import ServicePrincipal


# The default action set the front end needs to know about. ``ALL`` is
# excluded — it is a wildcard, not a real action.
STANDARD_ACTIONS = (
    actions.Action.READ,
    actions.Action.LIST,
    actions.Action.CREATE,
    actions.Action.UPDATE,
    actions.Action.DELETE,
)


@dataclass(frozen=True)
class ActionPermission:
    """Result of one (action, resource_type) probe."""

    action: str
    resource_type: str
    allowed: bool
    reason: str
    effective_role: int | None = None


def effective_actions(
    principal: Principal | None,
    resource_type: str,
    *,
    project: "Project | None" = None,
    workspace: "Workspace | None" = None,
) -> list[ActionPermission]:
    """Return the standard action set annotated with allow/deny.

    Args:
        principal: The resolved actor. ``None`` ⇒ every action denied.
        resource_type: Resource type to probe.
        project: The target project for project-scoped resources. The
            slug is sourced from ``workspace`` when ``project`` is None.
        workspace: Workspace for slug lookup when ``project`` is None.

    Returns:
        A list of :class:`ActionPermission`, one per action in
        :data:`STANDARD_ACTIONS`.
    """
    workspace_slug = (
        getattr(workspace, "slug", None) if workspace is not None else None
    )
    if workspace_slug is None and project is not None:
        workspace_slug = getattr(getattr(project, "workspace", None), "slug", None)

    project_id = str(project.id) if project is not None else None
    ctx = AuthzContext(workspace_slug=workspace_slug, project_id=project_id)

    results: list[ActionPermission] = []
    for action in STANDARD_ACTIONS:
        decision: Decision = authorize(
            principal,
            action,
            resource_type,
            resource=project,
            ctx=ctx,
        )
        results.append(
            ActionPermission(
                action=action,
                resource_type=resource_type,
                allowed=decision.allowed,
                reason=decision.reason,
                effective_role=decision.effective_role,
            )
        )
    return results


def user_effective_actions(
    principal: UserPrincipal,
    resource_type: str,
    *,
    workspace: "Workspace | None" = None,
    project: "Project | None" = None,
) -> list[ActionPermission]:
    """Convenience wrapper for human principals.

    Returns the same :class:`ActionPermission` list as
    :func:`effective_actions` but skips the SP-only branches of the
    decision chain. Used when the front end asks "what can this human do?"
    without an SP being on the request.
    """
    return effective_actions(
        principal,
        resource_type,
        workspace=workspace,
        project=project,
    )


def sp_effective_actions(
    sp: "ServicePrincipal",
    resource_type: str,
    *,
    project: "Project | None" = None,
    workspace: "Workspace | None" = None,
) -> list[ActionPermission]:
    """Convenience wrapper that constructs a
    :class:`ServicePrincipal_` from a model row and runs
    :func:`effective_actions`.
    """
    principal = ServicePrincipal_(service_principal=sp)
    return effective_actions(
        principal,
        resource_type,
        project=project,
        workspace=workspace,
    )