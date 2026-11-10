# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract test: a body that is not a JSON object is a refused payload.

``request.data`` of a JSON body that is not an object **is** that value
(a list, a string, a number) and has no ``get``. The writes that read
the body directly (``request.data.get(...)``) therefore raised an
``AttributeError`` and answered the generic 500 of the app layer, while
the writes that go through a serializer answered the documented 400
``invalid_payload`` — the feature was inconsistent with itself.

Every write of the feature answers the same envelope now, whatever the
body looks like. The test drives each of them with three malformed
bodies and asserts the answer.
"""

from __future__ import annotations

import pytest
from rest_framework import status

from plane.db.models import (
    FileAsset,
    Project,
    ProjectMember,
    TestCase,
    TestCaseModule,
    TestRun,
    TestRunCase,
    TestRunStepRecord,
)


MALFORMED_BODIES = ([1, 2], "abc", 7, True)


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="Body Shape Project",
        identifier="BSP",
        workspace=workspace,
        created_by=create_user,
        network=2,
    )
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    return project


@pytest.fixture
def module(db, project):
    return TestCaseModule.objects.create(
        project=project,
        workspace=project.workspace,
        code="CLI",
        description="The command line interface of pbot",
    )


@pytest.fixture
def case(db, project, module, create_user):
    case = TestCase.objects.create(
        project=project,
        module=module,
        title="Log in with a valid account",
        purpose="Make sure the login path of pbot works",
        object_overview="The login form of the desktop client",
        priority="P1",
        preconditions="The account exists and is active",
        environment="Windows 10, pbot 1.2.3",
        created_by=create_user,
    )
    case.save(disable_auto_set_user=True)
    return case


@pytest.fixture
def run(db, project, create_user):
    from datetime import date

    run = TestRun.objects.create(
        project=project,
        workspace=project.workspace,
        run_date=date(2026, 11, 9),
        version_number="1.2.3",
        build_number="b20261109",
        commit_hash="abcdef1234",
        environment="Windows 10, pbot 1.2.3",
        executed_by=create_user,
        created_by=create_user,
    )
    run.save(disable_auto_set_user=True)
    return run


@pytest.fixture
def run_case(db, project, run, case, create_user):
    from plane.db.models import TestRunStepRecord

    run_case = TestRunCase.objects.create(
        test_run=run,
        test_case=case,
        project=project,
        workspace=project.workspace,
        case_number=case.number,
        case_module_code=case.module.code,
        case_title=case.title,
        created_by=create_user,
    )
    TestRunStepRecord.objects.create(
        test_run_case=run_case,
        project=project,
        workspace=project.workspace,
        sequence_id=1,
        action_snapshot="Start pbot",
        expected_result_snapshot="The login form is shown",
        created_by=create_user,
    )
    return run_case


def cases_url(slug, project_id):
    return f"/api/workspaces/{slug}/projects/{project_id}/test-cases/"


def modules_url(slug, project_id):
    return f"/api/workspaces/{slug}/projects/{project_id}/test-case-modules/"


def runs_url(slug, project_id):
    return f"/api/workspaces/{slug}/projects/{project_id}/test-runs/"


def error_codes(response):
    return {entry.get("code") for entry in response.json().get("errors", [])}


@pytest.mark.contract
@pytest.mark.django_db
class TestTheBodyShape:
    """Every write of the feature refuses a body that is not an object."""

    def _endpoints(self, workspace, project, case, run, run_case):
        """The writes of the feature that carry a body."""
        return {
            # the raw-body writes
            "patch case": (
                "patch",
                f"{cases_url(workspace.slug, project.id)}{case.id}/",
            ),
            "put steps": (
                "put",
                f"{cases_url(workspace.slug, project.id)}{case.id}/steps/",
            ),
            "post issues": (
                "post",
                f"{cases_url(workspace.slug, project.id)}{case.id}/issues/",
            ),
            "patch run": (
                "patch",
                f"{runs_url(workspace.slug, project.id)}{run.id}/",
            ),
            "post run cases": (
                "post",
                f"{runs_url(workspace.slug, project.id)}{run.id}/cases/",
            ),
            "post case attachments": (
                "post",
                f"{runs_url(workspace.slug, project.id)}{run.id}/cases/{run_case.id}/attachments/",
            ),
            "post step attachments": (
                "post",
                f"{runs_url(workspace.slug, project.id)}{run.id}/cases/{run_case.id}/steps/1/attachments/",
            ),
            # the serializer-based writes
            "post module": ("post", modules_url(workspace.slug, project.id)),
            "post case": ("post", cases_url(workspace.slug, project.id)),
            "post run": ("post", runs_url(workspace.slug, project.id)),
            "patch run case": (
                "patch",
                f"{runs_url(workspace.slug, project.id)}{run.id}/cases/{run_case.id}/",
            ),
            "patch step record": (
                "patch",
                f"{runs_url(workspace.slug, project.id)}{run.id}/cases/{run_case.id}/steps/1/",
            ),
        }

    def test_a_body_that_is_not_an_object_is_refused(
        self, session_client, workspace, project, case, run, run_case
    ):
        endpoints = self._endpoints(workspace, project, case, run, run_case)

        for name, (method, url) in endpoints.items():
            for body in MALFORMED_BODIES:
                response = getattr(session_client, method)(
                    url, body, format="json"
                )
                assert response.status_code == status.HTTP_400_BAD_REQUEST, (
                    name,
                    body,
                    response.content,
                )
                assert response.json()["code"] == "invalid_payload", (name, body)
                assert "invalid_payload" in error_codes(response), (name, body)

    def test_the_stored_records_are_untouched(
        self, session_client, workspace, project, case, run, run_case
    ):
        """Nothing is written before the refusal."""
        steps_before = list(case.steps.all().values("id", "sequence_id", "action"))
        run_before = TestRun.objects.get(pk=run.id).updated_at

        for name, (method, url) in self._endpoints(
            workspace, project, case, run, run_case
        ).items():
            for body in MALFORMED_BODIES:
                getattr(session_client, method)(url, body, format="json")

        assert list(case.steps.all().values("id", "sequence_id", "action")) == steps_before
        assert TestRun.objects.get(pk=run.id).updated_at == run_before
        assert TestRunCase.objects.filter(test_run=run).count() == 1
        assert TestRunStepRecord.objects.filter(test_run_case=run_case).count() == 1
        assert FileAsset.objects.count() == 0
