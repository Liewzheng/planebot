# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Decorators and helpers used by the app-layer permission classes.

:meth:`allow_permission` is the workhorse decorator found on virtually every
internal endpoint. It started as a pure role-membership check against
:class:`WorkspaceMember` / :class:`ProjectMember`; mission M9 wires the
same decorator so that a :class:`ServicePrincipal_` request flows through
:class:`plane.core.authz.engine.authorize` instead.

The path split preserves the human-session contract:

* **Human principals** keep their existing role membership checks
  (workspace admin / member / guest) — no change to the call site.
* **Service principals** are routed through ``authorize()`` with the
  endpoint-declared ``resource_type`` and an HTTP-method-derived action.
  The endpoint MUST declare ``resource_type`` to be reachable for SPs.

The two paths share the same 403 error body and a ``crew``-friendly
``detail`` so the front end can keep treating them identically.
"""

from plane.db.models import WorkspaceMember, ProjectMember
from functools import wraps
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework import status

from enum import Enum

from plane.core.authz import (
    AuthzContext,
    Action as AuthzAction,
    authorize,
    principal_from_request,
)
from plane.core.authz.principal import ServicePrincipalAuthz, UserPrincipal


class ROLE(Enum):
    ADMIN = 20
    MEMBER = 15
    GUEST = 5


_DENY_BODY = {"error": "You don't have the required permissions."}


class IsAuthenticatedNoSP(BasePermission):
    """Default auth boundary for the internal app API.

    Wraps DRF's :class:`IsAuthenticated` semantics but additionally denies
    the service-principal proxy unless the view explicitly opts in via
    ``allow_service_principal = True``. Without this gate, every endpoint
    using only ``permission_classes = [IsAuthenticated]`` would let a bare
    service token through — the proxy passes ``is_authenticated=True`` and
    role-based filters (the only safety net) would fail closed by accident
    (``request.user.id`` is ``None`` and member queries return empty
    sets) — surfacing as 400/500 rather than 403.
    """

    message = "Authentication required."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        if getattr(user, "_is_service_principal_proxy", False):
            return bool(getattr(view, "allow_service_principal", False))
        return True


def _method_to_action(method: str) -> str:
    """Map an HTTP verb to an :class:`plane.core.authz.actions.Action`.

    ``HEAD``/``OPTIONS`` are treated as reads (collection membership, not
    state changes). The fallback covers DRF custom verbs — they inherit
    read semantics so the engine does not over-permit a writer.
    """
    if method in ("GET", "HEAD", "OPTIONS"):
        return AuthzAction.READ
    if method == "POST":
        return AuthzAction.CREATE
    if method in ("PUT", "PATCH"):
        return AuthzAction.UPDATE
    if method == "DELETE":
        return AuthzAction.DELETE
    return AuthzAction.READ


def _authorize_sp(
    principal: ServicePrincipalAuthz,
    *,
    level: str,
    resource_type: str | None,
    method: str,
    slug: str | None,
    project_id: str | None,
    sp_fallback: bool,
):
    """Run :func:`authorize` for an SP and short-circuit on deny.

    The endpoint must declare ``resource_type`` for an SP to reach this
    branch — without it we cannot pick the action-required-role floor so
    we deny. The action comes from the HTTP verb; project_id is taken
    from the URL kwargs when the endpoint is project-scoped (``level``
    or an explicit project id in kwargs).

    When ``sp_fallback`` is True, an SP token without resource_type is
    allowed through (the dispatch endpoint is this shape — it's the
    mechanism by which the front end learns what the SP can do). SP
    tokens still must be alive (``sp.is_active``); authorize() handles
    that in the engine.
    """
    if not resource_type:
        if sp_fallback:
            # Meta endpoints (e.g. principal dispatch) want any live SP
            # through; auth-level checks already happened.
            return None
        return Response(
            {"error": "Endpoint does not declare resource_type for SP authz."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Project-scoped resources require a project id; without it the grant
    # step has nothing to anchor on.
    if level.upper() == "PROJECT" and not project_id:
        return Response(
            _DENY_BODY,
            status=status.HTTP_403_FORBIDDEN,
        )

    action = _method_to_action(method)
    ctx = AuthzContext(workspace_slug=slug, project_id=project_id)
    try:
        decision = authorize(
            principal,
            action,
            resource_type,
            ctx=ctx,
        )
    except Exception:
        # Auth failures on the SP side already raise PermissionDenied via
        # enforce(); swallow other errors and deny with the standard body.
        return Response(_DENY_BODY, status=status.HTTP_403_FORBIDDEN)

    if not decision.allowed:
        # Mirror ``enforce``'s PermissionDenied shape without exposing the
        # reason codes to a caller that should never see them.
        return Response(_DENY_BODY, status=status.HTTP_403_FORBIDDEN)
    return None


def allow_permission(
    allowed_roles,
    level: str = "PROJECT",
    creator: bool = False,
    model=None,
    resource_type: str | None = None,
    sp_fallback: bool = False,
):
    """Decorate a method to gate it on role membership (humans) or on
    :func:`plane.core.authz.authorize` (service principals).

    Args:
        allowed_roles: Roles accepted for human principals. ROLE enum
            members or ints.
        level: ``"PROJECT"`` or ``"WORKSPACE"``. Determines which membership
            table the human path checks.
        creator: When True, only the row's ``created_by`` user is allowed
            (humans only — SP tokens never satisfy the creator check
            because they have no User row to compare against).
        model: Required when ``creator`` is True.
        resource_type: Optional authz resource type. Required for an SP
            request to be authorized; humans ignore it.
        sp_fallback: When True, an SP request without ``resource_type``
            still passes. Used by meta endpoints that don't fit a single
            resource type (e.g. principal dispatch).
    """

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(instance, request, *args, **kwargs):
            principal = principal_from_request(request)

            # Service-principal path ------------------------------------------------
            if isinstance(principal, ServicePrincipalAuthz):
                slug = kwargs.get("slug")
                project_id = kwargs.get("project_id")
                resp = _authorize_sp(
                    principal,
                    level=level,
                    resource_type=resource_type,
                    method=request.method,
                    slug=slug,
                    project_id=project_id,
                    sp_fallback=sp_fallback,
                )
                if resp is not None:
                    return resp
                return view_func(instance, request, *args, **kwargs)

            # Human path ------------------------------------------------------------
            user_principal = (
                principal if isinstance(principal, UserPrincipal) else None
            )
            if user_principal is None:
                return Response(_DENY_BODY, status=status.HTTP_403_FORBIDDEN)
            user = user_principal.user

            # Check for creator if required
            if creator and model:
                # check if the user is part of the workspace or not
                if not WorkspaceMember.objects.filter(
                    member=user,
                    workspace__slug=kwargs["slug"],
                    is_active=True,
                ).exists():
                    return Response(_DENY_BODY, status=status.HTTP_403_FORBIDDEN)

                obj = model.objects.filter(id=kwargs["pk"], created_by=user).exists()
                if obj:
                    return view_func(instance, request, *args, **kwargs)

            # Convert allowed_roles to their values if they are enum members
            allowed_role_values = [role.value if isinstance(role, ROLE) else role for role in allowed_roles]

            # Check role permissions
            if level == "WORKSPACE":
                if WorkspaceMember.objects.filter(
                    member=user,
                    workspace__slug=kwargs["slug"],
                    role__in=allowed_role_values,
                    is_active=True,
                ).exists():
                    return view_func(instance, request, *args, **kwargs)
            else:
                is_user_has_allowed_role = ProjectMember.objects.filter(
                    member=user,
                    workspace__slug=kwargs["slug"],
                    project_id=kwargs["project_id"],
                    role__in=allowed_role_values,
                    is_active=True,
                ).exists()

                # Return if the user has the allowed role else if they are workspace admin and part of the project regardless of the role # noqa: E501
                if is_user_has_allowed_role:
                    return view_func(instance, request, *args, **kwargs)
                elif (
                    ProjectMember.objects.filter(
                        member=user,
                        workspace__slug=kwargs["slug"],
                        project_id=kwargs["project_id"],
                        is_active=True,
                    ).exists()
                    and WorkspaceMember.objects.filter(
                        member=user,
                        workspace__slug=kwargs["slug"],
                        role=ROLE.ADMIN.value,
                        is_active=True,
                    ).exists()
                ):
                    return view_func(instance, request, *args, **kwargs)

            # Return permission denied if no conditions are met
            return Response(_DENY_BODY, status=status.HTTP_403_FORBIDDEN)

        return _wrapped_view

    return decorator
