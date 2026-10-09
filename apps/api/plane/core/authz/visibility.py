# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Member visibility predicates and effective-permission computation.

Single source of truth for who counts as a workspace member on every
surface: roster lists, assignee pickers, mention search, seat counts, and
analytics. Replaces the fragmented ``AI_VISIBLE_MEMBER_Q`` and friends
in :mod:`plane.ai_accounts.constants`.

The ``sp_assignable`` workspace toggle (see
:class:`plane.service_principals.models.WorkspaceSPSettings`) is the only
switch that ever toggles SP visibility. When it is False — the default —
SPs are invisible to every member surface. When True, SPs appear alongside
human WorkspaceMember rows in pickers and search results.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.db.models import Q, QuerySet

from plane.db.models import WorkspaceMember

if TYPE_CHECKING:
    from plane.db.models import User
    from plane.service_principals.models import (
        ServicePrincipal,
        WorkspaceSPSettings,
    )


# Predicate for human member querysets: ``is_bot=False`` is the canonical
# filter, equivalent to the legacy ``AI_VISIBLE_MEMBER_Q`` without the
# AI-agent exception. SPs are not User rows, so they never appear in a
# WorkspaceMember queryset regardless — they get bolted on by
# :func:`visible_principal_qs` when ``sp_assignable`` is on.
VISIBLE_MEMBER_Q = Q(member__is_bot=False)


def is_sp_assignable(
    workspace_settings: "WorkspaceSPSettings | None",
) -> bool:
    """Return the value of the ``sp_assignable`` toggle.

    A missing settings row is treated as the default (False): the flag
    is opt-in. Callers that want to mutate this should read
    :func:`get_or_create_workspace_sp_settings` instead.
    """
    if workspace_settings is None:
        return False
    return bool(workspace_settings.sp_assignable)


def get_or_create_workspace_sp_settings(workspace) -> "WorkspaceSPSettings":
    """Return the per-workspace SP settings row, creating it lazily."""
    from plane.service_principals.models import WorkspaceSPSettings

    settings, _ = WorkspaceSPSettings.objects.get_or_create(workspace=workspace)
    return settings


@dataclass(frozen=True)
class VisiblePrincipal:
    """A row produced by :func:`visible_principal_qs`.

    Holds the kind (``user`` or ``service``) plus the identifying fields
    the front end needs to render the picker. Concrete model rows are
    attached under ``row`` for callers that need the full object.
    """

    kind: str
    row: object


def visible_principal_qs(
    workspace,
    *,
    include_inactive_members: bool = False,
) -> list[VisiblePrincipal]:
    """Return the principals visible to a workspace member picker.

    Combines the active human :class:`WorkspaceMember` queryset (filtered
    by :data:`VISIBLE_MEMBER_Q`) with active
    :class:`ServicePrincipal` rows when the workspace's
    :data:`WorkspaceSPSettings.sp_assignable` flag is True.

    The result is a list (not a queryset) because the two source sets
    are heterogeneous; callers that need a queryset should use
    :func:`visible_member_qs` (humans only) or :func:`visible_sp_qs`
    (services only).

    Args:
        workspace: Workspace instance.
        include_inactive_members: When True, members with ``is_active=False``
            are included. Default False (active only). SPs always honor
            their own ``is_active`` flag.
    """
    members_qs = WorkspaceMember.objects.filter(workspace=workspace)
    if not include_inactive_members:
        members_qs = members_qs.filter(is_active=True)
    members_qs = members_qs.filter(VISIBLE_MEMBER_Q).select_related("member")

    result: list[VisiblePrincipal] = [
        VisiblePrincipal(kind="user", row=row) for row in members_qs
    ]

    if is_sp_assignable(getattr(workspace, "sp_settings", None)):
        from plane.service_principals.models import ServicePrincipal

        sps = ServicePrincipal.objects.filter(workspace=workspace, is_active=True)
        result.extend(VisiblePrincipal(kind="service", row=row) for row in sps)

    return result


def visible_member_qs(
    workspace,
    *,
    include_inactive_members: bool = False,
) -> QuerySet:
    """Return only the human :class:`WorkspaceMember` queryset.

    Use this when counting seats, building analytics, or any surface that
    must never include SPs regardless of the ``sp_assignable`` switch.
    """
    qs = WorkspaceMember.objects.filter(workspace=workspace)
    if not include_inactive_members:
        qs = qs.filter(is_active=True)
    return qs.filter(VISIBLE_MEMBER_Q)


def visible_sp_qs(workspace) -> QuerySet:
    """Return the active :class:`ServicePrincipal` queryset for a workspace.

    Returns an empty queryset when ``sp_assignable`` is False (the
    default), so callers can safely use the result without re-checking
    the toggle. This is the consumption point for picker / filter /
    mention surfaces.
    """
    from plane.service_principals.models import ServicePrincipal

    settings = getattr(workspace, "sp_settings", None)
    if not is_sp_assignable(settings):
        return ServicePrincipal.objects.none()

    return ServicePrincipal.objects.filter(workspace=workspace, is_active=True)


def effective_role_cap(
    sp: "ServicePrincipal",
    project_id: str,
    *,
    workspace_slug: str | None = None,
) -> int | None:
    """The runtime-effective role cap of an SP on a project.

    Returns the ``ProjectGrant.role_cap`` for the active grant on
    ``project_id``, capped again by the owner's current workspace role.
    ``None`` means the SP has no active grant (or the owner is no longer
    a workspace member) — the SP cannot act on this project.

    This is the value the front end should display as "this bot can act
    as …" and is recomputed on every call so demotion is immediate.
    """
    from plane.service_principals.models import ProjectGrant

    grant = (
        ProjectGrant.objects.filter(
            service_principal=sp, project_id=project_id, is_active=True
        )
        .values_list("role_cap", flat=True)
        .first()
    )
    if grant is None:
        return None

    slug = workspace_slug or getattr(sp.workspace, "slug", None)
    if slug is None:
        return grant

    owner_role = (
        WorkspaceMember.objects.filter(
            workspace__slug=slug,
            member_id=sp.owner_id,
            is_active=True,
        )
        .values_list("role", flat=True)
        .first()
    )
    if owner_role is None:
        return None

    return min(grant, owner_role)


def is_member_visible(user: User | None) -> bool:
    """Predicate for callers that have a single User in hand.

    The boolean form of :data:`VISIBLE_MEMBER_Q` for callers that
    already have a User instance and want a yes/no without going
    through a queryset.
    """
    if user is None:
        return False
    return bool(user.is_active and not getattr(user, "is_bot", False))