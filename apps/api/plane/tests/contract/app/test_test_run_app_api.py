# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for the app API of the batch side (M75 / TCASE-2R).

The surface under test is the internal app API
(``/api/workspaces/<slug>/projects/<project_id>/test-runs/`` and the
case records, step records and attachments below it). The tests pin the
behaviour the standard (STD-SW-003) demands:

* a case that is added to a batch is **snapshotted** — a later edit of
  the case never rewrites the history of the batch;
* the measured value and the evidence travel as the **Markdown source**
  that was typed: no HTML conversion, no stripping of the markup;
* a verdict and a conclusion are written **once**;
* ``finalize`` runs the validity rules of the standard and reports
  every problem on its own — the field path, the stable reason code and
  the Chinese sentence;
* a **completed batch is frozen**: every write returns 409;
* a case that was selected into a batch can be taken out again — the
  removal takes it out of that batch only, and the batch of a completed
  run refuses it like every other write;
* a file that is attached to a step is a measured value in its own
  right, and an inline image that was never uploaded is not.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from rest_framework import status

from plane.db.models import (
    APIToken,
    FileAsset,
    Issue,
    Project,
    ProjectMember,
    State,
    TestCaseModule,
    TestRun,
    TestRunCase,
    TestRunStepRecord,
    User,
    WorkspaceMember,
)
from plane.service_principals.constants import PrincipalType
from plane.service_principals.models import (
    ProjectGrant,
    ServicePrincipal,
    ServiceScope,
)


# --------------------------------------------------------------------------- #
# Fixtures / helpers                                                          #
# --------------------------------------------------------------------------- #


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="Test Run Project",
        identifier="TRP",
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
def issue(db, project, workspace, create_user):
    state = State.objects.create(
        name="Todo", project=project, workspace=workspace, group="backlog", default=True
    )
    return Issue.objects.create(
        name="The login of pbot does not work",
        project=project,
        workspace=workspace,
        state=state,
        created_by=create_user,
    )


@pytest.fixture
def sp(db, workspace, create_user):
    return ServicePrincipal.objects.create(
        workspace=workspace,
        owner=create_user,
        name="testrun-bot",
    )


@pytest.fixture
def sp_token(db, sp):
    return APIToken.objects.create(
        user=sp.owner,
        label="svc:testrun-bot",
        user_type=1,
        is_service=True,
        principal_type=PrincipalType.SERVICE,
        service_principal=sp,
        workspace=sp.workspace,
        token="plane_svc_testrun_bot",
    )


@pytest.fixture
def sp_key_client(api_client, sp_token):
    from rest_framework.test import APIClient

    client = APIClient()
    client.credentials(HTTP_X_API_KEY=sp_token.token)
    return client


@pytest.fixture
def sp_granted(db, sp, project):
    """The SP may read, create and update the batches of the project."""
    for resource_type in ("test_run", "test_case"):
        for action in ("read", "create", "update"):
            ServiceScope.objects.create(
                service_principal=sp,
                project=project,
                resource_type=resource_type,
                action=action,
            )
    ProjectGrant.objects.create(
        service_principal=sp,
        project=project,
        role_cap=15,
        is_active=True,
    )
    return sp


@pytest.fixture
def guest_client(db, workspace, project):
    """A client of a project **guest** (role 5).

    A guest reads the batches of the project but may not change the
    composition of one: adding a case and removing it are the same write.
    """
    from rest_framework.test import APIClient

    guest = User.objects.create(
        email=f"guest-{uuid4().hex}@plane.so",
        username=f"guest_{uuid4().hex}",
        first_name="Project",
        last_name="Guest",
    )
    WorkspaceMember.objects.create(workspace=workspace, member=guest, role=5)
    ProjectMember.objects.create(project=project, member=guest, role=5, is_active=True)
    client = APIClient()
    client.force_authenticate(user=guest)
    return client


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


def remove_case(client, workspace, project, run_id, run_case_id):
    return client.delete(
        f"{runs_url(workspace.slug, project.id)}{run_id}/cases/{run_case_id}/"
    )


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


