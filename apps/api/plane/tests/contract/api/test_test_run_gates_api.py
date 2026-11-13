# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: the gates of a batch on the v1 surface (M111).

The v1 surface is the one the pbot CLI drives, and the gates of a batch
are the rules of the feature — they cannot hold on one surface only. The
app surface has its own file (``contract/app/test_test_run_gates_app.py``);
this one pins the same three rules on the public API:

* the finalize refuses a step that was never touched, and the error
  names the case and the step;
* a 需人工 conclusion needs a 依据 — why the case cannot be judged by the
  machine;
* the re-test channel copies the case set and clears the outcome, so a
  second execution is a record of its own — and an evidence that is a
  copy of another batch's evidence is refused.

A batch that is complete still closes: the gates are not a blockade.
"""

from uuid import uuid4

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import Project, ProjectMember, TestCaseModule
from plane.db.models.api import APIToken


#: The prefix every v1 path of this feature carries.
BASE = "/api/v1/workspaces/{slug}/projects/{project_id}"


def runs_url(slug, project_id):
    return f"{BASE.format(slug=slug, project_id=project_id)}/test-runs/"


def cases_url(slug, project_id):
    return f"{BASE.format(slug=slug, project_id=project_id)}/test-cases/"


def error_codes(response):
    return {entry.get("code") for entry in response.json().get("errors", [])}


def error_entries(response):
    return response.json().get("errors", [])


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="Test Run Gates V1 Project",
        identifier="TRV",
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
def human_client(db, create_user):
    """A client with a human API token of its own.

    The throttle of the v1 surface is keyed on the API-key string, so a
    token per test keeps this file independent of the rest of the suite.
    """
    token = APIToken.objects.create(
        user=create_user,
        label="Test API Token",
        token=f"plane_api_run-gates-{uuid4().hex}",
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=token.token)
    return client


def case_payload(module, **overrides):
    payload = {
        "module": str(module.id),
        "title": "Log in with a valid account",
        "purpose": "Make sure the login path of pbot works",
        "object_overview": "The login form of the desktop client",
        "priority": "P1",
        "preconditions": "The account exists and is active",
        "environment": "Windows 10, pbot 1.2.3",
        "steps": [
            {"action": "Start pbot", "expected_result": "The login form is shown"},
            {"action": "Type the password", "expected_result": "The dashboard is shown"},
        ],
    }
    payload.update(overrides)
    return payload


def create_case(client, workspace, project, module, **overrides):
    response = client.post(
        cases_url(workspace.slug, project.id), case_payload(module, **overrides), format="json"
    )
    assert response.status_code == status.HTTP_201_CREATED, response.content
    return response.json()


def create_run(client, workspace, project, user, **overrides):
    payload = {
        "run_date": "2026-11-09",
        "version_number": "1.2.3",
        "build_number": "b20261109",
        "commit_hash": "abcdef1234",
        "environment": "Windows 10, pbot 1.2.3",
        "executed_by": str(user.id),
    }
    payload.update(overrides)
    response = client.post(runs_url(workspace.slug, project.id), payload, format="json")
    assert response.status_code == status.HTTP_201_CREATED, response.content
    return response.json()


def add_case(client, workspace, project, run_id, case_id):
    response = client.post(
        f"{runs_url(workspace.slug, project.id)}{run_id}/cases/",
        {"test_case_ids": [str(case_id)]},
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED, response.content
    return response.json()[0]


def mark_step(client, workspace, project, run_id, run_case_id, sequence_id, **payload):
    return client.patch(
        f"{runs_url(workspace.slug, project.id)}{run_id}/cases/{run_case_id}/steps/{sequence_id}/",
        payload,
        format="json",
    )


def conclude(client, workspace, project, run_id, run_case_id, **payload):
    return client.patch(
        f"{runs_url(workspace.slug, project.id)}{run_id}/cases/{run_case_id}/",
        payload,
        format="json",
    )


def finalize(client, workspace, project, run_id):
    return client.post(
        f"{runs_url(workspace.slug, project.id)}{run_id}/finalize/", {}, format="json"
    )


@pytest.mark.contract
@pytest.mark.django_db
class TestTheGatesOnV1:
    def test_a_step_that_was_never_touched_refuses_the_finalize(
        self, human_client, workspace, project, module, create_user
    ):
        """The counter-example of the review: a batch with an unset step."""
        case = create_case(human_client, workspace, project, module)
        run = create_run(human_client, workspace, project, create_user)
        run_case = add_case(human_client, workspace, project, run["id"], case["id"])

        response = mark_step(
            human_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            1,
            conclusion="fail",
            actual_value="the login button stayed grey",
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        response = conclude(
            human_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            conclusion="FAIL",
            evidence="the screen showed the error of the account",
        )
        assert response.status_code == status.HTTP_200_OK, response.content

        response = finalize(human_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert error_codes(response) == {"run_step_conclusion_unset"}, response.content

        entry = error_entries(response)[0]
        assert entry["case_number"] == case["number"], entry
        assert entry["step_sequence"] == 2, entry
        assert entry["path"] == f"cases[{case['number']}].steps[2].conclusion", entry

    def test_a_manual_conclusion_without_a_basis_refuses_the_finalize(
        self, human_client, workspace, project, module, create_user
    ):
        """The executor and the condition are not a 依据."""
        case = create_case(human_client, workspace, project, module)
        run = create_run(human_client, workspace, project, create_user)
        run_case = add_case(human_client, workspace, project, run["id"], case["id"])

        for sequence_id in (1, 2):
            response = mark_step(
                human_client,
                workspace,
                project,
                run["id"],
                run_case["id"],
                sequence_id,
                conclusion="pass",
                actual_value="the form was shown and the dashboard appeared",
            )
            assert response.status_code == status.HTTP_200_OK, response.content
        response = conclude(
            human_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            conclusion="MANUAL",
            manual_executor="Li Si",
            manual_condition="after the developer prepares the environment",
            evidence="the case was executed by hand",
        )
        assert response.status_code == status.HTTP_200_OK, response.content

        response = finalize(human_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert "run_manual_without_basis" in error_codes(response), response.content
        entry = next(
            entry for entry in error_entries(response) if entry["code"] == "run_manual_without_basis"
        )
        assert entry["case_number"] == case["number"], entry

    def test_a_retest_copies_the_cases_and_clears_the_outcome(
        self, human_client, workspace, project, module, create_user
    ):
        """The re-test runs the same cases and records its own execution."""
        case = create_case(human_client, workspace, project, module)
        source = create_run(human_client, workspace, project, create_user)
        source_case = add_case(human_client, workspace, project, source["id"], case["id"])

        mark_step(
            human_client,
            workspace,
            project,
            source["id"],
            source_case["id"],
            1,
            conclusion="fail",
            actual_value="the login button stayed grey",
        )
        conclude(
            human_client,
            workspace,
            project,
            source["id"],
            source_case["id"],
            conclusion="FAIL",
            evidence="the screen showed the error of the account",
        )

        response = human_client.post(
            runs_url(workspace.slug, project.id),
            {
                "run_date": "2026-11-09",
                "version_number": "1.2.4",
                "build_number": "b20261110",
                "commit_hash": "123456abcd",
                "environment": "Windows 10, pbot 1.2.4",
                "executed_by": str(create_user.id),
                "retest_of": source["id"],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.content
        retest = response.json()

        assert retest["number"] == "RUN-20261109-02", retest
        assert retest["retest_of"] == source["id"], retest
        assert len(retest["cases"]) == 1, retest
        copied = retest["cases"][0]
        assert copied["case_number"] == case["number"], copied
        assert copied["conclusion"] == "", copied
        assert copied["evidence"] == "", copied
        assert [step["conclusion"] for step in copied["steps"]] == ["unset", "unset"], copied
        assert f"**批次**：{retest['number']}" in copied["evidence_header"], copied

    def test_an_evidence_may_not_be_copied_from_another_batch(
        self, human_client, workspace, project, module, create_user
    ):
        """The twin the re-test replaces: the same evidence, twice."""
        case = create_case(human_client, workspace, project, module)
        first = create_run(human_client, workspace, project, create_user)
        first_case = add_case(human_client, workspace, project, first["id"], case["id"])
        second = create_run(human_client, workspace, project, create_user)
        second_case = add_case(human_client, workspace, project, second["id"], case["id"])

        response = conclude(
            human_client,
            workspace,
            project,
            first["id"],
            first_case["id"],
            conclusion="FAIL",
            evidence="the screen showed the error of the account",
        )
        assert response.status_code == status.HTTP_200_OK, response.content

        response = conclude(
            human_client,
            workspace,
            project,
            second["id"],
            second_case["id"],
            conclusion="FAIL",
            evidence="the screen showed the error of the account",
        )
        assert response.status_code == status.HTTP_409_CONFLICT, response.content
        assert error_codes(response) == {"run_evidence_copied"}, response.content

    def test_a_complete_batch_still_closes(
        self, human_client, workspace, project, module, create_user
    ):
        """The gates are not a blockade: a batch that is complete closes."""
        case = create_case(human_client, workspace, project, module)
        run = create_run(human_client, workspace, project, create_user)
        run_case = add_case(human_client, workspace, project, run["id"], case["id"])

        for sequence_id in (1, 2):
            response = mark_step(
                human_client,
                workspace,
                project,
                run["id"],
                run_case["id"],
                sequence_id,
                conclusion="pass",
                actual_value="the form was shown and the dashboard appeared",
            )
            assert response.status_code == status.HTTP_200_OK, response.content
        response = conclude(
            human_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            conclusion="PASS",
            evidence="the login succeeded and the dashboard appeared",
        )
        assert response.status_code == status.HTTP_200_OK, response.content

        response = finalize(human_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.json()["status"] == "completed"
