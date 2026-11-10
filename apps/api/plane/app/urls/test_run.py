# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.views.test_run import (
    TestRunAttachmentEndpoint,
    TestRunCaseDetailEndpoint,
    TestRunCaseEndpoint,
    TestRunFinalizeEndpoint,
    TestRunStepAttachmentEndpoint,
    TestRunStepRecordEndpoint,
    TestRunViewSet,
)


urlpatterns = [
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-runs/",
        TestRunViewSet.as_view({"get": "list", "post": "create"}),
        name="test-runs",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-runs/<uuid:pk>/",
        TestRunViewSet.as_view(
            {
                "get": "retrieve",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="test-runs",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-runs/<uuid:test_run_id>/cases/",
        TestRunCaseEndpoint.as_view(),
        name="test-run-cases",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-runs/<uuid:test_run_id>/cases/<uuid:run_case_id>/",
        TestRunCaseDetailEndpoint.as_view(),
        name="test-run-cases",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-runs/<uuid:test_run_id>/cases/<uuid:run_case_id>/attachments/",
        TestRunAttachmentEndpoint.as_view(),
        name="test-run-attachments",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-runs/<uuid:test_run_id>/cases/<uuid:run_case_id>/attachments/<uuid:asset_id>/",
        TestRunAttachmentEndpoint.as_view(),
        name="test-run-attachments",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-runs/<uuid:test_run_id>/cases/<uuid:run_case_id>/steps/<int:sequence_id>/",
        TestRunStepRecordEndpoint.as_view(),
        name="test-run-step-records",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-runs/<uuid:test_run_id>/cases/<uuid:run_case_id>/steps/<int:sequence_id>/attachments/",
        TestRunStepAttachmentEndpoint.as_view(),
        name="test-run-step-attachments",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-runs/<uuid:test_run_id>/cases/<uuid:run_case_id>/steps/<int:sequence_id>/attachments/<uuid:asset_id>/",
        TestRunStepAttachmentEndpoint.as_view(),
        name="test-run-step-attachments",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/test-runs/<uuid:test_run_id>/finalize/",
        TestRunFinalizeEndpoint.as_view(),
        name="test-run-finalize",
    ),
]
