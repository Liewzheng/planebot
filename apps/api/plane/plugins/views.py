# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Plugin registry management endpoints (design §6 and §10).

Surface:

* ``GET  /api/workspaces/<slug>/plugins/``                    list
* ``POST /api/workspaces/<slug>/plugins/``                    install
* ``POST /api/workspaces/<slug>/plugins/import/``              import (manifest only)
* ``POST /api/workspaces/<slug>/plugins/<key>/enable/``        enable
* ``POST /api/workspaces/<slug>/plugins/<key>/disable/``       disable
* ``POST /api/workspaces/<slug>/plugins/<key>/uninstall/``     uninstall (soft delete)
* ``GET  /api/workspaces/<slug>/plugins/<key>/manifest/``      manifest export
* ``GET  /api/workspaces/<slug>/plugins/audit/``               read-only audit listing

Authorization: workspace-scoped. Writes are admin-only; reads of the plugin
list / manifest are allowed to workspace members, the audit trail is
admin-only. Every endpoint inherits ``BaseAPIView``'s ``IsAuthenticatedNoSP``
permission, so a service-principal token is denied before the body runs —
plugins are deliberately a human-admin surface (design §9).
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response

from plane.app.permissions import ROLE, allow_permission
from plane.app.views.base import BaseAPIView
from plane.db.models import Workspace

from .audit import actor_from_request
from .constants import AuditAction
from .contract import KERNEL_CONTRACT_HASH
from .errors import PluginRegistryError
from .models import Plugin, PluginAuditEvent
from .registry import (
    disable_plugin,
    enable_plugin,
    get_plugin,
    install_manifest,
    uninstall_plugin,
)
from .serializers import (
    PluginAuditEventSerializer,
    PluginInstallSerializer,
    PluginSerializer,
    PluginUninstallSerializer,
)

AUDIT_DEFAULT_LIMIT = 200
AUDIT_MAX_LIMIT = 500


def _request_id(request) -> str:
    return (request.headers.get("X-Request-ID") or "")[:64]


def _workspace(slug):
    return Workspace.objects.get(slug=slug)


def _error(exc: PluginRegistryError) -> Response:
    return Response(exc.as_body(), status=exc.http_status)


class PluginListCreateAPIEndpoint(BaseAPIView):
    """List installed plugins / install one from its manifest."""

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        plugins = Plugin.objects.filter(workspace__slug=slug)
        return Response(PluginSerializer(plugins, many=True).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug):
        serializer = PluginInstallSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        try:
            plugin = install_manifest(
                workspace=_workspace(slug),
                manifest=serializer.validated_data["manifest"],
                actor=actor_from_request(request),
                action=AuditAction.INSTALL,
                request_id=_request_id(request),
            )
        except PluginRegistryError as exc:
            return _error(exc)
        return Response(PluginSerializer(plugin).data, status=status.HTTP_201_CREATED)


class PluginImportAPIEndpoint(BaseAPIView):
    """Import a manifest that came from outside the kernel.

    Same validation and lifecycle as install — the differences are the audit
    action (``import``) and that the manifest schema has no member able to
    carry code: v0 runs no plugin code, and a ``kind=package`` manifest is
    refused with ``runtime_unavailable`` on this path too.
    """

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug):
        serializer = PluginInstallSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        try:
            plugin = install_manifest(
                workspace=_workspace(slug),
                manifest=serializer.validated_data["manifest"],
                actor=actor_from_request(request),
                action=AuditAction.IMPORT,
                request_id=_request_id(request),
            )
        except PluginRegistryError as exc:
            return _error(exc)
        return Response(PluginSerializer(plugin).data, status=status.HTTP_201_CREATED)


class PluginEnableAPIEndpoint(BaseAPIView):
    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug, key):
        try:
            plugin = enable_plugin(
                get_plugin(_workspace(slug), key),
                actor=actor_from_request(request),
                request_id=_request_id(request),
            )
        except PluginRegistryError as exc:
            return _error(exc)
        return Response(PluginSerializer(plugin).data, status=status.HTTP_200_OK)


class PluginDisableAPIEndpoint(BaseAPIView):
    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug, key):
        try:
            plugin = disable_plugin(
                get_plugin(_workspace(slug), key),
                actor=actor_from_request(request),
                request_id=_request_id(request),
            )
        except PluginRegistryError as exc:
            return _error(exc)
        return Response(PluginSerializer(plugin).data, status=status.HTTP_200_OK)


class PluginUninstallAPIEndpoint(BaseAPIView):
    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug, key):
        serializer = PluginUninstallSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        try:
            plugin = uninstall_plugin(
                get_plugin(_workspace(slug), key),
                confirm=serializer.validated_data["confirm"],
                reason=serializer.validated_data["reason"],
                actor=actor_from_request(request),
                request_id=_request_id(request),
            )
        except PluginRegistryError as exc:
            return _error(exc)
        return Response(PluginSerializer(plugin).data, status=status.HTTP_200_OK)


class PluginManifestAPIEndpoint(BaseAPIView):
    """Export the stored manifest (read-only evidence, not a re-download)."""

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug, key):
        try:
            plugin = get_plugin(_workspace(slug), key)
        except PluginRegistryError as exc:
            return _error(exc)
        return Response(
            {
                "manifest": plugin.manifest,
                "manifest_hash": plugin.manifest_hash,
                "contract_hash": plugin.contract_hash,
                "kernel_contract_hash": KERNEL_CONTRACT_HASH,
                "contract_drifted": plugin.contract_drifted,
            },
            status=status.HTTP_200_OK,
        )


class PluginAuditListAPIEndpoint(BaseAPIView):
    """Read-only audit listing, oldest first (monotonic ids)."""

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug):
        events = PluginAuditEvent.objects.filter(workspace_id=_workspace(slug).id)
        key = request.query_params.get("key")
        if key:
            events = events.filter(plugin_key=key)

        try:
            limit = int(request.query_params.get("limit", AUDIT_DEFAULT_LIMIT))
        except (TypeError, ValueError):
            return Response(
                {"error": "limit must be an integer"}, status=status.HTTP_400_BAD_REQUEST
            )
        limit = max(1, min(limit, AUDIT_MAX_LIMIT))
        return Response(
            PluginAuditEventSerializer(events[:limit], many=True).data,
            status=status.HTTP_200_OK,
        )
