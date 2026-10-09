# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third Party imports
from rest_framework.permissions import BasePermission, SAFE_METHODS

# Module imports
from plane.db.models import WorkspaceMember
from plane.core.authz import (
    AuthzContext,
    Action as AuthzAction,
    authorize,
    principal_from_request,
)
from plane.core.authz.principal import ServicePrincipalAuthz, UserPrincipal


# Permission Mappings
Admin = 20
Member = 15
Guest = 5


def _resource_type_for_view(view) -> str | None:
    """Return the authz resource type the view declares.

    Views opt into SP authorization by setting ``authz_resource_type``. If
    the attribute is absent or falsy the view is treated as human-only and
    SP requests short-circuit to a deny. Endpoint-declared ``resource_type``
    is the M9 contract: ``allow_permission`` reads the same attribute via
    the ``resource_type`` kwarg, but permission classes read it from the
    view object directly.
    """
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
    """``True`` when the SP principal satisfies the four-step chain for the
    view's declared resource_type and the request's HTTP method.

    When the view doesn't declare ``authz_resource_type``, the SP path is
    denied: every endpoint that wants to accept SPs has to opt in.
    Default-deny is the M9 policy for permission classes; method-level
    ``@allow_permission`` decorators carry the resource_type explicitly.
    """
    principal = principal_from_request(request)
    if not isinstance(principal, ServicePrincipalAuthz):
        return False

    resource_type = _resource_type_for_view(view)
    if not resource_type:
        return False

    action = _action_for_method(request.method)
    slug = getattr(view, "workspace_slug", None)
    ctx = AuthzContext(workspace_slug=slug)
    decision = authorize(principal, action, resource_type, ctx=ctx)
    return bool(decision.allowed)


def _user_from_principal(request):
    principal = principal_from_request(request)
    if not isinstance(principal, UserPrincipal):
        return None
    return principal.user


# TODO: Move the below logic to python match - python v3.10
class WorkSpaceBasePermission(BasePermission):
    def has_permission(self, request, view):
        # allow anyone to create a workspace
        if request.user.is_anonymous:
            return False

        # Service-principal proxy never falls through to the human path;
        # route through authorize() and short-circuit on missing resource_type.
        if isinstance(getattr(request, "user", None), type(None)):
            return False
        if getattr(request.user, "_is_service_principal_proxy", False):
            return _sp_has_permission(request, view)

        if request.method == "POST":
            return True

        ## Safe Methods
        if request.method in SAFE_METHODS:
            return True

        # allow only admins and owners to update the workspace settings
        if request.method in ["PUT", "PATCH"]:
            return WorkspaceMember.objects.filter(
                member=request.user,
                workspace__slug=view.workspace_slug,
                role__in=[Admin, Member],
                is_active=True,
            ).exists()

        # allow only owner to delete the workspace
        if request.method == "DELETE":
            return WorkspaceMember.objects.filter(
                member=request.user,
                workspace__slug=view.workspace_slug,
                role=Admin,
                is_active=True,
            ).exists()


class WorkspaceOwnerPermission(BasePermission):
    def has_permission(self, request, view):
        if request.user.is_anonymous:
            return False
        if getattr(request.user, "_is_service_principal_proxy", False):
            return _sp_has_permission(request, view)

        return WorkspaceMember.objects.filter(
            workspace__slug=view.workspace_slug, member=request.user, role=Admin, is_active=True
        ).exists()


class WorkSpaceAdminPermission(BasePermission):
    def has_permission(self, request, view):
        if request.user.is_anonymous:
            return False
        if getattr(request.user, "_is_service_principal_proxy", False):
            return _sp_has_permission(request, view)

        return WorkspaceMember.objects.filter(
            member=request.user,
            workspace__slug=view.workspace_slug,
            role__in=[Admin, Member],
            is_active=True,
        ).exists()


class WorkspaceEntityPermission(BasePermission):
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

        return WorkspaceMember.objects.filter(
            member=request.user,
            workspace__slug=view.workspace_slug,
            role__in=[Admin, Member],
            is_active=True,
        ).exists()


class WorkspaceViewerPermission(BasePermission):
    def has_permission(self, request, view):
        if request.user.is_anonymous:
            return False
        if getattr(request.user, "_is_service_principal_proxy", False):
            return _sp_has_permission(request, view)

        return WorkspaceMember.objects.filter(
            member=request.user, workspace__slug=view.workspace_slug, is_active=True
        ).exists()


class WorkspaceUserPermission(BasePermission):
    def has_permission(self, request, view):
        if request.user.is_anonymous:
            return False
        if getattr(request.user, "_is_service_principal_proxy", False):
            return _sp_has_permission(request, view)

        return WorkspaceMember.objects.filter(
            member=request.user, workspace__slug=view.workspace_slug, is_active=True
        ).exists()


class WorkspaceMemberPermission(BasePermission):
    """Allows access only to active workspace members.

    Resolves the workspace via 'slug' or 'workspace_id' in URL kwargs so this
    class can be used on endpoints that identify the workspace by either
    identifier (e.g. FileAssetEndpoint which mixes both URL patterns).

    For SP requests the class defers to :func:`_sp_has_permission`. SP
    authorization requires the endpoint to declare ``authz_resource_type``
    explicitly (the workspace-member predicate is too coarse to infer
    intent); endpoints that want SP access typically override this class
    with a more specific permission set.
    """

    def has_permission(self, request, view):
        if request.user.is_anonymous:
            return False
        if getattr(request.user, "_is_service_principal_proxy", False):
            return _sp_has_permission(request, view)

        workspace_id = view.kwargs.get("workspace_id")
        if workspace_id:
            return WorkspaceMember.objects.filter(
                workspace_id=workspace_id, member=request.user, is_active=True
            ).exists()

        slug = view.kwargs.get("slug")
        if slug:
            return WorkspaceMember.objects.filter(
                workspace__slug=slug, member=request.user, is_active=True
            ).exists()

        return False
