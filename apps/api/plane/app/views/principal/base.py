# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Unified principal-dispatch endpoint for the app.

Returns the **single source of truth** the front end consumes to render
member pickers, filter chips, mention search, and action affordances.
The web client (M10) used to recompute these predicates per-view; M9
hands the computation off to the API so every page agrees.

Response shape:

::

    {
        "principal": {
            "kind": "user" | "service",
            "id": "<user-or-sp uuid>",
            "workspace_role": 20 | 15 | 5 | null,
        },
        "members": [
            {
                "kind": "user" | "service",
                "id": ...,
                "display_name": ...,
                "email": ... | null,
                "avatar": ...,
                "role": 20 | 15 | 5 | null,
                "is_active": true | false,
            },
            ...
        ],
        "sp_assignable": true | false,
        "permissions": {
            "<resource_type>": {
                "read":  {"allowed": bool, "effective_role": 20 | null},
                "create":{...},
                ...
            },
            ...
        }
    }

The members list honors :data:`plane.core.authz.VISIBLE_MEMBER_Q` (filters
out ``is_bot`` users) and the per-workspace ``sp_assignable`` switch.
Effective permissions cover a fixed, resource-type-agnostic vocabulary —
the front end asks for "what can I do on work_items" and gets the matrix
back rather than guessing from the role.

