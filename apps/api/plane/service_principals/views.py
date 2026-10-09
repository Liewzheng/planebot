# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""SP management endpoints.

These endpoints model the management surface only — authz wiring through
``authorize()`` lands in later missions. Routes mirror the ai_accounts shape
so the migration path is straightforward, with the route prefix switched from
``ai-accounts`` to ``service-principals`` and token strings prefixed
``plane_svc_`` instead of ``plane_api_``.
"""

from django.db import transaction
from rest_framework import status
from rest_framework.response import Response

from plane.app.permissions import ROLE, allow_permission
from plane.app.views.base import BaseAPIView
from plane.app.views.user.mfa import check_step_up_totp
from plane.authentication.rate_limit import StepUpThrottle
from plane.db.models import APIToken, Workspace
from plane.db.models.api import generate_service_token

from .constants import PrincipalType
from .models import ProjectGrant, ServicePrincipal, ServiceScope, WorkspaceSPSettings
from .serializers import (
    ProjectGrantInputSerializer,
    ProjectGrantSerializer,
    ServicePrincipalCreateSerializer,
    ServicePrincipalSerializer,
    ServicePrincipalUpdateSerializer,
    ServiceScopeInputSerializer,
    ServiceScopeSerializer,
    WorkspaceSPSettingsInputSerializer,
    WorkspaceSPSettingsSerializer,
)


def _get_sp(slug, pk):
    return ServicePrincipal.objects.select_related("owner", "workspace").get(
        pk=pk, workspace__slug=slug
    )


def _reject_if_sp_token(request):
    """Return 403 if the request carried a service-principal API token.

    Session-cookie admins (the human UI path) never set an SP token, so this
    is a no-op for them. Three legitimate surfaces can carry a token:

    * ``request.auth`` is the resolved ``APIToken`` instance (when the request
      ran through ``APIKeyAuthentication`` and that path is wired in);
    * ``request.auth`` is the token string (the current shape returned by
      ``validate_api_token``);
    * the raw ``X-Api-Key`` header is present without APIKeyAuthentication
      running yet (defensive header probing / integration tests; M8/M9 will
      close that loop).

    Each is checked against the ``APIToken.principal_type`` column. ``None``
    is returned when the caller has no service-token signal, otherwise a 403
    Response.
    """
    api_token = None
    auth = getattr(request, "auth", None)
    if isinstance(auth, APIToken):
        api_token = auth
    elif isinstance(auth, str) and auth:
        api_token = APIToken.objects.filter(token=auth).first()
    else:
        header_token = None
        if hasattr(request, "headers") and request.headers is not None:
            header_token = request.headers.get("X-Api-Key")
        if header_token:
            api_token = APIToken.objects.filter(token=header_token).first()
    if api_token and api_token.principal_type == PrincipalType.SERVICE:
        return Response(
            {"error": "Service principals cannot manage workspace SP settings"},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


class ServicePrincipalListCreateAPIEndpoint(BaseAPIView):
    """GET (list) / POST (create) service principals in a workspace."""

    def get_throttles(self):
        # Only creation is step-up verified — the resulting ``token`` string
        # is shown exactly once, so the step-up gates a live credential.
        if self.request.method == "POST":
            return [StepUpThrottle()]
        return super().get_throttles()

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug):
        sps = (
            ServicePrincipal.objects.filter(workspace__slug=slug)
            .select_related("owner", "workspace")
            .prefetch_related("scopes", "grants")
        )
        return Response(
            ServicePrincipalSerializer(sps, many=True).data,
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug):
        mfa_error = check_step_up_totp(request)
        if mfa_error:
            return mfa_error

        serializer = ServicePrincipalCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        name = serializer.validated_data["name"]
        description = serializer.validated_data["description"]
        avatar = serializer.validated_data["avatar"]

        workspace = Workspace.objects.get(slug=slug)
        with transaction.atomic():
            sp = ServicePrincipal.objects.create(
                workspace=workspace,
                owner=request.user,
                name=name,
                description=description,
                avatar=avatar,
            )
            token = APIToken.objects.create(
                user=request.user,
                label=f"svc:{name}",
                user_type=1,
                is_service=True,
                principal_type=PrincipalType.SERVICE,
                service_principal=sp,
                workspace=workspace,
                token=generate_service_token(),
            )

        data = ServicePrincipalSerializer(sp).data
        # The service token secret is returned exactly once, on creation.
        data["token"] = token.token
        return Response(data, status=status.HTTP_201_CREATED)


class ServicePrincipalDetailAPIEndpoint(BaseAPIView):
    """GET / PATCH / DELETE a single service principal."""

    def get_throttles(self):
        if self.request.method == "DELETE":
            return [StepUpThrottle()]
        return super().get_throttles()

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug, pk):
        sp = _get_sp(slug, pk)
        return Response(
            ServicePrincipalSerializer(sp).data,
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def patch(self, request, slug, pk):
        serializer = ServicePrincipalUpdateSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        sp = _get_sp(slug, pk)
        with transaction.atomic():
            for field in ("name", "description", "avatar", "is_active"):
                if field in serializer.validated_data:
                    setattr(sp, field, serializer.validated_data[field])
            sp.save()

            # Toggling the SP toggles its service tokens with it.
            if "is_active" in serializer.validated_data:
                APIToken.objects.filter(
                    service_principal=sp,
                    principal_type=PrincipalType.SERVICE,
                ).update(is_active=serializer.validated_data["is_active"])
        return Response(
            ServicePrincipalSerializer(sp).data,
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def delete(self, request, slug, pk):
        mfa_error = check_step_up_totp(request)
        if mfa_error:
            return mfa_error

        sp = _get_sp(slug, pk)
        with transaction.atomic():
            APIToken.objects.filter(
                service_principal=sp,
                principal_type=PrincipalType.SERVICE,
            ).update(is_active=False)
            sp.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ServicePrincipalScopeAPIEndpoint(BaseAPIView):
    """GET / PUT the SP's scope set.

    PUT replaces the entire scope set (same shape as ai_accounts scopes).
    Workspace-wide scope rows have project=None.
    """

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug, pk):
        sp = _get_sp(slug, pk)
        scopes = ServiceScope.objects.filter(service_principal=sp)
        return Response(
            ServiceScopeSerializer(scopes, many=True).data,
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def put(self, request, slug, pk):
        sp = _get_sp(slug, pk)
        items = request.data.get("scopes", [])
        if not isinstance(items, list):
            return Response(
                {"error": "scopes must be a list"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = ServiceScopeInputSerializer(data=items, many=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # All referenced projects must belong to this workspace.
        project_ids = [i["project"] for i in serializer.validated_data if i["project"]]
        if project_ids:
            valid = sp.workspace.workspace_project.filter(id__in=project_ids).count()
            if valid != len(set(project_ids)):
                return Response(
                    {"error": "All projects must belong to this workspace"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        with transaction.atomic():
            ServiceScope.objects.filter(service_principal=sp).delete()
            ServiceScope.objects.bulk_create(
                [
                    ServiceScope(
                        service_principal=sp,
                        project_id=item["project"],
                        resource_type=item["resource_type"],
                        action=item["action"],
                    )
                    for item in serializer.validated_data
                ]
            )
        scopes = ServiceScope.objects.filter(service_principal=sp)
        return Response(
            ServiceScopeSerializer(scopes, many=True).data,
            status=status.HTTP_200_OK,
        )


class ServicePrincipalGrantAPIEndpoint(BaseAPIView):
    """GET / PUT the SP's project grants.

    PUT replaces the entire grant set. role_cap mirrors the existing Plane
    role integers (20/15/5). Default-deny: a project with no grant means the
    SP cannot reach it.
    """

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug, pk):
        sp = _get_sp(slug, pk)
        grants = ProjectGrant.objects.filter(service_principal=sp)
        return Response(
            ProjectGrantSerializer(grants, many=True).data,
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def put(self, request, slug, pk):
        sp = _get_sp(slug, pk)
        items = request.data.get("grants", [])
        if not isinstance(items, list):
            return Response(
                {"error": "grants must be a list"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = ProjectGrantInputSerializer(data=items, many=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # All referenced projects must belong to this workspace.
        project_ids = [i["project"] for i in serializer.validated_data]
        if project_ids:
            valid = sp.workspace.workspace_project.filter(id__in=project_ids).count()
            if valid != len(set(project_ids)):
                return Response(
                    {"error": "All projects must belong to this workspace"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        with transaction.atomic():
            ProjectGrant.objects.filter(service_principal=sp).delete()
            ProjectGrant.objects.bulk_create(
                [
                    ProjectGrant(
                        service_principal=sp,
                        project_id=item["project"],
                        role_cap=item["role_cap"],
                        is_active=item["is_active"],
                    )
                    for item in serializer.validated_data
                ]
            )
        grants = ProjectGrant.objects.filter(service_principal=sp)
        return Response(
            ProjectGrantSerializer(grants, many=True).data,
            status=status.HTTP_200_OK,
        )


class ServicePrincipalRotateTokenAPIEndpoint(BaseAPIView):
    """Revoke every existing service token and issue a fresh one.

    Step-up gated: rotation exposes a fresh secret, so the TOTP check is the
    same as on creation.
    """

    throttle_classes = [StepUpThrottle]

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug, pk):
        mfa_error = check_step_up_totp(request)
        if mfa_error:
            return mfa_error

        sp = _get_sp(slug, pk)
        if not sp.is_active:
            return Response(
                {"error": "Cannot rotate the token of an inactive service principal"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            APIToken.objects.filter(
                service_principal=sp,
                principal_type=PrincipalType.SERVICE,
                is_active=True,
            ).update(is_active=False)
            token = APIToken.objects.create(
                user=sp.owner,
                label=f"svc:{sp.name}",
                user_type=1,
                is_service=True,
                principal_type=PrincipalType.SERVICE,
                service_principal=sp,
                workspace=sp.workspace,
                token=generate_service_token(),
            )

        data = ServicePrincipalSerializer(sp).data
        # The new service token secret is returned exactly once.
        data["token"] = token.token
        return Response(data, status=status.HTTP_200_OK)


class WorkspaceSPSettingsAPIEndpoint(BaseAPIView):
    """GET / PATCH the workspace's SP toggles (currently just ``sp_assignable``).

    The endpoint is a per-workspace configuration switch (like other general
    settings), not a credential operation — it is therefore not step-up TOTP
    gated. Workspace-admin-only access is enforced by ``allow_permission``;
    SP principal tokens (``principal_type=SERVICE``) are rejected by
    ``_reject_if_sp_token`` so an SP cannot toggle its own visibility in the
    assignee / filter / mention pickers.

    GET is lazy: when no ``WorkspaceSPSettings`` row exists yet the model
    defaults are returned (``{"sp_assignable": False}``) without persisting a
    row — the frontend (M11) is wired against the same convention so the UI
    sees the model default as a 200, not a 404.

    PATCH upserts via ``get_or_create(workspace=...)`` so a workspace stays
    pinned to a single row even when callers PATCH the same flag repeatedly.
    """

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug):
        rejected = _reject_if_sp_token(request)
        if rejected is not None:
            return rejected

        workspace = Workspace.objects.get(slug=slug)
        settings_row = WorkspaceSPSettings.objects.filter(workspace=workspace).first()
        return Response(
            WorkspaceSPSettingsSerializer(settings_row).data,
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def patch(self, request, slug):
        rejected = _reject_if_sp_token(request)
        if rejected is not None:
            return rejected

        serializer = WorkspaceSPSettingsInputSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        workspace = Workspace.objects.get(slug=slug)
        with transaction.atomic():
            settings_row, _created = WorkspaceSPSettings.objects.get_or_create(
                workspace=workspace,
            )
            settings_row.sp_assignable = serializer.validated_data["sp_assignable"]
            settings_row.save()

        return Response(
            WorkspaceSPSettingsSerializer(settings_row).data,
            status=status.HTTP_200_OK,
        )