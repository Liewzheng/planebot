# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from .views import (
    ServicePrincipalDetailAPIEndpoint,
    ServicePrincipalGrantAPIEndpoint,
    ServicePrincipalListCreateAPIEndpoint,
    ServicePrincipalRotateTokenAPIEndpoint,
    ServicePrincipalScopeAPIEndpoint,
    WorkspaceSPSettingsAPIEndpoint,
)

urlpatterns = [
    path(
        "workspaces/<str:slug>/service-principals/",
        ServicePrincipalListCreateAPIEndpoint.as_view(),
        name="service-principals",
    ),
    path(
        "workspaces/<str:slug>/service-principals/<uuid:pk>/",
        ServicePrincipalDetailAPIEndpoint.as_view(),
        name="service-principals-detail",
    ),
    path(
        "workspaces/<str:slug>/service-principals/<uuid:pk>/scopes/",
        ServicePrincipalScopeAPIEndpoint.as_view(),
        name="service-principals-scopes",
    ),
    path(
        "workspaces/<str:slug>/service-principals/<uuid:pk>/grants/",
        ServicePrincipalGrantAPIEndpoint.as_view(),
        name="service-principals-grants",
    ),
    path(
        "workspaces/<str:slug>/service-principals/<uuid:pk>/rotate-token/",
        ServicePrincipalRotateTokenAPIEndpoint.as_view(),
        name="service-principals-rotate-token",
    ),
    path(
        "workspaces/<str:slug>/sp-settings/",
        WorkspaceSPSettingsAPIEndpoint.as_view(),
        name="workspace-sp-settings",
    ),
]