SP requests still get a body — the visible-members list is empty (SPs are
not workspace members) and the permissions matrix comes from the SP's
own scope/grant set.
"""

from __future__ import annotations

from typing import Optional

from rest_framework import status
from rest_framework.response import Response

from plane.app.permissions import ROLE, allow_permission
from plane.app.views.base import BaseAPIView
from plane.core.authz import (
    ActionPermission,
    ALLOWED,
    DENY_NO_PRINCIPAL,
    DENY_ROLE_CAP,
    DENY_UNREGISTERED,
    ResourceType,
    STANDARD_ACTIONS,
    effective_actions,
    is_sp_assignable,
    principal_from_request,
    required_role,
    visible_member_qs,
    visible_sp_qs,
)
from plane.core.authz.principal import (
    ServicePrincipalAuthz,
    UserPrincipal,
)
from plane.db.models import Workspace, WorkspaceMember
from plane.service_principals.models import WorkspaceSPSettings


# Resource-type vocabulary exposed to the front end. Excludes ``all`` (it
# is a wildcard, not a real resource) and ``invite`` (workspace-only, the
# SP cannot reach it through the picker anyway).
_DISPATCH_RESOURCE_TYPES = (
    ResourceType.PROJECT,
    ResourceType.WORK_ITEM,
    ResourceType.CYCLE,
    ResourceType.MODULE,
    ResourceType.PAGE,
    ResourceType.STATE,
    ResourceType.LABEL,
    ResourceType.ESTIMATE,
    ResourceType.INTAKE,
    ResourceType.COMMENT,
    ResourceType.ASSET,
    ResourceType.STICKY,
    ResourceType.MEMBER,
    ResourceType.USER,
)


# Workspace member role constants, kept in sync with plane.app.permissions.ROLE.
_ROLE_ADMIN = 20
_ROLE_MEMBER = 15
_ROLE_GUEST = 5


def _human_workspace_role(principal: UserPrincipal, slug: str) -> Optional[int]:
    """Workspace role for a UserPrincipal, or None when not a member."""
    if not slug:
        return None
    return (
        WorkspaceMember.objects.filter(
            workspace__slug=slug, member=principal.user, is_active=True
        )
        .values_list("role", flat=True)
        .first()
    )


def _user_effective_actions(
    workspace_role: Optional[int],
    resource_type: str,
) -> list[ActionPermission]:
    """Per-action allow/deny derived from the user's actual workspace role.

    The authz engine short-circuits non-``ServicePrincipal_`` principals
    to ``Decision.allow``; for the dispatch endpoint we need truthful
    per-action results so the front end stops recomputing predicates (a
    guest must NOT see ``delete.allowed=true`` on work_items just because
    the engine has no human-side role gate).
    """
    results: list[ActionPermission] = []
    for action in STANDARD_ACTIONS:
        required = required_role(action, resource_type)
        if required is None:
            results.append(
                ActionPermission(
                    action=action,
                    resource_type=resource_type,
                    allowed=False,
                    reason=DENY_UNREGISTERED,
                    effective_role=None,
                )
            )
            continue
        if workspace_role is None:
            # Not a workspace member — no action allowed on the resource.
            results.append(
                ActionPermission(
                    action=action,
                    resource_type=resource_type,
                    allowed=False,
                    reason=DENY_NO_PRINCIPAL,
                    effective_role=None,
                )
            )
            continue
        allowed = workspace_role >= required
        results.append(
            ActionPermission(
                action=action,
                resource_type=resource_type,
                allowed=allowed,
                reason=ALLOWED if allowed else DENY_ROLE_CAP,
                effective_role=workspace_role,
            )
        )
    return results


def _sp_effective_actions(
    principal: ServicePrincipalAuthz,
    resource_type: str,
    *,
    project_id: Optional[str] = None,
) -> list[ActionPermission]:
    """SP permissions route through :func:`plane.core.authz.effective_actions`.

    The engine runs the four-step chain (scope → grant → role_cap → owner
    intersection) for each (action, resource_type). The same rules that
    gate a real SP call gate the dispatch result, so the matrix is
    truthful: a bare SP token (no scope / no grant) reports deny on every
    action.
    """
    return effective_actions(
        principal,
        resource_type,
        project=None,  # dispatch is workspace-level; project_id passed in future
    )


class PrincipalDispatchEndpoint(BaseAPIView):
    """GET /api/workspaces/<slug>/principal/permissions/.

    Returns the unified principal payload — visible members, the
    ``sp_assignable`` switch, and the per-resource-type effective
    permissions matrix. The endpoint takes no project id; the
    workspace-level effective permissions are returned alongside the
    project-scoped ones (the front end queries project-scoped data
    separately when needed, but the workspace vocabulary is what every
    picker / filter chip / action button consumes).

    Privacy:

    * Service principals see an empty members list by default (they aren't
      workspace members). The SP's own row appears in the list only when
      ``sp_assignable`` is True on the workspace; no other SPs are exposed.
    * Human **guests** see the human roster without ``email`` (mirrors
      ``WorkSpaceMemberViewSet.list`` which withholds email when the viewer
      is at guest level). Humans with role > GUEST see the same roster with
      email included.
    """

    # The dispatch endpoint is the M10 wire; SPs need to read their own
    # permissions, so the default ``IsAuthenticatedNoSP`` boundary must
    # opt in. Method-level :func:`allow_permission` then does the
    # resource_type-aware check (``sp_fallback=True`` — this endpoint is
    # meta so the SP path doesn't carry a single resource_type).
    allow_service_principal = True

    @allow_permission(
        allowed_roles=[ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST],
        level="WORKSPACE",
        sp_fallback=True,
    )
    def get(self, request, slug):
        principal = principal_from_request(request)
        if principal is None:
            return Response(
                {"error": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            return Response(
                {"error": "Workspace not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Lazy read: the WorkspaceSPSettings row may not exist yet; the
        # default (``sp_assignable=False``) is returned without persisting.
        # Persisting on a read endpoint would break the GET idempotency
        # contract the M8 endpoint establishes.
        settings_row = WorkspaceSPSettings.objects.filter(workspace=workspace).first()
        sp_assignable = is_sp_assignable(settings_row)

        # Compute the human's workspace role once and reuse for both the
        # principal block and the permissions matrix. The SP path doesn't
        # need a workspace role here (the engine resolves it from
        # WorkspaceMember on every authorize() call).
        human_workspace_role: Optional[int] = None
        if isinstance(principal, UserPrincipal):
            human_workspace_role = _human_workspace_role(principal, slug)

        members_payload = _serialize_members(
            workspace,
            principal=principal,
            viewer_workspace_role=human_workspace_role,
            sp_assignable=sp_assignable,
        )

        principal_payload = _serialize_principal(principal, slug, human_workspace_role)

        permissions_payload = _serialize_permissions(
            principal=principal,
            viewer_workspace_role=human_workspace_role,
        )

        return Response(
            {
                "principal": principal_payload,
                "members": members_payload,
                "sp_assignable": sp_assignable,
                "permissions": permissions_payload,
            },
            status=status.HTTP_200_OK,
        )


def _serialize_members(
    workspace,
    *,
    principal,
    viewer_workspace_role: Optional[int],
    sp_assignable: bool,
) -> list[dict]:
    """Visible members block.

    Service principals see at most their own row (and only when
    ``sp_assignable`` is on). Humans see the full active human roster;
    email exposure follows the caller's role — guests don't get email,
    members / admins do.
    """
    is_sp = isinstance(principal, ServicePrincipalAuthz)
    rows: list[dict] = []

    if is_sp:
        # Per contract: SPs are not workspace members. We only surface
        # the caller itself when the workspace opts into assignable bots,
        # so the front end can render the SP's own chip.
        if sp_assignable:
            sp_row = visible_sp_qs(workspace).filter(
                id=principal.service_principal.id
            ).first()
            if sp_row is not None:
                rows.append(
                    {
                        "kind": "service",
                        "id": str(sp_row.id),
                        "display_name": sp_row.name,
                        "email": None,
                        "avatar": getattr(sp_row, "avatar", "") or "",
                        "role": None,
                        "is_active": bool(getattr(sp_row, "is_active", True)),
                    }
                )
        return rows

    # Human path: full visible roster, email exposed only when role > GUEST.
    include_email = (viewer_workspace_role or 0) > _ROLE_GUEST
    members_qs = visible_member_qs(workspace).select_related(
        "member", "member__avatar_asset"
    )
    rows.extend(
        {
            "kind": "user",
            "id": str(row.member_id),
            "display_name": (row.member.display_name or "").strip(),
            "email": row.member.email if include_email else None,
            "avatar": getattr(getattr(row.member, "avatar_asset", None), "asset", "") or "",
            "role": row.role,
            "is_active": row.is_active and bool(getattr(row.member, "is_active", True)),
        }
        for row in members_qs
    )
    return rows


def _serialize_principal(
    principal, slug: str, human_workspace_role: Optional[int]
) -> dict:
    if isinstance(principal, UserPrincipal):
        return {
            "kind": "user",
            "id": str(getattr(principal.user, "id", "")),
            "workspace_role": human_workspace_role,
        }
    if isinstance(principal, ServicePrincipalAuthz):
        return {
            "kind": "service",
            "id": str(principal.service_principal.id),
            # SPs have no workspace member role; the engine resolves
            # the owner's role on every authorize() call instead.
            "workspace_role": None,
        }
    return {"kind": "anonymous", "id": "", "workspace_role": None}


def _serialize_permissions(
    principal,
    *,
    viewer_workspace_role: Optional[int],
) -> dict:
    """Per-(resource_type, action) allow/deny matrix.

    For humans we cannot rely on the authz engine — it short-circuits
    non-``ServicePrincipal_`` principals to allow, which would tell a
    guest they can ``delete`` work_items. Instead we walk the role
    matrix (``ACTION_REQUIRED_ROLE``) against the caller's workspace
    role and report truthfully. For SPs we let the engine run; the
    four-step chain is the same gate a real SP call goes through.
    """
    payload: dict = {}
    for resource_type in _DISPATCH_RESOURCE_TYPES:
        if isinstance(principal, ServicePrincipalAuthz):
            perms = _sp_effective_actions(principal, resource_type)
        else:
            perms = _user_effective_actions(viewer_workspace_role, resource_type)
        payload[resource_type] = {
            ap.action: {
                "allowed": ap.allowed,
                "effective_role": ap.effective_role,
                "reason": ap.reason,
            }
            for ap in perms
        }
    return payload

