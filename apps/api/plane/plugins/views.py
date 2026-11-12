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

from django.db.models import Max
from rest_framework import status
from rest_framework.response import Response

from plane.app.permissions import ROLE, allow_permission
from plane.app.views.base import BaseAPIView
from plane.db.models import Workspace

from .audit import actor_from_request
from .constants import AUDIT_ACTION_CHOICES, AuditAction
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

#: Cursor metadata for the audit listing. The body stays the plain JSON list it
#: has always been (rows and envelope unchanged), so a reader that ignores the
#: headers still gets a correct, newest-first page; the headers only say how to
#: ask for the next (older) one. ``X-Audit-Next-Cursor`` carries the value to
#: send back as ``?before_id=`` and is absent when the page is the last.
AUDIT_NEXT_CURSOR_HEADER = "X-Audit-Next-Cursor"
AUDIT_HAS_MORE_HEADER = "X-Audit-Has-More"


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
    """Read-only audit listing, newest first, cursor-paginated.

    Ordering and continuation:

    * rows come back **newest first** (``-id``). ``id`` is monotonic on an
      append-only table, so it is the only ordering key needed — the oldest
      row can no longer crowd the newest one out of the page;
    * ``?before_id=<id>`` is an **exclusive** upper bound: the next page is
      every row with ``id < before_id``. Nothing is ever deleted and ids only
      grow, so a row written between two pages lands *above* every cursor
      already handed out: it shows up in a fresh first page, never as a
      repeat of an older page and never as a gap in the older suffix. A row
      whose insert commits *after* the reader has paged past its id is the one
      exception — the walk is a view of what was committed when each page was
      read, not a snapshot — and it too surfaces in a fresh first page;
    * ``?limit=`` is clamped to ``[1, AUDIT_MAX_LIMIT]`` (one row beyond the
      page is fetched so ``has_more`` is exact, not guessed from fullness);
    * page metadata travels in ``X-Audit-Next-Cursor`` (the ``before_id`` to
      send for the next page, absent on the last page) and
      ``X-Audit-Has-More``. The body is the same JSON list as before.

    Filters: ``?key=<plugin>`` (unchanged), ``?action=<action>``, and
    ``?distinct=key``, which collapses the filtered trail to one row per
    plugin key — the newest event of that subset per key. So
    ``?action=uninstall&distinct=key`` answers "which keys were ever
    uninstalled" with one bounded, cursor-paged answer per request instead of
    a client-side scan over a fixed slice. The per-key newest event is
    computed by a server-side ``GROUP BY`` over the whole (workspace-scoped)
    trail and the cursor applies to that aggregate, so the inventory cannot
    silently stop at a slice of any size.
    """

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug):
        events = PluginAuditEvent.objects.filter(workspace_id=_workspace(slug).id)

        key = request.query_params.get("key")
        if key:
            events = events.filter(plugin_key=key)

        action = request.query_params.get("action")
        if action in (None, ""):
            action = None
        elif action not in dict(AUDIT_ACTION_CHOICES):
            return Response(
                {"error": "action must be one of: %s" % ", ".join(dict(AUDIT_ACTION_CHOICES))},
                status=status.HTTP_400_BAD_REQUEST,
            )
        else:
            events = events.filter(action=action)

        distinct = request.query_params.get("distinct")
        if distinct in (None, ""):
            distinct = None
        elif distinct != "key":
            return Response({"error": "distinct must be 'key'"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            limit = int(request.query_params.get("limit", AUDIT_DEFAULT_LIMIT))
        except (TypeError, ValueError):
            return Response({"error": "limit must be an integer"}, status=status.HTTP_400_BAD_REQUEST)
        limit = max(1, min(limit, AUDIT_MAX_LIMIT))

        before_id = None
        raw_before_id = request.query_params.get("before_id")
        if raw_before_id not in (None, ""):
            try:
                before_id = int(raw_before_id)
            except (TypeError, ValueError):
                return Response(
                    {"error": "before_id must be an integer"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if before_id < 1:
                return Response(
                    {"error": "before_id must be a positive integer"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if distinct == "key":
            page, has_more = self._distinct_by_key_page(events, limit, before_id)
        else:
            if before_id is not None:
                events = events.filter(id__lt=before_id)
            page = list(events.order_by("-id")[: limit + 1])
            has_more = len(page) > limit
            page = page[:limit]

        response = Response(PluginAuditEventSerializer(page, many=True).data, status=status.HTTP_200_OK)
        response[AUDIT_HAS_MORE_HEADER] = "true" if has_more else "false"
        if has_more and page:
            response[AUDIT_NEXT_CURSOR_HEADER] = str(page[-1].id)
        return response

    @staticmethod
    def _distinct_by_key_page(events, limit, before_id):
        """One row per key: the newest event of ``events`` for that key.

        The aggregate (``MAX(id)`` keyed by ``plugin_key``) is evaluated by
        Postgres over the whole filtered trail; ``before_id`` filters the
        aggregate, not the raw rows, so paging this inventory has the same
        no-gap/no-repeat property as paging the trail itself.
        """
        latest = events.values("plugin_key").annotate(last_id=Max("id")).order_by("-last_id")
        if before_id is not None:
            latest = latest.filter(last_id__lt=before_id)
        rows = list(latest[: limit + 1])
        has_more = len(rows) > limit
        ids = [row["last_id"] for row in rows[:limit]]
        by_id = {event.id: event for event in PluginAuditEvent.objects.filter(id__in=ids)}
        return [by_id[event_id] for event_id in ids if event_id in by_id], has_more