def make_valid_batch(client, workspace, project, module, user, step_payload=None):
    """A batch that passes every rule of the standard."""
    case = create_case(client, workspace, project, module)
    run = create_run(client, workspace, project, user)
    run_case = add_case(client, workspace, project, run["id"], case["id"])

    response = mark_step(
        client,
        workspace,
        project,
        run["id"],
        run_case["id"],
        1,
        **(step_payload or {"conclusion": "pass", "actual_value": "the form was shown"}),
    )
    assert response.status_code == status.HTTP_200_OK, response.content

    response = conclude(
        client,
        workspace,
        project,
        run["id"],
        run_case["id"],
        conclusion="PASS",
        evidence="the login succeeded",
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    return run, run_case


def make_asset(project, name="shot.png", entity_type=FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD):
    return FileAsset.objects.create(
        attributes={"name": name, "type": "image/png", "size": 4096},
        asset=f"{project.workspace_id}/{name}",
        size=4096,
        workspace=project.workspace,
        project=project,
        entity_type=entity_type,
        is_uploaded=True,
    )


# --------------------------------------------------------------------------- #
# The batch itself                                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestRunHeader:
    def test_the_server_assigns_the_batch_number(
        self, session_client, workspace, project, create_user
    ):
        run = create_run(session_client, workspace, project, create_user)
        assert run["number"] == "RUN-20261109-01"

    def test_the_batch_numbers_increase_inside_the_day(
        self, session_client, workspace, project, create_user
    ):
        first = create_run(session_client, workspace, project, create_user)
        second = create_run(session_client, workspace, project, create_user)
        assert first["number"] == "RUN-20261109-01"
        assert second["number"] == "RUN-20261109-02"

    def test_the_executor_is_written_by_the_server(
        self, session_client, workspace, project, create_user
    ):
        run = create_run(session_client, workspace, project, create_user)
        assert run["executed_by"] == str(create_user.id)
        # The server keeps a text snapshot of the executor next to the
        # foreign key; the client never writes it.
        assert run["executed_by_name"]

    def test_a_batch_cannot_be_completed_by_writing_the_status(
        self, session_client, workspace, project, create_user
    ):
        run = create_run(session_client, workspace, project, create_user)
        response = session_client.patch(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/",
            {"status": "completed"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_the_tested_version_is_frozen(
        self, session_client, workspace, project, create_user
    ):
        run = create_run(session_client, workspace, project, create_user)
        response = session_client.patch(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/",
            {"version_number": "9.9.9"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "run_version_fixed" in error_codes(response)

    def test_a_stale_update_is_a_conflict(
        self, session_client, workspace, project, create_user
    ):
        run = create_run(session_client, workspace, project, create_user)
        session_client.patch(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/",
            {"environment": "Linux"},
            format="json",
        )
        response = session_client.patch(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/",
            {"environment": "macOS", "updated_at": run["updated_at"]},
            format="json",
        )
        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json()["code"] == "conflict"


# --------------------------------------------------------------------------- #
# ⑥ The snapshot                                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestSnapshot:
    def test_an_edit_of_the_case_does_not_touch_the_batch(
        self, session_client, workspace, project, module, create_user
    ):
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        assert run_case["case_title"] == "Log in with a valid account"

        response = session_client.patch(
            f"{cases_url(workspace.slug, project.id)}{case['id']}/",
            {"title": "A title the case carries now"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        # The batch still holds what was executed.
        response = session_client.get(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/"
        )
        stored = response.json()[0]
        assert stored["case_title"] == "Log in with a valid account"

    def test_the_step_table_is_snapshotted_as_well(
        self, session_client, workspace, project, module, create_user
    ):
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        add_case(session_client, workspace, project, run["id"], case["id"])

        session_client.put(
            f"{cases_url(workspace.slug, project.id)}{case['id']}/steps/",
            {"steps": [{"action": "A different step", "expected_result": "A different result"}]},
            format="json",
        )

        response = session_client.get(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/"
        )
        steps = response.json()[0]["steps"]
        assert steps[0]["action_snapshot"] == "Start pbot"


# --------------------------------------------------------------------------- #
# The step record and the Markdown                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestStepRecord:
    def test_the_verdict_and_the_measured_value_are_written(
        self, session_client, workspace, project, module, create_user
    ):
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        response = mark_step(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            1,
            conclusion="pass",
            actual_value="the form was shown",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["conclusion"] == "pass"
        assert response.json()["actual_value"] == "the form was shown"

    def test_the_measured_value_keeps_its_markdown_source(
        self, session_client, workspace, project, module, create_user
    ):
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        markdown = "**the form** was shown, `not bold`, and a [link](https://plane.so)"
        response = mark_step(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            1,
            conclusion="pass",
            actual_value=markdown,
        )
        assert response.status_code == status.HTTP_200_OK
        # The source travels as it was typed: no HTML, no stripped markup.
        assert response.json()["actual_value"] == markdown

    def test_the_evidence_keeps_its_markdown_source(
        self, session_client, workspace, project, module, create_user
    ):
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        markdown = "see the **screenshot** below"
        response = conclude(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            conclusion="PASS",
            evidence=markdown,
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["evidence"] == markdown

    def test_a_verdict_is_written_once(
        self, session_client, workspace, project, module, create_user
    ):
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        response = mark_step(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            1,
            conclusion="pass",
            actual_value="the form was shown",
        )
        assert response.status_code == status.HTTP_200_OK

        response = mark_step(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            1,
            conclusion="fail",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "conclusion_is_final" in error_codes(response)

    def test_a_conclusion_is_written_once(
        self, session_client, workspace, project, module, create_user
    ):
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        response = conclude(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            conclusion="PASS",
            evidence="it worked",
        )
        assert response.status_code == status.HTTP_200_OK

        response = conclude(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            conclusion="FAIL",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "conclusion_is_final" in error_codes(response)


# --------------------------------------------------------------------------- #
# ③④⑤⑨⑩⑪ The validity rules of the standard                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestFinalize:
    def test_a_valid_batch_is_completed(
        self, session_client, workspace, project, module, create_user
    ):
        run, _ = make_valid_batch(session_client, workspace, project, module, create_user)

        response = finalize(session_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == "completed"

    def test_an_invalid_batch_reports_every_problem(
        self, session_client, workspace, project, module, create_user
    ):
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        # The step is marked ✘ but no measured value was written, and the
        # case carries no conclusion and no evidence.
        response = mark_step(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            1,
            conclusion="fail",
        )
        assert response.status_code == status.HTTP_200_OK

        response = finalize(session_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        codes = error_codes(response)
        assert "fail_without_actual_value" in codes
        assert "conclusion_invalid" in codes
        assert "evidence_missing" in codes

        # Every problem carries the field path of the field it belongs to.
        paths = {entry.get("path") for entry in response.json()["errors"]}
        assert f"cases[{case['number']}].steps[1].actual_value" in paths
        assert f"cases[{case['number']}].evidence" in paths

    def test_the_batch_carries_its_case_records(
        self, session_client, workspace, project, module, create_user
    ):
        """The batch answer carries the case records it holds."""
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        # The batch that was created carries the key (it was empty then).
        assert "cases" in run
        assert run["cases"] == []

        # The detail carries the case record.
        response = session_client.get(f"{runs_url(workspace.slug, project.id)}{run['id']}/")
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert len(body["cases"]) == 1
        assert body["cases"][0]["case_number"] == "TC-CLI-001"
        assert body["cases"][0]["id"] == run_case["id"]

        # The list carries them as well.
        response = session_client.get(runs_url(workspace.slug, project.id))
        assert response.status_code == status.HTTP_200_OK
        listed = response.json()["results"][0]
        assert len(listed["cases"]) == 1
        assert listed["cases"][0]["case_number"] == "TC-CLI-001"

        # And so does the answer of the finalize.
        mark_step(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            1,
            conclusion="pass",
            actual_value="the form was shown",
        )
        conclude(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            conclusion="PASS",
            evidence="it worked",
        )
        response = finalize(session_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()["cases"]) == 1

    def test_a_completed_batch_is_frozen(
        self, session_client, workspace, project, module, create_user
    ):
        run, run_case = make_valid_batch(session_client, workspace, project, module, create_user)
        response = finalize(session_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_200_OK

        # Every write on the batch is refused with 409.
        response = session_client.patch(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/",
            {"environment": "Linux"},
            format="json",
        )
        assert response.status_code == status.HTTP_409_CONFLICT

        response = mark_step(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            1,
            actual_value="a new observation",
        )
        assert response.status_code == status.HTTP_409_CONFLICT

        response = conclude(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            remarks="a remark",
        )
        assert response.status_code == status.HTTP_409_CONFLICT

        response = finalize(session_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_409_CONFLICT

    def test_a_pass_conclusion_may_not_have_a_failed_step(
        self, session_client, workspace, project, module, create_user
    ):
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
            actual_value="the form was not shown",
        )
        conclude(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            conclusion="PASS",
            evidence="it looked fine",
        )

        response = finalize(session_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "conclusion_pass_with_failed_step" in error_codes(response)

    def test_a_fail_conclusion_needs_a_failed_step(
        self, session_client, workspace, project, module, create_user
    ):
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
            conclusion="pass",
            actual_value="the form was shown",
        )
        conclude(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            conclusion="FAIL",
            evidence="something went wrong",
        )

        response = finalize(session_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "conclusion_fail_without_failed_step" in error_codes(response)

    def test_an_evaluation_word_is_not_a_measured_value(
        self, session_client, workspace, project, module, create_user
    ):
        """⑩ The measured value must be the observed original."""
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
            actual_value="**不一致**",
        )
        conclude(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            conclusion="FAIL",
            evidence="the screen showed something else",
        )

        response = finalize(session_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "actual_value_is_evaluation" in error_codes(response)

    def test_an_inline_image_that_was_never_uploaded_is_not_a_measured_value(
        self, session_client, workspace, project, module, create_user
    ):
        """⑪ ``![](…)`` points at nothing the batch can show."""
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
            actual_value="![](shot.png)",
        )
        conclude(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            conclusion="FAIL",
            evidence="the screen showed something else",
        )

        response = finalize(session_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        codes = error_codes(response)
        assert "fail_without_actual_value" in codes
        assert "actual_value_is_image_only" in codes

    def test_an_attached_file_is_a_measured_value(
        self, session_client, workspace, project, module, create_user
    ):
        """⑨ The screenshot of the screen is what the tester saw."""
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        asset = make_asset(project)
        response = session_client.post(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/{run_case['id']}/steps/1/attachments/",
            {"asset_id": str(asset.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        mark_step(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            1,
            conclusion="fail",
        )
        conclude(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            conclusion="FAIL",
            evidence="the screenshot of the screen",
        )

        response = finalize(session_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == "completed"

    def test_a_batch_without_the_environment_items_is_invalid(
        self, session_client, workspace, project, module, create_user
    ):
        run = create_run(
            session_client,
            workspace,
            project,
            create_user,
            version_number="",
            build_number="",
            commit_hash="",
            environment="",
        )

        response = finalize(session_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        codes = error_codes(response)
        assert "run_version_number_missing" in codes
        assert "run_build_number_missing" in codes
        assert "run_commit_hash_missing" in codes
        assert "run_environment_missing" in codes


# --------------------------------------------------------------------------- #
# The attachments                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestAttachments:
    def test_a_file_is_bound_listed_and_unbound(
        self, session_client, workspace, project, module, create_user
    ):
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        asset = make_asset(project)
        url = f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/{run_case['id']}/attachments/"

        response = session_client.post(url, {"asset_id": str(asset.id)}, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body["id"] == str(asset.id)
        assert body["asset_id"] == str(asset.id)
        assert body["name"] == "shot.png"
        assert body["size"] == 4096
        assert body["url"]

        response = session_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()) == 1

        response = session_client.delete(f"{url}{asset.id}/")
        assert response.status_code == status.HTTP_204_NO_CONTENT

        response = session_client.get(url)
        assert response.json() == []

    def test_the_attachments_travel_with_the_record(
        self, session_client, workspace, project, module, create_user
    ):
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        asset = make_asset(project)
        session_client.post(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/{run_case['id']}/steps/1/attachments/",
            {"asset_id": str(asset.id)},
            format="json",
        )

        response = session_client.get(f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/")
        steps = response.json()[0]["steps"]
        assert len(steps[0]["attachments"]) == 1
        assert steps[0]["attachments"][0]["name"] == "shot.png"

    def test_a_file_cannot_be_bound_twice(
        self, session_client, workspace, project, module, create_user
    ):
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        asset = make_asset(project)
        url = f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/{run_case['id']}/attachments/"
        assert session_client.post(url, {"asset_id": str(asset.id)}, format="json").status_code == (
            status.HTTP_201_CREATED
        )

        response = session_client.post(url, {"asset_id": str(asset.id)}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "asset_already_bound" in error_codes(response)

    def test_a_file_of_another_project_is_refused(
        self, session_client, workspace, project, module, create_user, db
    ):
        """A file of another project may not be moved into this one."""
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        other_project = Project.objects.create(
            name="Other Project",
            identifier="OTH",
            workspace=workspace,
            created_by=create_user,
            network=2,
        )
        foreign = make_asset(other_project, name="foreign.png")

        response = session_client.post(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/{run_case['id']}/attachments/",
            {"asset_id": str(foreign.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "asset_project_mismatch" in error_codes(response)

        # The file stayed where it was.
        foreign.refresh_from_db()
        assert foreign.project_id == other_project.id
        assert foreign.test_run_case_id is None

    def test_a_file_that_belongs_to_another_entity_is_refused(
        self, session_client, workspace, project, module, create_user, issue
    ):
        """A file may not end up with two owners."""
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        attached = make_asset(project, name="issue-shot.png")
        attached.issue_id = issue.id
        attached.save()

        response = session_client.post(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/{run_case['id']}/attachments/",
            {"asset_id": str(attached.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "asset_already_bound" in error_codes(response)

        # The file still belongs to the work item alone.
        attached.refresh_from_db()
        assert attached.issue_id == issue.id
        assert attached.test_run_case_id is None

    def test_a_file_uploaded_through_the_project_endpoint_can_be_bound(
        self, session_client, workspace, project, module, create_user
    ):
        """The workspace upload needs an entity type and binds the file
        right away; the project endpoint leaves the file unbound, so the
        bind is what attaches it to a record."""
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        response = session_client.post(
            f"/api/assets/v2/workspaces/{workspace.slug}/projects/{project.id}/",
            {
                "name": "shot.png",
                "type": "image/png",
                "size": 4096,
                "entity_type": FileAsset.EntityTypeContext.TEST_RUN_CASE_EVIDENCE,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        asset_id = response.json()["asset_id"]

        # The file carries no foreign key — the bind is what attaches it.
        asset = FileAsset.objects.get(id=asset_id)
        assert asset.test_run_case_id is None
        assert asset.project_id == project.id

        response = session_client.post(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/{run_case['id']}/attachments/",
            {"asset_id": asset_id},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.content

    def test_a_work_item_attachment_answers_already_bound(
        self, session_client, workspace, project, module, create_user, issue
    ):
        """The file hangs on a work item: the answer names that, not the
        kind of entity type the file carries."""
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        attached = make_asset(project, name="issue-shot.png")
        attached.entity_type = FileAsset.EntityTypeContext.ISSUE_ATTACHMENT
        attached.issue_id = issue.id
        attached.save()

        response = session_client.post(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/{run_case['id']}/attachments/",
            {"asset_id": str(attached.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "asset_already_bound" in error_codes(response)
        assert "asset_not_bindable" not in error_codes(response)

    def test_the_cover_of_a_project_is_refused(
        self, session_client, workspace, project, module, create_user
    ):
        """The cover of a project belongs to the project as a whole — it
        may not be attached to a record of a batch (and the unbind would
        kill the cover)."""
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        cover = make_asset(project, name="cover.png")
        cover.entity_type = FileAsset.EntityTypeContext.PROJECT_COVER
        cover.save()

        response = session_client.post(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/{run_case['id']}/attachments/",
            {"asset_id": str(cover.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "asset_not_bindable" in error_codes(response)

        # The cover is untouched.
        cover.refresh_from_db()
        assert cover.test_run_case_id is None
        assert cover.is_deleted is False

    def test_the_logo_of_a_workspace_is_refused(
        self, session_client, workspace, project, module, create_user
    ):
        """The logo of a workspace belongs to the workspace as a whole."""
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        logo = make_asset(project, name="logo.png")
        logo.entity_type = FileAsset.EntityTypeContext.WORKSPACE_LOGO
        logo.project_id = None
        logo.save()

        response = session_client.post(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/{run_case['id']}/attachments/",
            {"asset_id": str(logo.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "asset_not_bindable" in error_codes(response)

        logo.refresh_from_db()
        assert logo.test_run_case_id is None
        assert logo.is_deleted is False

    def test_an_unknown_file_is_a_404(
        self, session_client, workspace, project, module, create_user
    ):
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        response = session_client.post(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/{run_case['id']}/attachments/",
            {"asset_id": str(uuid4())},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "asset_not_found" in error_codes(response)

    def test_a_malformed_asset_id_is_a_400(
        self, session_client, workspace, project, module, create_user
    ):
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        response = session_client.post(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/{run_case['id']}/attachments/",
            {"asset_id": "not-a-uuid"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "asset_not_found" in error_codes(response)

    def test_an_attachment_of_a_completed_batch_is_refused(
        self, session_client, workspace, project, module, create_user
    ):
        run, run_case = make_valid_batch(session_client, workspace, project, module, create_user)
        assert finalize(session_client, workspace, project, run["id"]).status_code == (
            status.HTTP_200_OK
        )

        asset = make_asset(project)
        url = f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/{run_case['id']}/attachments/"
        response = session_client.post(url, {"asset_id": str(asset.id)}, format="json")
        assert response.status_code == status.HTTP_409_CONFLICT


# --------------------------------------------------------------------------- #
# Adding cases to a batch                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestAddCases:
    def test_a_case_is_added_once(
        self, session_client, workspace, project, module, create_user
    ):
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)

        run_case = add_case(session_client, workspace, project, run["id"], case["id"])
        assert run_case["case_number"] == "TC-CLI-001"
        assert len(run_case["steps"]) == 1

    def test_a_case_cannot_be_added_twice(
        self, session_client, workspace, project, module, create_user
    ):
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        add_case(session_client, workspace, project, run["id"], case["id"])

        response = session_client.post(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/",
            {"test_case_ids": [str(case["id"])]},
            format="json",
        )
        assert response.status_code == status.HTTP_409_CONFLICT
        assert "duplicate_case_number" in error_codes(response)

    def test_a_completed_batch_may_not_be_deleted(
        self, session_client, workspace, project, module, create_user
    ):
        """A finished batch is the history of the execution — it stays."""
        run, _ = make_valid_batch(session_client, workspace, project, module, create_user)
        assert finalize(session_client, workspace, project, run["id"]).status_code == (
            status.HTTP_200_OK
        )

        response = session_client.delete(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/"
        )
        assert response.status_code == status.HTTP_409_CONFLICT
        assert "run_completed" in error_codes(response)

        # The batch is still there.
        assert TestRun.objects.filter(pk=run["id"]).exists()

    def test_a_case_of_another_project_is_refused(
        self, session_client, workspace, project, module, create_user, db
    ):
        create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)

        other_project = Project.objects.create(
            name="Other Project",
            identifier="OTH",
            workspace=workspace,
            created_by=create_user,
            network=2,
        )
        ProjectMember.objects.create(
            project=other_project, member=create_user, role=20, is_active=True
        )
        other_module = TestCaseModule.objects.create(
            project=other_project,
            workspace=workspace,
            code="OTH",
            description="The module of the other project",
        )
        foreign = create_case(session_client, workspace, other_project, other_module)

        response = session_client.post(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/",
            {"test_case_ids": [str(foreign["id"])]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "case_not_in_project" in error_codes(response)

    def test_a_completed_batch_takes_no_more_cases(
        self, session_client, workspace, project, module, create_user
    ):
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        add_case(session_client, workspace, project, run["id"], case["id"])
        run_case = TestRunCase.objects.get(test_run_id=run["id"])

        mark_step(
            session_client,
            workspace,
            project,
            run["id"],
            run_case.id,
            1,
            conclusion="pass",
            actual_value="the form was shown",
        )
        conclude(
            session_client,
            workspace,
            project,
            run["id"],
            run_case.id,
            conclusion="PASS",
            evidence="it worked",
        )
        assert finalize(session_client, workspace, project, run["id"]).status_code == (
            status.HTTP_200_OK
        )

        other = create_case(session_client, workspace, project, module)
        response = session_client.post(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/",
            {"test_case_ids": [str(other["id"])]},
            format="json",
        )
        assert response.status_code == status.HTTP_409_CONFLICT


# --------------------------------------------------------------------------- #
# Removing cases from a batch                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestRemoveCases:
    """Removing a case from a batch is the undo of adding it.

    The case record belongs to the batch alone: removing it takes the
    case out of *this* batch and touches neither the case itself nor the
    other batches that hold it.
    """

    def test_a_case_is_removed_from_the_batch(
        self, session_client, workspace, project, module, create_user
    ):
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        response = remove_case(
            session_client, workspace, project, run["id"], run_case["id"]
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT

        # The record is gone from the batch …
        response = session_client.get(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/"
        )
        assert response.json() == []

        response = session_client.get(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/{run_case['id']}/"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

        # … and the case itself is untouched.
        response = session_client.get(f"{cases_url(workspace.slug, project.id)}{case['id']}/")
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["title"] == "Log in with a valid account"

    def test_the_removed_case_can_be_added_again(
        self, session_client, workspace, project, module, create_user
    ):
        """Remove, then select the case again — the UI flow of a misclick."""
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        assert remove_case(
            session_client, workspace, project, run["id"], run_case["id"]
        ).status_code == (status.HTTP_204_NO_CONTENT)

        again = add_case(session_client, workspace, project, run["id"], case["id"])
        assert again["id"] != run_case["id"]

        response = session_client.get(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/"
        )
        assert [row["id"] for row in response.json()] == [again["id"]]

    def test_a_second_removal_is_a_404(
        self, session_client, workspace, project, module, create_user
    ):
        """The row is already gone: the second removal answers 404, not 500."""
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        assert remove_case(
            session_client, workspace, project, run["id"], run_case["id"]
        ).status_code == (status.HTTP_204_NO_CONTENT)

        response = remove_case(session_client, workspace, project, run["id"], run_case["id"])
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_a_case_of_another_batch_is_a_404(
        self, session_client, workspace, project, module, create_user
    ):
        """The lookup is scoped by the batch of the URL: no cross-batch removal."""
        case = create_case(session_client, workspace, project, module)
        first = create_run(session_client, workspace, project, create_user)
        second = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, first["id"], case["id"])

        response = remove_case(
            session_client, workspace, project, second["id"], run_case["id"]
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

        # The record is still in its own batch.
        response = session_client.get(
            f"{runs_url(workspace.slug, project.id)}{first['id']}/cases/"
        )
        assert [row["id"] for row in response.json()] == [run_case["id"]]

    def test_a_removal_touches_neither_the_case_nor_its_other_batches(
        self, session_client, workspace, project, module, create_user
    ):
        case = create_case(session_client, workspace, project, module)
        first = create_run(session_client, workspace, project, create_user)
        second = create_run(session_client, workspace, project, create_user)
        first_record = add_case(session_client, workspace, project, first["id"], case["id"])
        second_record = add_case(session_client, workspace, project, second["id"], case["id"])

        assert remove_case(
            session_client, workspace, project, first["id"], first_record["id"]
        ).status_code == (status.HTTP_204_NO_CONTENT)

        # The other batch still holds the case.
        response = session_client.get(
            f"{runs_url(workspace.slug, project.id)}{second['id']}/cases/"
        )
        assert [row["id"] for row in response.json()] == [second_record["id"]]

        # And the case row itself is still there.
        response = session_client.get(f"{cases_url(workspace.slug, project.id)}{case['id']}/")
        assert response.status_code == status.HTTP_200_OK

    def test_a_guest_may_not_remove_a_case(
        self, session_client, guest_client, workspace, project, module, create_user
    ):
        """A guest reads the batch but does not change its composition."""
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        response = remove_case(guest_client, workspace, project, run["id"], run_case["id"])
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # The record survived the refused removal.
        response = session_client.get(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/"
        )
        assert [row["id"] for row in response.json()] == [run_case["id"]]

    def test_a_completed_batch_refuses_the_removal(
        self, session_client, workspace, project, module, create_user
    ):
        run, run_case = make_valid_batch(session_client, workspace, project, module, create_user)
        assert finalize(session_client, workspace, project, run["id"]).status_code == (
            status.HTTP_200_OK
        )

        response = remove_case(
            session_client, workspace, project, run["id"], run_case["id"]
        )
        assert response.status_code == status.HTTP_409_CONFLICT
        assert "run_completed" in error_codes(response)

        # The record is the history of the execution — it stays.
        response = session_client.get(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/"
        )
        assert [row["id"] for row in response.json()] == [run_case["id"]]


# --------------------------------------------------------------------------- #
# ⑦ + ⑧ The service principal on the batch side                              #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestRunServicePrincipal:
    def test_a_service_principal_runs_a_batch(
        self, sp_key_client, workspace, project, module, create_user, sp_granted
    ):
        case = create_case(sp_key_client, workspace, project, module)
        assert case["number"] == "TC-CLI-001"

        run = create_run(sp_key_client, workspace, project, create_user)
        assert run["number"] == "RUN-20261109-01"

        run_case = add_case(sp_key_client, workspace, project, run["id"], case["id"])
        assert run_case["case_number"] == "TC-CLI-001"

        response = mark_step(
            sp_key_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            1,
            conclusion="pass",
            actual_value="the form was shown",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_the_write_is_credited_to_the_shadow_bot(
        self, sp_key_client, workspace, project, module, create_user, sp_granted
    ):
        case = create_case(sp_key_client, workspace, project, module)
        run = create_run(sp_key_client, workspace, project, create_user)
        run_case = add_case(sp_key_client, workspace, project, run["id"], case["id"])
        mark_step(
            sp_key_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            1,
            conclusion="pass",
            actual_value="the form was shown",
        )

        record = TestRunStepRecord.objects.get(test_run_case_id=run_case["id"])
        assert record.created_by is not None
        assert record.created_by.is_bot is True
        assert record.created_by.bot_type == "SERVICE_PRINCIPAL"

    def test_a_service_principal_may_not_write_a_foreign_batch(
        self, sp_key_client, session_client, workspace, project, module, create_user, sp_granted
    ):
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        response = mark_step(
            sp_key_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            1,
            conclusion="fail",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_human_admin_may_write_the_batch_of_the_bot(
        self, sp_key_client, session_client, workspace, project, module, create_user, sp_granted
    ):
        case = create_case(sp_key_client, workspace, project, module)
        run = create_run(sp_key_client, workspace, project, create_user)
        run_case = add_case(sp_key_client, workspace, project, run["id"], case["id"])

        response = mark_step(
            session_client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            1,
            conclusion="pass",
            actual_value="the form was shown",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_a_service_principal_removes_a_case_with_the_update_scope(
        self, sp_key_client, workspace, project, module, create_user, sp_granted
    ):
        """``sp_granted`` holds no ``delete`` scope at all.

        The removal of a case is judged as an ``update`` of the batch's
        composition — the same action the add asks for — so the principal
        that may add a case can take it out again.
        """
        case = create_case(sp_key_client, workspace, project, module)
        run = create_run(sp_key_client, workspace, project, create_user)
        run_case = add_case(sp_key_client, workspace, project, run["id"], case["id"])

        response = remove_case(sp_key_client, workspace, project, run["id"], run_case["id"])
        assert response.status_code == status.HTTP_204_NO_CONTENT, response.content

        response = sp_key_client.get(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/"
        )
        assert response.json() == []

    def test_a_service_principal_without_the_scope_is_refused(
        self, session_client, sp_key_client, workspace, project, module, create_user, sp
    ):
        """A grant without the ``update test_run`` scope is a 403."""
        ProjectGrant.objects.create(
            service_principal=sp, project=project, role_cap=15, is_active=True
        )
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])

        response = remove_case(sp_key_client, workspace, project, run["id"], run_case["id"])
        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
        assert response.json() == {"error": "You don't have the required permissions."}

        # The record survived the refused removal.
        response = session_client.get(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/"
        )
        assert [row["id"] for row in response.json()] == [run_case["id"]]


# --------------------------------------------------------------------------- #
# The upload of the files of a batch (the existing v2 asset endpoint)         #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestAssetUpload:
    def test_a_file_is_uploaded_into_the_record_of_a_step(
        self, session_client, workspace, project, module, create_user
    ):
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, run["id"], case["id"])
        record = TestRunStepRecord.objects.get(test_run_case_id=run_case["id"])

        response = session_client.post(
            f"/api/assets/v2/workspaces/{workspace.slug}/",
            {
                "name": "shot.png",
                "type": "image/png",
                "size": 4096,
                "entity_type": FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD,
                "entity_identifier": str(record.id),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["asset_id"]

        asset = FileAsset.objects.get(id=body["asset_id"])
        assert asset.test_run_step_record_id == record.id
        # The file belongs to the project of the record — an asset
        # without a project is readable by every member of the
        # workspace.
        assert asset.project_id == project.id
        assert asset.entity_type == FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD

    def test_the_upload_into_a_completed_batch_is_refused(
        self, session_client, workspace, project, module, create_user
    ):
        run, run_case = make_valid_batch(session_client, workspace, project, module, create_user)
        assert finalize(session_client, workspace, project, run["id"]).status_code == (
            status.HTTP_200_OK
        )
        record = TestRunStepRecord.objects.get(test_run_case_id=run_case["id"])

        response = session_client.post(
            f"/api/assets/v2/workspaces/{workspace.slug}/",
            {
                "name": "shot.png",
                "type": "image/png",
                "size": 4096,
                "entity_type": FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD,
                "entity_identifier": str(record.id),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_409_CONFLICT

    def test_the_file_of_a_completed_batch_may_not_be_deleted(
        self, session_client, workspace, project, module, create_user
    ):
        run, run_case = make_valid_batch(session_client, workspace, project, module, create_user)
        record = TestRunStepRecord.objects.get(test_run_case_id=run_case["id"])
        asset = make_asset(project)
        asset.test_run_step_record = record
        asset.save()

        assert finalize(session_client, workspace, project, run["id"]).status_code == (
            status.HTTP_200_OK
        )

        response = session_client.delete(
            f"/api/assets/v2/workspaces/{workspace.slug}/{asset.id}/"
        )
        assert response.status_code == status.HTTP_409_CONFLICT
