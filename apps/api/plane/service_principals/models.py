# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Service Principal models.

A ServicePrincipal (SP) is a non-human principal owned by a workspace admin.
It is NOT a User — it has no WorkspaceMember / ProjectMember rows and never
appears in member pickers, mention search, or seat counts. Authentication is
through service tokens (``plane_svc_`` prefixed APIToken rows) and
authorization goes through the SP scope / grant tables defined here.
"""

from django.conf import settings
from django.db import models

from plane.db.models import BaseModel

from .constants import ACTION_CHOICES, RESOURCE_CHOICES


class WorkspaceSPSettings(BaseModel):
    """Per-workspace SP toggles. One row per workspace, lazily created."""

    workspace = models.OneToOneField(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="sp_settings",
    )
    # When False (default) SPs are hidden from assignee / filter / mention
    # pickers. When True the unified visible-member predicate picks them up
    # alongside human WorkspaceMember rows. The flag exists to keep an opt-in
    # default: assignable bots are a workspace-level policy decision.
    sp_assignable = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Workspace SP Settings"
        verbose_name_plural = "Workspace SP Settings"
        db_table = "workspace_sp_settings"
        ordering = ("-created_at",)

    def __str__(self):
        return f"WorkspaceSPSettings(workspace={self.workspace_id})"


class ServicePrincipal(BaseModel):
    """A non-human principal owned by a workspace admin.

    SPs do not appear in WorkspaceMember / ProjectMember tables; the only
    inheritance path is the explicit ProjectGrant rows.
    """

    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="service_principals",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_service_principals",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    # Optional avatar URL — no FK to FileAsset; SPs do not get the workspace
    # upload pipeline by default and accept either a URL or a path string.
    avatar = models.CharField(max_length=800, blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Service Principal"
        verbose_name_plural = "Service Principals"
        db_table = "service_principals"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.name} ({self.workspace.slug})"


class ServiceScope(BaseModel):
    """SP scope row: the SP may perform ``action`` on ``resource_type`` in
    ``project`` (null project = workspace-wide).

    Absence of a matching row means denied (default-deny). The wildcard
    resource_type / action ("all") is honored at the scope layer; project
    visibility is decided separately by ProjectGrant.
    """

    service_principal = models.ForeignKey(
        ServicePrincipal,
        on_delete=models.CASCADE,
        related_name="scopes",
    )
    project = models.ForeignKey(
        "db.Project",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="sp_scopes",
    )
    resource_type = models.CharField(max_length=50, choices=RESOURCE_CHOICES)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)

    class Meta:
        verbose_name = "Service Scope"
        verbose_name_plural = "Service Scopes"
        db_table = "service_principal_scopes"
        ordering = ("-created_at",)
        unique_together = ["service_principal", "project", "resource_type", "action", "deleted_at"]

    def __str__(self):
        return f"{self.service_principal.name}: {self.action} {self.resource_type}"


class ProjectGrant(BaseModel):
    """Per-project grant of an SP into a project with a role cap.

    role_cap mirrors the existing Plane role integers (20=ADMIN/15=MEMBER/
    5=GUEST). It is the upper bound the SP may reach inside this project; the
    owner-intersection check at authorize() time caps it again by the SP
    owner's current workspace role. Absence of an active row means the SP is
    not a member of the project (default-deny).
    """

    ROLE_CAP_CHOICES = (
        (20, "Admin"),
        (15, "Member"),
        (5, "Guest"),
    )

    service_principal = models.ForeignKey(
        ServicePrincipal,
        on_delete=models.CASCADE,
        related_name="grants",
    )
    project = models.ForeignKey(
        "db.Project",
        on_delete=models.CASCADE,
        related_name="sp_grants",
    )
    role_cap = models.PositiveSmallIntegerField(choices=ROLE_CAP_CHOICES, default=15)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Project Grant"
        verbose_name_plural = "Project Grants"
        db_table = "service_principal_project_grants"
        ordering = ("-created_at",)
        unique_together = ["service_principal", "project", "deleted_at"]

    def __str__(self):
        return f"{self.service_principal.name} -> {self.project_id} (cap={self.role_cap})"