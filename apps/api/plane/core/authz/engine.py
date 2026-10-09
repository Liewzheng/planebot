# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""``authorize()`` — the four-step SP decision chain.

Implements the chain from ``sp-design.md`` §5.2:

1. **scope hit** — ``(resource_type, action)`` must be in the SP's
   :class:`ServiceScope` set (wildcards honored).
2. **grant present** — for project resources, an active
   :class:`ProjectGrant` row exists. For workspace-level resources, a
   workspace-wide scope row (project=None) is required.
3. **role_cap covers** — the action's required role (per
   :data:`ACTION_REQUIRED_ROLE`) must be ``<=`` the ``ProjectGrant.role_cap``.
4. **owner intersection** — the SP's owner user must currently be an
   active workspace member whose role covers ``role_cap``. This step is
   non-cached: every call re-queries WorkspaceMember so a demotion takes
   effect on the next request.

Any failure short-circuits to a deny :class:`Decision`. The chain is the
single authorization point for every SP request regardless of which surface
(v1, app, live, webhook) is calling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from django.db.models import Q

from plane.db.models import WorkspaceMember

from . import actions, resources
from .decision import (
    ALLOWED,
    DENY_GRANT_MISS,
    DENY_INACTIVE_OWNER,
    DENY_INACTIVE_PRINCIPAL,
    DENY_NO_PRINCIPAL,
    DENY_OWNER_NOT_WORKSPACE_MEMBER,
    DENY_OWNER_ROLE,
    DENY_ROLE_CAP,
    DENY_SCOPE_MISS,
    DENY_UNREGISTERED,
    DENY_WORKSPACE_LEVEL_WRITE,
    Decision,
)
from .principal import Principal, ServicePrincipal_

if TYPE_CHECKING:
    from plane.db.models import Project


@dataclass(frozen=True)
class AuthzContext:
    """Per-request context for :func:`authorize`.

    Attributes:
        workspace_slug: The workspace slug from the URL. Used by the
            owner-intersection step. Optional — when absent, the SP's
            workspace slug is used.
        project_id: The project UUID the request is targeting, if any.
            ``None`` for workspace-level endpoints.
    """

    workspace_slug: Optional[str] = None
    project_id: Optional[str] = None


