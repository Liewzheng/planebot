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
    ResourceType,
    effective_actions,
    effective_role_cap,
    get_or_create_workspace_sp_settings,
    is_sp_assignable,
    principal_from_request,
    visible_member_qs,
    visible_sp_qs,
)
from plane.core.authz.principal import (
    ServicePrincipalAuthz,
    UserPrincipal,
)
from plane.db.models import Workspace, WorkspaceMember
from plane.service_principals.models import ServicePrincipal  # noqa: F401


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


def _principal_role_for_human(principal: UserPrincipal, slug: str) -> Optional[int]:
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


def _principal_role_for_sp(
    principal: ServicePrincipalAuthz, slug: str, project_id: Optional[str]
) -> Optional[int]:
    """Effective role cap for an SP. ``None`` means the SP cannot act.

    For project-scoped resources we read the grant cap (the owner's role
    cap at the workspace level); for workspace-level resources we return
    the owner's workspace role (the engine has the role_cap floor).
    """
    if not project_id:
        # No project: surface the owner's workspace role so the front end
        # can show "this SP acts as Workspace Admin/Member/Guest".
        sp = principal.service_principal
        slug = slug or getattr(getattr(sp, "workspace", None), "slug", None)
        if not slug:
            return None
        return (
            WorkspaceMember.objects.filter(
                workspace__slug=slug, member=sp.owner_id, is_active=True
            )
            .values_list("role", flat=True)
            .first()
        )
    return effective_role_cap(
        principal.service_principal, str(project_id), workspace_slug=slug
    )


class PrincipalDispatchEndpoint(BaseAPIView):
    """GET /api/workspaces/<slug>/principal/permissions/.

    Returns the unified principal payload — visible members and effective
    permissions per resource type. The endpoint takes no project id; the
    workspace-level effective permissions are returned alongside the
    project-scoped ones (the front end queries project-scoped data
    separately when needed, but the workspace vocabulary is what every
    picker / filter chip / action button consumes).
    """

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

        # Settings row (or none) and the sp_assignable switch.
        settings_row = get_or_create_workspace_sp_settings(workspace)
        sp_assignable = is_sp_assignable(settings_row)

        # Members + (optional) SPs.
        members_payload = _serialize_members(
            workspace,
            include_sps=sp_assignable,
        )

        principal_payload = _serialize_principal(principal, slug)

        # Effective permissions per resource type. We call
        # effective_actions once per resource type so the front end can
        # stamp "you can edit issues" / "you can view pages" in one go.
        permissions_payload = _serialize_permissions(
            principal=principal,
            workspace=workspace,
            slug=slug,
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


def _serialize_members(workspace, *, include_sps: bool) -> list[dict]:
    members_qs = visible_member_qs(workspace).select_related("member", "member__avatar_asset")
    rows = [
        {
            "kind": "user",
            "id": str(row.member_id),
            "display_name": (row.member.display_name or "").strip(),
            "email": row.member.email,
            "avatar": getattr(getattr(row.member, "avatar_asset", None), "asset", "") or "",
            "role": row.role,
            "is_active": row.is_active and bool(getattr(row.member, "is_active", True)),
        }
        for row in members_qs
    ]

    if include_sps:
        sp_qs = visible_sp_qs(workspace)
        rows.extend(
            {
                "kind": "service",
                "id": str(sp_row.id),
                "display_name": sp_row.name,
                "email": None,
                "avatar": getattr(sp_row, "avatar", "") or "",
                "role": None,
                "is_active": bool(getattr(sp_row, "is_active", True)),
            }
            for sp_row in sp_qs
        )

    return rows


def _serialize_principal(
    principal, slug: str
) -> dict:
    if isinstance(principal, UserPrincipal):
        role = _principal_role_for_human(principal, slug)
        return {
            "kind": "user",
            "id": str(getattr(principal.user, "id", "")),
            "workspace_role": role,
        }
    if isinstance(principal, ServicePrincipalAuthz):
        return {
            "kind": "service",
            "id": str(principal.service_principal.id),
            "workspace_role": None,
        }
    return {"kind": "anonymous", "id": "", "workspace_role": None}


def _serialize_permissions(principal, *, workspace, slug: str) -> dict:
    payload: dict = {}
    for resource_type in _DISPATCH_RESOURCE_TYPES:
        perms: list[ActionPermission] = effective_actions(
            principal,
            resource_type,
            workspace=workspace,
        )
        payload[resource_type] = {
            ap.action: {
                "allowed": ap.allowed,
                "effective_role": ap.effective_role,
                "reason": ap.reason,
            }
            for ap in perms
        }
    return payload
