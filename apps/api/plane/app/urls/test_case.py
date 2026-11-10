# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.views.test_case import (
    TestCaseIssueDetailEndpoint,
    TestCaseIssueEndpoint,
    TestCaseModuleEndpoint,
    TestCaseStepEndpoint,
    TestCaseViewSet,
)


urlpatterns = [
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-case-modules/",
        TestCaseModuleEndpoint.as_view(),
        name="test-case-modules",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-cases/",
        TestCaseViewSet.as_view({"get": "list", "post": "create"}),
        name="test-cases",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-cases/<uuid:pk>/",
        TestCaseViewSet.as_view(
            {
                "get": "retrieve",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="test-cases",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-cases/<uuid:test_case_id>/steps/",
        TestCaseStepEndpoint.as_view(),
        name="test-case-steps",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-cases/<uuid:test_case_id>/issues/",
        TestCaseIssueEndpoint.as_view(),
        name="test-case-issues",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-cases/<uuid:test_case_id>/issues/<uuid:issue_id>/",
        TestCaseIssueDetailEndpoint.as_view(),
        name="test-case-issues",
    ),
]