def authorize(
    principal: Principal | None,
    action: str,
    resource_type: str,
    resource: object | None = None,
    ctx: AuthzContext | None = None,
) -> Decision:
    """Run the four-step decision chain.

    Args:
        principal: Resolved by the authentication layer. ``None`` ⇒ deny.
        action: One of :class:`Action`'s values (or ``"all"`` wildcard).
        resource_type: One of :class:`ResourceType`'s values.
        resource: The actual resource instance, when available. Reserved
            for future per-row decisions; the chain currently only consults
            the resource's type and project. Pass ``None`` for collection
            endpoints.
        ctx: Per-request context (workspace slug, project id).

    Returns:
        A :class:`Decision`. Callers that want to raise can use
        :func:`decision_to_drf_exception` or check ``.allowed`` directly.
    """
    ctx = ctx or AuthzContext()

    # Step 0 — basic shape: principal must be present and the action/resource
    # must be registered. An unregistered pair is default-deny (no need to
    # do further work; even an admin would not reach here on a normal path
    # because the URL is registered, but new paths throw 403 until they are).
    if principal is None:
        return Decision.deny(DENY_NO_PRINCIPAL, "No principal on the request.")

    if not actions.is_registered_action(action):
        return Decision.deny(
            DENY_UNREGISTERED,
            f"Action '{action}' is not registered.",
        )

    if not resources.is_registered(resource_type):
        return Decision.deny(
            DENY_UNREGISTERED,
            f"Resource '{resource_type}' is not registered.",
        )

    # Only service principals flow through this engine. Human principals
    # keep their existing role-based permissions in app/api views — the
    # engine is the SP gate, not a replacement for WorkspaceMember.
    if not isinstance(principal, ServicePrincipal_):
        return Decision.allow(effective_role=None)

    sp = principal.service_principal
    if not sp.is_active:
        return Decision.deny(
            DENY_INACTIVE_PRINCIPAL,
            f"Service principal '{sp.name}' is inactive.",
        )

    # Workspace-level resources can only be read. Writes must land on a
    # project-scoped resource through a ProjectGrant (Q4 from the design
    # review).
    if (
        resources.is_workspace_level(resource_type)
        and action not in resources.ALLOWED_WORKSPACE_LEVEL_ACTIONS
        and action not in (actions.Action.ALL,)
    ):
        return Decision.deny(
            DENY_WORKSPACE_LEVEL_WRITE,
            (
                f"Writes on workspace-level resource '{resource_type}' "
                "are not permitted; route through a project-scoped grant."
            ),
        )

    # Step 1 — scope hit. Project-scoped scope wins over workspace-wide scope.
    from plane.service_principals.models import ServiceScope

    project_id = _resolve_project_id(resource, ctx)

    scope_qs = ServiceScope.objects.filter(
        service_principal=sp,
        resource_type__in=[resource_type, resources.ResourceType.ALL],
        action__in=[action, actions.Action.ALL],
    )
    if project_id:
        scope_present = scope_qs.filter(
            Q(project_id=project_id) | Q(project__isnull=True)
        ).exists()
    else:
        scope_present = scope_qs.filter(project__isnull=True).exists()

    if not scope_present:
        return Decision.deny(
            DENY_SCOPE_MISS,
            (
                f"Service principal '{sp.name}' lacks scope for "
                f"{action} {resource_type}"
                + (f" in project {project_id}." if project_id else ".")
            ),
        )

    # Step 2 — grant present. Project-scoped resources require an active
    # ProjectGrant; workspace-level resources skip this step (the
    # workspace-wide scope row already covers them).
    if project_id and not resources.is_workspace_level(resource_type):
        from plane.service_principals.models import ProjectGrant

        grant = ProjectGrant.objects.filter(
            service_principal=sp,
            project_id=project_id,
            is_active=True,
        ).first()
        if grant is None:
            return Decision.deny(
                DENY_GRANT_MISS,
                (
                    f"Service principal '{sp.name}' has no active grant "
                    f"for project {project_id}."
                ),
            )

        # Step 3 — role_cap covers the action's required role.
        required = actions.required_role(action, resource_type)
        if required is None:
            return Decision.deny(
                DENY_UNREGISTERED,
                (
                    f"No role requirement registered for {action} "
                    f"{resource_type}."
                ),
            )
        if grant.role_cap < required:
            return Decision.deny(
                DENY_ROLE_CAP,
                (
                    f"Grant role_cap ({grant.role_cap}) is below the "
                    f"required role ({required}) for {action} "
                    f"{resource_type}."
                ),
            )

        effective_role = grant.role_cap
    else:
        # Workspace-level resource: scope row is the only gate. The
        # owner-intersection step still applies; effective_role is the
        # owner's workspace role at the time of the call.
        effective_role = None

    # Step 4 — owner intersection. The owner user must be an active
    # workspace member whose role covers the action. The role is fetched
    # fresh on every call (no cache): a demotion takes effect on the next
    # request, which is the design constraint from sp-design.md §5.2.
    workspace_slug = ctx.workspace_slug or getattr(sp.workspace, "slug", None)
    if not workspace_slug:
        return Decision.deny(
            DENY_NO_PRINCIPAL,
            "Workspace slug is missing from the request context.",
        )

    owner_row = (
        WorkspaceMember.objects.filter(
            workspace__slug=workspace_slug,
            member_id=sp.owner_id,
            is_active=True,
        )
        .values_list("role", flat=True)
        .first()
    )
    if owner_row is None:
        return Decision.deny(
            DENY_OWNER_NOT_WORKSPACE_MEMBER,
            (
                f"Service principal owner is not an active member of "
                f"workspace '{workspace_slug}'."
            ),
        )

    if not sp.owner.is_active:
        return Decision.deny(
            DENY_INACTIVE_OWNER,
            "Service principal owner is inactive.",
        )

    required_for_intersection = effective_role if project_id else (
        actions.required_role(action, resource_type) or 0
    )
    if required_for_intersection and owner_row < required_for_intersection:
        return Decision.deny(
            DENY_OWNER_ROLE,
            (
                f"Owner workspace role ({owner_row}) is below required "
                f"({required_for_intersection})."
            ),
        )

    return Decision.allow(effective_role=effective_role or owner_row)


def _resolve_project_id(
    resource: object | None,
    ctx: AuthzContext | None,
) -> Optional[str]:
    """Pick the project id for the current request, in priority order:

    1. The resource is itself a Project (or has a ``workspace_id`` and no
       ``project_id``) — use ``resource.id`` as the project id.
    2. ``resource.project_id`` (per-row instance has the answer).
    3. ``ctx.project_id`` (per-request from URL routing).
    """
    if resource is not None:
        if getattr(resource, "workspace_id", None) and not getattr(
            resource, "project_id", None
        ):
            return str(getattr(resource, "id", None) or "")
        pid = getattr(resource, "project_id", None)
        if pid:
            return str(pid)
    if ctx and ctx.project_id:
        return str(ctx.project_id)
    return None