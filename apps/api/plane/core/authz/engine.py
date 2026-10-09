# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""``authorize()`` — the four-step SP decision chain.

Implements the chain from ``sp-design.md`` §5.2:

1. **scope hit** — ``(resource_type, action)`` must be in the SP's
   :class:`ServiceScope` set (wildcards honored). A ``read`` scope row
   also satisfies a ``list`` request (LIST is authz-internal; the SP
   management API exposes ``read`` only — see ``engine._scope_action_set``).
2. **grant present** — for project resources, an active
   :class:`ProjectGrant` row exists. For workspace-level resources, a
   workspace-wide scope row (project=None) is required.
3. **role_cap covers** — the action's required role (per
   :data:`ACTION_REQUIRED_ROLE`) must be ``<=`` the ``ProjectGrant.role_cap``.
4. **owner intersection** — the SP's owner user must currently be an
   active workspace member whose role covers the effective floor. This
   step is non-cached: every call re-queries WorkspaceMember so a
   demotion takes effect on the next request.

Pre-chain guards:

* ``Action.ALL`` / ``ResourceType.ALL`` are NOT accepted as caller-side
  input. Wildcards live on the scope-row side only — the engine never
  short-circuits to a wildcard resolution against the matrix, which would
  silently drop the role floor (P2-3 from M7 review).
* ``ctx.workspace_slug`` must match the SP's workspace; the engine
  refuses to evaluate a cross-workspace request even though M6's
  management API cannot create one (defense-in-depth — P2-5).

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


# Reject caller-side wildcards. ``Action.ALL`` / ``ResourceType.ALL`` are
# scope-row-side only; accepting them at the call site would let a future
# caller bypass the matrix floor (P2-3 from M7 review).
_CALLER_WILDCARDS = frozenset({actions.Action.ALL})


@dataclass(frozen=True)
class AuthzContext:
    """Per-request context for :func:`authorize`.

    Attributes:
        workspace_slug: The workspace slug from the URL. Used by the
            owner-intersection step and the cross-workspace consistency
            check. Optional — when absent, the SP's own workspace slug is
            used (and the consistency check becomes a no-op).
        project_id: The project UUID the request is targeting, if any.
            ``None`` for workspace-level endpoints.
    """

    workspace_slug: Optional[str] = None
    project_id: Optional[str] = None


def _scope_action_set(action: str) -> list[str]:
    """Action values that satisfy a scope lookup for ``action``.

    A ``read`` scope row satisfies a ``list`` request: the SP management
    API exposes ``read`` only (no ``list``), so a scope row keyed on
    ``list`` is unreachable in practice; widening the lookup means a
    SP granted ``read`` correctly authorizes list endpoints when M8/M9
    map collection routes to ``Action.LIST``. The ``all`` wildcard is
    also honored, matching the documented scope-row semantics.
    """
    if action == actions.Action.LIST:
        return [actions.Action.LIST, actions.Action.READ, actions.Action.ALL]
    return [action, actions.Action.ALL]


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
        action: One of :class:`Action`'s values (``read``, ``list``,
            ``create``, ``update``, ``delete``). ``all`` is rejected as
            caller-side input — wildcards belong on the scope row.
        resource_type: One of :class:`ResourceType`'s values. ``all`` is
            rejected as caller-side input for the same reason.
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
    # must be registered. An unregistered pair is default-deny. Wildcards
    # are rejected at the caller boundary (see P2-3 in M7 review).
    if principal is None:
        return Decision.deny(DENY_NO_PRINCIPAL, "No principal on the request.")

    if action in _CALLER_WILDCARDS:
        return Decision.deny(
            DENY_UNREGISTERED,
            (
                f"Action '{action}' is a scope-row wildcard and not "
                "accepted as caller input."
            ),
        )

    if resource_type in _CALLER_WILDCARDS:
        return Decision.deny(
            DENY_UNREGISTERED,
            (
                f"Resource type '{resource_type}' is a scope-row wildcard "
                "and not accepted as caller input."
            ),
        )

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

    # Cross-workspace consistency check (P2-5 from M7 review). M6's
    # management API already constrains scope/grant rows to the SP's
    # workspace, so reaching this branch needs M8/M9 to also pass a
    # foreign slug. Reject defensively rather than silently using the
    # SP's workspace.
    sp_workspace_slug = getattr(sp.workspace, "slug", None)
    if (
        ctx.workspace_slug
        and sp_workspace_slug
        and ctx.workspace_slug != sp_workspace_slug
    ):
        return Decision.deny(
            DENY_NO_PRINCIPAL,
            (
                f"Workspace slug '{ctx.workspace_slug}' does not match "
                f"service principal's workspace '{sp_workspace_slug}'."
            ),
        )

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
    ):
        return Decision.deny(
            DENY_WORKSPACE_LEVEL_WRITE,
            (
                f"Writes on workspace-level resource '{resource_type}' "
                "are not permitted; route through a project-scoped grant."
            ),
        )

    # Step 1 — scope hit. Project-scoped scope wins over workspace-wide scope.
    # LIST requests are satisfied by a READ scope row (see _scope_action_set).
    from plane.service_principals.models import ServiceScope

    project_id = _resolve_project_id(resource, ctx)

    scope_qs = ServiceScope.objects.filter(
        service_principal=sp,
        resource_type__in=[resource_type, resources.ResourceType.ALL],
        action__in=_scope_action_set(action),
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

        effective_role: Optional[int] = grant.role_cap
    else:
        # Workspace-level resource: scope row is the only gate. The
        # owner-intersection step still applies; effective_role is the
        # owner's workspace role at the time of the call. Set it to the
        # action's required role so the intersection has a floor even
        # when no ProjectGrant produced one (P2-4 from M7 review).
        effective_role = None

    # Step 4 — owner intersection. The owner user must be an active
    # workspace member whose role covers the action. The role is fetched
    # fresh on every call (no cache): a demotion takes effect on the next
    # request, which is the design constraint from sp-design.md §5.2.
    workspace_slug = ctx.workspace_slug or sp_workspace_slug
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

    # Owner floor: prefer the grant's effective_role when present
    # (project-scoped resources), fall back to the action's required
    # role for workspace-level resources so the floor is never None
    # (P2-4 from M7 review).
    required_for_intersection = effective_role
    if required_for_intersection is None:
        required_for_intersection = (
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