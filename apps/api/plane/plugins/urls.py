# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Plugin registry routes.

Static segments (``import/``, ``audit/``) are declared before the ``<str:key>``
routes so they are never swallowed as a plugin key.
"""

from django.urls import path

from .views import (
    PluginAuditListAPIEndpoint,
    PluginDisableAPIEndpoint,
    PluginEnableAPIEndpoint,
    PluginImportAPIEndpoint,
    PluginListCreateAPIEndpoint,
    PluginManifestAPIEndpoint,
    PluginUninstallAPIEndpoint,
)

urlpatterns = [
    path(
        "workspaces/<str:slug>/plugins/",
        PluginListCreateAPIEndpoint.as_view(),
        name="plugins",
    ),
    path(
        "workspaces/<str:slug>/plugins/import/",
        PluginImportAPIEndpoint.as_view(),
        name="plugins-import",
    ),
    path(
        "workspaces/<str:slug>/plugins/audit/",
        PluginAuditListAPIEndpoint.as_view(),
        name="plugins-audit",
    ),
    path(
        "workspaces/<str:slug>/plugins/<str:key>/enable/",
        PluginEnableAPIEndpoint.as_view(),
        name="plugins-enable",
    ),
    path(
        "workspaces/<str:slug>/plugins/<str:key>/disable/",
        PluginDisableAPIEndpoint.as_view(),
        name="plugins-disable",
    ),
    path(
        "workspaces/<str:slug>/plugins/<str:key>/uninstall/",
        PluginUninstallAPIEndpoint.as_view(),
        name="plugins-uninstall",
    ),
    path(
        "workspaces/<str:slug>/plugins/<str:key>/manifest/",
        PluginManifestAPIEndpoint.as_view(),
        name="plugins-manifest",
    ),
]
