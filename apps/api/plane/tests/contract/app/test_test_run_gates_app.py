# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for the gates of a batch (M111).

The surface under test is the app API of the batch side
(``/api/workspaces/<slug>/projects/<project_id>/test-runs/``). The gates
are the three rules the review of the real batches asked for:

* **the finalize refuses a step that was never touched** — no verdict and
  nothing written about it. The error names the case and the step, so
  the tester knows what to fill in. The shape the standard asks for (a
  step that was not executed but whose reason is written in the measured
  value, §6.1 rule 1) still closes the batch — the gate is not a
  blockade;
* **a 需人工 conclusion needs a 依据** — why the case cannot be judged
  by the machine. The executor and the condition answer *who* and
  *when*; without a basis 需人工 is a default that lets a batch close
  over cases nobody executed;
* **the re-test channel replaces the twin** — a batch that names the
  batch it re-tests copies the case set and nothing of the execution, so
  a second execution is a record of its own. An evidence that is a
  verbatim copy of another batch's evidence is refused, and so is an
  evidence that names another batch: the batch number is written by the
  server, and the record of a batch shows its own.
"""

from __future__ import annotations

import pytest
from rest_framework import status

from plane.db.models import Project, ProjectMember, TestCaseModule


# --------------------------------------------------------------------------- #
# Fixtures / helpers                                                          #
# --------------------------------------------------------------------------- #


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="Test Run Gates Project",
        identifier="TRG",
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


def runs_url(slug, project_id):
    return f"/api/workspaces/{slug}/projects/{project_id}/test-runs/"


def cases_url(slug, project_id):
    return f"/api/workspaces/{slug}/projects/{project_id}/test-cases/"


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


def error_codes(response):
    return {entry.get("code") for entry in response.json().get("errors", [])}


def error_entries(response):
    return response.json().get("errors", [])


# --------------------------------------------------------------------------- #
# ① The finalize refuses a step that was never touched                          #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestTheUnsetStepGate:
    def test_a_step_that_was_never_touched_refuses_the_finalize(
        self, session_client, workspace, project, module, create_user
    ):
        """The counter-example of the review: a batch with an unset step."""
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        # Step 1 failed and carries its observation; step 2 was never
        # touched at all.
        response = mark_step(
            session_client,
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
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            conclusion="FAIL",
            evidence="the screen showed the error of the account",
        )
        assert response.status_code == status.HTTP_200_OK, response.content

        response = finalize(session_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert error_codes(response) == {"run_step_conclusion_unset"}, response.content

        # The entry names the case and the step that is missing.
        entry = error_entries(response)[0]
        assert entry["case_number"] == case["number"], entry
        assert entry["step_sequence"] == 2, entry
        assert entry["path"] == f"cases[{case['number']}].steps[2].conclusion", entry
        assert entry["message"], entry

        # The batch is not completed: it stays open until the step is
        # filled in.
        assert run["status"] != "completed"

    def test_the_batch_closes_once_the_step_is_filled_in(
        self, session_client, workspace, project, module, create_user
    ):
        """The gate is not a blockade: the same batch closes afterwards."""
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        mark_step(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            1,
            conclusion="fail",
            actual_value="the login button stayed grey",
        )
        conclude(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            conclusion="FAIL",
            evidence="the screen showed the error of the account",
        )
        assert finalize(session_client, workspace, project, run["id"]).status_code == (
            status.HTTP_400_BAD_REQUEST
        )

        mark_step(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            2,
            conclusion="pass",
            actual_value="the second attempt showed the dashboard",
        )
        response = finalize(session_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.json()["status"] == "completed"

    def test_the_manual_step_of_the_standard_still_closes_the_batch(
        self, session_client, workspace, project, module, create_user
    ):
        """§6.1 rule 1: a step that was not executed, with the reason in
        the measured value, is a record and not a hole."""
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        mark_step(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            1,
            conclusion="fail",
            actual_value="the login button stayed grey",
        )
        mark_step(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            2,
            actual_value="the notification permission was not granted, this step was not executed",
        )
        conclude(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            conclusion="FAIL",
            evidence="the screen showed the error of the account",
        )

        response = finalize(session_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.json()["status"] == "completed"


# --------------------------------------------------------------------------- #
# ② 需人工 needs a 依据                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestTheManualBasisGate:
    def test_a_manual_conclusion_without_a_basis_refuses_the_finalize(
        self, session_client, workspace, project, module, create_user
    ):
        """The executor and the condition are not a 依据."""
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        for sequence_id in (1, 2):
            mark_step(
                session_client,
                workspace,
                project,
                run["id"],
                run_case["id"],
                sequence_id,
                conclusion="pass",
                actual_value="the form was shown and the dashboard appeared",
            )
        response = conclude(
            session_client,
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

        response = finalize(session_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert "run_manual_without_basis" in error_codes(response), response.content

        # The entry names the case that carries the 需人工.
        entry = next(
            entry
            for entry in error_entries(response)
            if entry["code"] == "run_manual_without_basis"
        )
        assert entry["case_number"] == case["number"], entry
        assert entry["path"] == f"cases[{case['number']}].na_reason", entry

    def test_a_manual_conclusion_with_a_basis_closes_the_batch(
        self, session_client, workspace, project, module, create_user
    ):
        """The 依据 is what makes 需人工 a record and not a default."""
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        for sequence_id in (1, 2):
            mark_step(
                session_client,
                workspace,
                project,
                run["id"],
                run_case["id"],
                sequence_id,
                conclusion="pass",
                actual_value="the form was shown and the dashboard appeared",
            )
        conclude(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            conclusion="MANUAL",
            manual_executor="Li Si",
            manual_condition="after the developer prepares the environment",
            na_reason="the case needs a physical device, the script cannot drive it",
            evidence="the case was executed by hand",
        )

        response = finalize(session_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.json()["status"] == "completed"


# --------------------------------------------------------------------------- #
# ③ The re-test channel and the twin                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestTheRetestChannel:
    def test_a_retest_copies_the_cases_and_clears_the_outcome(
        self, session_client, workspace, project, module, create_user
    ):
        """The re-test runs the same cases and records its own execution."""
        case = create_case(session_client, workspace, project, module)
        source = create_run(session_client, workspace, project, create_user)
        source_case = add_case(session_client, workspace, project, source["id"], case["id"])

        mark_step(
            session_client,
            workspace,
            project,
            source["id"],
            source_case["id"],
            1,
            conclusion="fail",
            actual_value="the login button stayed grey",
        )
        conclude(
            session_client,
            workspace,
            project,
            source["id"],
            source_case["id"],
            conclusion="FAIL",
            evidence="the screen showed the error of the account",
        )

        response = session_client.post(
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

        # The new batch is a batch of its own: its own number and its own
        # link to the batch it re-tests.
        assert retest["number"] == "RUN-20261109-02", retest
        assert retest["number"] != source["number"], retest
        assert retest["retest_of"] == source["id"], retest
        assert retest["retest_of_number"] == source["number"], retest

        # The case set is copied — the re-test runs these cases.
        assert len(retest["cases"]) == 1, retest
        copied = retest["cases"][0]
        assert copied["case_number"] == case["number"], copied

        # Nothing of the execution is copied.
        assert copied["conclusion"] == "", copied
        assert copied["evidence"] == "", copied
        assert copied["na_reason"] == "", copied
        assert copied["manual_executor"] == "", copied
        assert [step["conclusion"] for step in copied["steps"]] == ["unset", "unset"], copied
        assert [step["actual_value"] for step in copied["steps"]] == ["", ""], copied

    def test_the_header_of_a_copied_case_names_the_new_batch(
        self, session_client, workspace, project, module, create_user
    ):
        """The batch number of a record is written by the server."""
        case = create_case(session_client, workspace, project, module)
        source = create_run(session_client, workspace, project, create_user)
        add_case(session_client, workspace, project, source["id"], case["id"])

        response = session_client.post(
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

        header = retest["cases"][0]["evidence_header"]
        assert f"**批次**：{retest['number']}" in header, header
        assert source["number"] not in header, header

    def test_an_evidence_may_not_be_copied_from_another_batch(
        self, session_client, workspace, project, module, create_user
    ):
        """The twin the re-test replaces: the same evidence, twice."""
        case = create_case(session_client, workspace, project, module)
        first = create_run(session_client, workspace, project, create_user)
        first_case = add_case(session_client, workspace, project, first["id"], case["id"])
        second = create_run(session_client, workspace, project, create_user)
        second_case = add_case(session_client, workspace, project, second["id"], case["id"])

        response = conclude(
            session_client,
            workspace,
            project,
            first["id"],
            first_case["id"],
            conclusion="FAIL",
            evidence="the screen showed the error of the account",
        )
        assert response.status_code == status.HTTP_200_OK, response.content

        response = conclude(
            session_client,
            workspace,
            project,
            second["id"],
            second_case["id"],
            conclusion="FAIL",
            evidence="the screen showed the error of the account",
        )
        assert response.status_code == status.HTTP_409_CONFLICT, response.content
        assert error_codes(response) == {"run_evidence_copied"}, response.content

    def test_a_client_may_ask_for_an_empty_batch_with_the_link_alone(
        self, session_client, workspace, project, module, create_user
    ):
        """``copy_cases: false`` keeps the old way of opening a batch."""
        case = create_case(session_client, workspace, project, module)
        source = create_run(session_client, workspace, project, create_user)
        add_case(session_client, workspace, project, source["id"], case["id"])

        response = session_client.post(
            runs_url(workspace.slug, project.id),
            {
                "run_date": "2026-11-09",
                "version_number": "1.2.4",
                "build_number": "b20261110",
                "commit_hash": "123456abcd",
                "environment": "Windows 10, pbot 1.2.4",
                "executed_by": str(create_user.id),
                "retest_of": source["id"],
                "copy_cases": False,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.content
        batch = response.json()
        assert batch["retest_of"] == source["id"], batch
        assert batch["cases"] == [], batch

    def test_a_retest_refuses_a_case_that_is_not_written_yet(
        self, session_client, workspace, project, module, create_user
    ):
        """§5.6: a case that is not written yet must not enter a batch —
        not through the add path and not through the re-test path."""
        from plane.db.models import TestCase, TestCaseStep, TestRun as TestRunRow
        from plane.db.models import TestRunCase as TestRunCaseRow

        # The case is written at the model level, so the authoring rules
        # of the write surface could not have refused it.
        case = TestCase.objects.create(
            project=project,
            module=module,
            title="Switch the language",
            purpose="Make sure the language switch of the settings page works",
            object_overview="The language menu of the settings page",
            priority="P1",
            preconditions="The settings page is open",
            environment="Windows 10, pbot 1.2.3",
            created_by=create_user,
        )
        TestCaseStep.objects.create(
            project=project,
            test_case=case,
            sequence_id=1,
            action="Switch the language to English",
            expected_result="切换语言后界面显示正常",
        )
        source = create_run(session_client, workspace, project, create_user)
        source_row = TestRunRow.objects.get(pk=source["id"])
        TestRunCaseRow.create_for_test_case(test_case=case, test_run=source_row)

        response = session_client.post(
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
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert response.json()["code"] == "invalid_test_case", response.content
        # Nothing was created: the re-test was refused as a whole.
        assert TestRunRow.objects.filter(retest_of=source_row).count() == 0

    def test_an_evidence_may_not_name_another_batch(
        self, session_client, workspace, project, module, create_user
    ):
        """A hand-written batch number is how a record points at the wrong
        batch — the server writes the number."""
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        response = conclude(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            conclusion="FAIL",
            evidence="批次：RUN-20261113-02（draft）\n\nthe screen showed the error of the account",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert error_codes(response) == {"run_foreign_batch_number"}, response.content

    def test_the_evidence_header_is_rendered_by_the_server(
        self, session_client, workspace, project, module, create_user
    ):
        """The header names the batch, the version, the day and the person."""
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        header = run_case["evidence_header"]
        assert f"**批次**：{run['number']}" in header, header
        assert "**被测版本**：1.2.3" in header, header
        assert "**执行日期**：2026-11-09" in header, header
        assert "**执行人**：" in header, header
