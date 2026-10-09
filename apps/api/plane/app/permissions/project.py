# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third Party imports
from rest_framework.permissions import SAFE_METHODS, BasePermission

# Module import
from plane.db.models import ProjectMember, WorkspaceMember
from plane.db.models.project import ROLE
from plane.core.authz import (
    AuthzContext,
    Action as AuthzAction,
    authorize,
    principal_from_request,
)
from plane.core.authz.principal import ServicePrincipalAuthz


def _resource_type_for_view(view) -> str | None:
    return getattr(view, "authz_resource_type", None)


def _action_for_method(method: str) -> str:
    if method in ("GET", "HEAD", "OPTIONS"):
        return AuthzAction.READ
    if method == "POST":
        return AuthzAction.CREATE
    if method in ("PUT", "PATCH"):
        return AuthzAction.UPDATE
    if method == "DELETE":
        return AuthzAction.DELETE
    return AuthzAction.READ


def _sp_has_permission(request, view) -> bool:
    """``True`` when the SP satisfies the four-step chain for the view's
    declared resource_type. Project-scoped resources require the project
    id from the URL kwargs (the engine uses it for grant lookup).
    """
    principal = principal_from_request(request)
    if not isinstance(principal, ServicePrincipalAuthz):
        return False

    resource_type = _resource_type_for_view(view)
    if not resource_type:
        return False

    action = _action_for_method(request.method)
    slug = getattr(view, "workspace_slug", None)
    project_id = getattr(view, "project_id", None) or view.kwargs.get("project_id")
    ctx = AuthzContext(workspace_slug=slug, project_id=str(project_id) if project_id else None)
    decision = authorize(principal, action, resource_type, ctx=ctx)
    return bool(decision.allowed)


class ProjectBasePermission(BasePermission):
    def has_permission(self, request, view):
        if request.user.is_anonymous:
            return False
        if getattr(request.user, "_is_service_principal_proxy", False):
            return _sp_has_permission(request, view)

        ## Safe Methods -> Handle the filtering logic in queryset
        if request.method in SAFE_METHODS:
            return WorkspaceMember.objects.filter(
                workspace__slug=view.workspace_slug, member=request.user, is_active=True
            ).exists()

        ## Only workspace owners or admins can create the projects
        if request.method == "POST":
            return WorkspaceMember.objects.filter(
                workspace__slug=view.workspace_slug,
                member=request.user,
                role__in=[ROLE.ADMIN.value, ROLE.MEMBER.value],
                is_active=True,
            ).exists()

        project_member_qs = ProjectMember.objects.filter(
            workspace__slug=view.workspace_slug,
            member=request.user,
            project_id=view.project_id,
            is_active=True,
        )

        ## Only project admins or workspace admin who is part of the project can access

        if project_member_qs.filter(role=ROLE.ADMIN.value).exists():
            return True
        else:
            return (
                project_member_qs.exists()
                and WorkspaceMember.objects.filter(
                    member=request.user,
                    workspace__slug=view.workspace_slug,
                    role=ROLE.ADMIN.value,
                    is_active=True,
                ).exists()
            )


class ProjectMemberPermission(BasePermission):
    def has_permission(self, request, view):
        if request.user.is_anonymous:
            return False
        if getattr(request.user, "_is_service_principal_proxy", False):
            return _sp_has_permission(request, view)

        ## Safe Methods -> Handle the filtering logic in queryset
        if request.method in SAFE_METHODS:
            return ProjectMember.objects.filter(
                workspace__slug=view.workspace_slug,
                member=request.user,
                project_id=view.project_id,
                is_active=True,
            ).exists()
        ## Only workspace owners or admins can create the projects
        if request.method == "POST":
            return WorkspaceMember.objects.filter(
                workspace__slug=view.workspace_slug,
                member=request.user,
                role__in=[ROLE.ADMIN.value, ROLE.MEMBER.value],
                is_active=True,
            ).exists()

        ## Only Project Admins can update project attributes
        return ProjectMember.objects.filter(
            workspace__slug=view.workspace_slug,
            member=request.user,
            role__in=[ROLE.ADMIN.value, ROLE.MEMBER.value],
            project_id=view.project_id,
            is_active=True,
        ).exists()


class ProjectEntityPermission(BasePermission):
    def has_permission(self, request, view):
        if request.user.is_anonymous:
            return False
        if getattr(request.user, "_is_service_principal_proxy", False):
            return _sp_has_permission(request, view)

        # Handle requests based on project__identifier
        if hasattr(view, "project_identifier") and view.project_identifier:
            if request.method in SAFE_METHODS:
                return ProjectMember.objects.filter(
                    workspace__slug=view.workspace_slug,
                    member=request.user,
                    project__identifier=view.project_identifier,
                    is_active=True,
                ).exists()

        ## Safe Methods -> Handle the filtering logic in queryset
        if request.method in SAFE_METHODS:
            return ProjectMember.objects.filter(
                workspace__slug=view.workspace_slug,
                member=request.user,
                project_id=view.project_id,
                is_active=True,
            ).exists()

        ## Only project members or admins can create and edit the project attributes
        return ProjectMember.objects.filter(
            workspace__slug=view.workspace_slug,
            member=request.user,
            role__in=[ROLE.ADMIN.value, ROLE.MEMBER.value],
            project_id=view.project_id,
            is_active=True,
        ).exists()


class ProjectAdminPermission(BasePermission):
    def has_permission(self, request, view):
        if request.user.is_anonymous:
            return False
        if getattr(request.user, "_is_service_principal_proxy", False):
            return _sp_has_permission(request, view)

        return ProjectMember.objects.filter(
            workspace__slug=view.workspace_slug,
            member=request.user,
            role=ROLE.ADMIN.value,
            project_id=view.project_id,
            is_active=True,
        ).exists()


class ProjectLitePermission(BasePermission):
    def has_permission(self, request, view):
        if request.user.is_anonymous:
            return False
        if getattr(request.user, "_is_service_principal_proxy", False):
            return _sp_has_permission(request, view)

        return ProjectMember.objects.filter(
            workspace__slug=view.workspace_slug,
            member=request.user,
            project_id=view.project_id,
            is_active=True,
        ).exists()
