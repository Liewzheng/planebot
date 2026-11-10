# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The URLs of the test-case feature on the v1 surface.

The paths mirror the app surface one to one — only the prefix differs
(``/api/v1/…`` instead of ``/api/…``) — so a client that knows one of
them knows the other.
"""

from django.urls import path

from plane.api.views.test_case import (
    TestCaseDetailEndpoint,
    TestCaseIssueDetailEndpoint,
    TestCaseIssueEndpoint,
    TestCaseListCreateEndpoint,
    TestCaseModuleEndpoint,
    TestCaseStepEndpoint,
)


urlpatterns = [
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-case-modules/",
        TestCaseModuleEndpoint.as_view(http_method_names=["get", "post"]),
        name="test-case-modules",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-cases/",
        TestCaseListCreateEndpoint.as_view(http_method_names=["get", "post"]),
        name="test-cases",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-cases/<uuid:pk>/",
        TestCaseDetailEndpoint.as_view(http_method_names=["get", "patch", "delete"]),
        name="test-cases",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-cases/<uuid:test_case_id>/steps/",
        TestCaseStepEndpoint.as_view(http_method_names=["get", "put"]),
        name="test-case-steps",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-cases/<uuid:test_case_id>/issues/",
        TestCaseIssueEndpoint.as_view(http_method_names=["get", "post"]),
        name="test-case-issues",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-cases/<uuid:test_case_id>/issues/<uuid:issue_id>/",
        TestCaseIssueDetailEndpoint.as_view(http_method_names=["delete"]),
        name="test-case-issues",
    ),
]
