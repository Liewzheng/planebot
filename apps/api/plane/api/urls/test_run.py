# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The URLs of the batch side of the test-case feature on the v1 surface.

The paths mirror the app surface one to one — only the prefix differs
(``/api/v1/…`` instead of ``/api/…``) — so a client that knows one of
them knows the other.
"""

from django.urls import path

from plane.api.views.test_run import (
    TestRunAttachmentEndpoint,
    TestRunCaseDetailEndpoint,
    TestRunCaseEndpoint,
    TestRunDetailEndpoint,
    TestRunFinalizeEndpoint,
    TestRunListCreateEndpoint,
    TestRunStepAttachmentEndpoint,
    TestRunStepRecordEndpoint,
)


urlpatterns = [
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-runs/",
        TestRunListCreateEndpoint.as_view(http_method_names=["get", "post"]),
        name="test-runs",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-runs/<uuid:pk>/",
        TestRunDetailEndpoint.as_view(http_method_names=["get", "patch", "delete"]),
        name="test-runs",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-runs/<uuid:test_run_id>/cases/",
        TestRunCaseEndpoint.as_view(http_method_names=["get", "post"]),
        name="test-run-cases",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-runs/<uuid:test_run_id>/cases/<uuid:run_case_id>/",
        TestRunCaseDetailEndpoint.as_view(http_method_names=["get", "patch", "delete"]),
        name="test-run-cases",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-runs/<uuid:test_run_id>/cases/<uuid:run_case_id>/attachments/",
        TestRunAttachmentEndpoint.as_view(http_method_names=["get", "post"]),
        name="test-run-attachments",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-runs/<uuid:test_run_id>/cases/<uuid:run_case_id>/attachments/<uuid:asset_id>/",
        TestRunAttachmentEndpoint.as_view(http_method_names=["delete"]),
        name="test-run-attachments",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-runs/<uuid:test_run_id>/cases/<uuid:run_case_id>/steps/<int:sequence_id>/",
        TestRunStepRecordEndpoint.as_view(http_method_names=["get", "patch"]),
        name="test-run-step-records",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-runs/<uuid:test_run_id>/cases/<uuid:run_case_id>/steps/<int:sequence_id>/attachments/",
        TestRunStepAttachmentEndpoint.as_view(http_method_names=["get", "post"]),
        name="test-run-step-attachments",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-runs/<uuid:test_run_id>/cases/<uuid:run_case_id>/steps/<int:sequence_id>/attachments/<uuid:asset_id>/",
        TestRunStepAttachmentEndpoint.as_view(http_method_names=["delete"]),
        name="test-run-step-attachments",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-runs/<uuid:test_run_id>/finalize/",
        TestRunFinalizeEndpoint.as_view(http_method_names=["post"]),
        name="test-run-finalize",
    ),
]
