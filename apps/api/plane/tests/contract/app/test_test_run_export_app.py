# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for the raw-data export of a batch (M95 / TCASE-EXPORT).

The export is a **core, default-on** feature: the document it answers with
is the record of an execution that other consumers (the PDF renderer of the
report, the CLI) read, so the tests pin it:

* the top-level, per-batch, per-case, per-step and per-attachment key sets
  are exact — a consumer may rely on every one of them and on nothing else;
* ``schema_version`` is part of the document, so a later shape is a new
  version instead of a silent change;
* the files of both levels travel with a ``url`` that comes from the
  existing asset-signing path (a short-lived signed URL), never a bucket
  path and never the bytes inline;
* ``summary`` is the answer of ``/summary/`` for the same batch — the
  export never recounts what :func:`summarize_run` already counted;
* the export answers the same readers as the read endpoints of the batch:
  ADMIN, MEMBER and GUEST of the project, and a service principal holding
  the ``read test_run`` scope; an unknown batch or one of another project
  is a 404;
* the endpoint is a GET: a write on it is a 405.
"""
from __future__ import annotations

from unittest import mock
from uuid import uuid4

import pytest
from rest_framework import status

from plane.db.models import (
    APIToken,
    FileAsset,
    Project,
    ProjectMember,
    TestCaseModule,
    User,
    WorkspaceMember,
)
from plane.service_principals.constants import PrincipalType
from plane.service_principals.models import (
    ProjectGrant,
    ServicePrincipal,
    ServiceScope,
)


#: Where the endpoint under test imports the storage class it signs with.
S3_STORAGE_PATH = "plane.app.views.test_run.export.S3Storage"

SIGNED_URL = "https://signed.example/get"


# --------------------------------------------------------------------------- #
# Fixtures / helpers                                                          #
# --------------------------------------------------------------------------- #


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="Export Project",
        identifier="EXP",
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
def guest_client(db, workspace, project):
    """A client of a project **guest** (role 5), who may read the batches."""
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


@pytest.fixture
def sp(db, workspace, create_user):
    return ServicePrincipal.objects.create(
        workspace=workspace,
        owner=create_user,
        name="export-bot",
    )


@pytest.fixture
def sp_key_client(db, sp):
    from rest_framework.test import APIClient

    token = APIToken.objects.create(
        user=sp.owner,
        label="svc:export-bot",
        user_type=1,
        is_service=True,
        principal_type=PrincipalType.SERVICE,
        service_principal=sp,
        workspace=sp.workspace,
        token="plane_svc_export_bot",
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=token.token)
    return client


@pytest.fixture
def sp_read_grant(db, sp, project):
    """The SP may read the batches of the project, and nothing else."""
    ServiceScope.objects.create(
        service_principal=sp,
        project=project,
        resource_type="test_run",
        action="read",
    )
    ProjectGrant.objects.create(
        service_principal=sp,
        project=project,
        role_cap=15,
        is_active=True,
    )
    return sp


@pytest.fixture
def sp_grant_without_scopes(db, sp, project):
    """The SP holds the project, but no scope at all."""
    ProjectGrant.objects.create(
        service_principal=sp,
        project=project,
        role_cap=15,
        is_active=True,
    )
    return sp


def runs_url(slug, project_id):
    return f"/api/workspaces/{slug}/projects/{project_id}/test-runs/"


def cases_url(slug, project_id):
    return f"/api/workspaces/{slug}/projects/{project_id}/test-cases/"


def export_url(slug, project_id, run_id):
    return f"{runs_url(slug, project_id)}{run_id}/export/"


def summary_url(slug, project_id, run_id):
    return f"{runs_url(slug, project_id)}{run_id}/summary/"


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
            {"action": "Enter the account", "expected_result": "The main window opens"},
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
    response = client.patch(
        f"{runs_url(workspace.slug, project.id)}{run_id}/cases/{run_case_id}/steps/{sequence_id}/",
        payload,
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    return response.json()


def conclude(client, workspace, project, run_id, run_case_id, **payload):
    response = client.patch(
        f"{runs_url(workspace.slug, project.id)}{run_id}/cases/{run_case_id}/",
        payload,
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    return response.json()


def make_asset(project, name="shot.png", entity_type=FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD):
    return FileAsset.objects.create(
        attributes={"name": name, "type": "image/png", "size": 4096},
        asset=f"{project.workspace_id}/{uuid4().hex}-{name}",
        size=4096,
        workspace=project.workspace,
        project=project,
        entity_type=entity_type,
        is_uploaded=True,
    )


def bind_step_asset(client, workspace, project, run_id, run_case_id, sequence_id, asset):
    response = client.post(
        f"{runs_url(workspace.slug, project.id)}{run_id}/cases/{run_case_id}/"
        f"steps/{sequence_id}/attachments/",
        {"asset_id": str(asset.id)},
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED, response.content
    return response.json()


def bind_case_asset(client, workspace, project, run_id, run_case_id, asset):
    response = client.post(
        f"{runs_url(workspace.slug, project.id)}{run_id}/cases/{run_case_id}/attachments/",
        {"asset_id": str(asset.id)},
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED, response.content
    return response.json()


def exported(client, workspace, project, run_id):
    """GET the export of a batch with the asset signer mocked.

    The signing path is mocked in every test that reads the export of a
    batch holding files: the URL it mints is the contract, not the bytes
    behind it. The positive tests assert the exact URL, so the mock also
    proves the endpoint went through the signing path at all.
    """
    with mock.patch(S3_STORAGE_PATH) as mock_storage:
        mock_storage.return_value.generate_presigned_url.return_value = SIGNED_URL
        response = client.get(export_url(workspace.slug, project.id, run_id))
    return response, mock_storage


def make_batch(client, workspace, project, module, user):
    """One executed case: step 1 failed with a file, step 2 passed, case failed.

    The case carries one file at each level — one on the failed step (the
    measured value) and one on the case (the evidence).
    """
    case = create_case(client, workspace, project, module)
    run = create_run(client, workspace, project, user)
    run_case = add_case(client, workspace, project, run["id"], case["id"])

    step_asset = make_asset(project, name="step-shot.png")
    case_asset = make_asset(
        project,
        name="evidence.png",
        entity_type=FileAsset.EntityTypeContext.TEST_RUN_CASE_EVIDENCE,
    )
    bind_step_asset(client, workspace, project, run["id"], run_case["id"], 1, step_asset)
    bind_case_asset(client, workspace, project, run["id"], run_case["id"], case_asset)

    mark_step(
        client,
        workspace,
        project,
        run["id"],
        run_case["id"],
        1,
        conclusion="fail",
        actual_value="the login form never appeared",
    )
    mark_step(
        client,
        workspace,
        project,
        run["id"],
        run_case["id"],
        2,
        conclusion="pass",
        actual_value="the main window opened",
    )
    conclude(
        client,
        workspace,
        project,
        run["id"],
        run_case["id"],
        conclusion="FAIL",
        evidence="the form stayed blank",
        remarks="happened on the second launch",
        actual_result_summary="the login form never appeared",
    )
    return run, run_case, step_asset, case_asset


# --------------------------------------------------------------------------- #
# The shape of the document                                                   #
# --------------------------------------------------------------------------- #


TOP_LEVEL_KEYS = {"schema_version", "batch", "cases", "summary"}

BATCH_KEYS = {
    "number",
    "run_date",
    "version_number",
    "build_number",
    "commit_hash",
    "environment",
    "executed_by_name",
    "status",
    "retest_of",
    "retest_of_number",
    "created_at",
    "updated_at",
}

CASE_KEYS = {
    "id",
    "case_number",
    "case_title",
    "case_module_code",
    "case_priority",
    "case_purpose",
    "case_object_overview",
    "case_preconditions",
    "case_environment",
    "conclusion",
    "evidence",
    "remarks",
    "actual_result_summary",
    "na_reason",
    "na_criteria_source",
    "manual_executor",
    "manual_condition",
    "steps",
    "attachments",
}

STEP_KEYS = {
    "sequence_id",
    "action",
    "expected_result",
    "conclusion",
    "actual_value",
    "attachments",
}

ATTACHMENT_KEYS = {"id", "name", "mime_type", "size", "url"}

SUMMARY_KEYS = {"total", "pass", "fail", "na", "manual", "deviations"}


@pytest.mark.contract
@pytest.mark.django_db
class TestExportShape:
    def test_the_document_pins_its_key_sets(
        self, session_client, workspace, project, module, create_user
    ):
        """Every level of the document carries exactly the pinned keys.

        A consumer reads this document without asking the server again;
        an added key is as much a change of the contract as a removed one,
        so both directions are asserted.
        """
        run, _, _, _ = make_batch(session_client, workspace, project, module, create_user)

        response, _ = exported(session_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_200_OK, response.content
        document = response.json()

        assert set(document) == TOP_LEVEL_KEYS
        assert set(document["batch"]) == BATCH_KEYS
        assert set(document["summary"]) == SUMMARY_KEYS

        assert len(document["cases"]) == 1
        case = document["cases"][0]
        assert set(case) == CASE_KEYS

        assert [step["sequence_id"] for step in case["steps"]] == [1, 2]
        for step in case["steps"]:
            assert set(step) == STEP_KEYS

        assert len(case["steps"][0]["attachments"]) == 1
        assert set(case["steps"][0]["attachments"][0]) == ATTACHMENT_KEYS
        assert len(case["attachments"]) == 1
        assert set(case["attachments"][0]) == ATTACHMENT_KEYS

    def test_the_schema_version_starts_at_one(
        self, session_client, workspace, project, module, create_user
    ):
        run = create_run(session_client, workspace, project, create_user)
        response, _ = exported(session_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.json()["schema_version"] == 1

    def test_the_batch_header_carries_what_the_batch_recorded(
        self, session_client, workspace, project, module, create_user
    ):
        run, _, _, _ = make_batch(session_client, workspace, project, module, create_user)
        session_client.patch(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/",
            {"status": "in_progress"},
            format="json",
        )

        # A re-test names the batch it re-tests, by id and by number.
        retest = create_run(
            session_client,
            workspace,
            project,
            create_user,
            run_date="2026-11-10",
            retest_of=run["id"],
        )

        response, _ = exported(session_client, workspace, project, run["id"])
        header = response.json()["batch"]
        assert header["number"] == "RUN-20261109-01"
        assert header["run_date"] == "2026-11-09"
        assert header["version_number"] == "1.2.3"
        assert header["build_number"] == "b20261109"
        assert header["commit_hash"] == "abcdef1234"
        assert header["environment"] == "Windows 10, pbot 1.2.3"
        assert header["executed_by_name"]
        assert header["status"] == "in_progress"
        assert header["retest_of"] is None
        assert header["retest_of_number"] is None
        assert header["created_at"]
        assert header["updated_at"]

        response, _ = exported(session_client, workspace, project, retest["id"])
        header = response.json()["batch"]
        assert header["number"] == "RUN-20261110-01"
        assert header["retest_of"] == run["id"]
        assert header["retest_of_number"] == "RUN-20261109-01"

    def test_a_batch_without_cases_exports_an_empty_list(
        self, session_client, workspace, project, create_user
    ):
        run = create_run(session_client, workspace, project, create_user)
        response, _ = exported(session_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_200_OK, response.content
        document = response.json()
        assert document["cases"] == []
        assert document["summary"] == {
            "total": 0,
            "pass": 0,
            "fail": 0,
            "na": 0,
            "manual": 0,
            "deviations": [],
        }


# --------------------------------------------------------------------------- #
# The case record, its steps and its files                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestExportContent:
    def test_a_failed_step_and_the_files_of_both_levels_are_exported(
        self, session_client, workspace, project, module, create_user
    ):
        run, run_case, step_asset, case_asset = make_batch(
            session_client, workspace, project, module, create_user
        )

        response, mock_storage = exported(session_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_200_OK, response.content
        case = response.json()["cases"][0]

        # The record the row was built from, so a rendered document can
        # cross-reference it back to the row.
        assert case["id"] == run_case["id"]

        # The snapshot of the case header, as stored on the record.
        assert case["case_number"] == "TC-CLI-001"
        assert case["case_module_code"] == "CLI"
        assert case["case_title"] == "Log in with a valid account"
        assert case["case_purpose"] == "Make sure the login path of pbot works"
        assert case["case_object_overview"] == "The login form of the desktop client"
        assert case["case_priority"] == "P1"
        assert case["case_preconditions"] == "The account exists and is active"
        assert case["case_environment"] == "Windows 10, pbot 1.2.3"

        # The execution fields.
        assert case["conclusion"] == "FAIL"
        assert case["evidence"] == "the form stayed blank"
        assert case["remarks"] == "happened on the second launch"
        assert case["actual_result_summary"] == "the login form never appeared"
        assert case["na_reason"] == ""
        assert case["na_criteria_source"] == ""
        assert case["manual_executor"] == ""
        assert case["manual_condition"] == ""

        # The step table, ordered by the sequence it was executed in, with
        # the snapshot text of the action and of the expected result.
        first, second = case["steps"]
        assert first["action"] == "Start pbot"
        assert first["expected_result"] == "The login form is shown"
        assert first["conclusion"] == "fail"
        assert first["actual_value"] == "the login form never appeared"
        assert second["action"] == "Enter the account"
        assert second["expected_result"] == "The main window opens"
        assert second["conclusion"] == "pass"
        assert second["actual_value"] == "the main window opened"

        # Both files travel with the record they hang on, and both URLs are
        # the signed URL of the existing asset-signing path.
        step_files = first["attachments"]
        assert len(step_files) == 1
        assert step_files[0]["id"] == str(step_asset.id)
        assert step_files[0]["name"] == "step-shot.png"
        assert step_files[0]["mime_type"] == "image/png"
        assert step_files[0]["size"] == 4096
        assert step_files[0]["url"] == SIGNED_URL

        case_files = case["attachments"]
        assert len(case_files) == 1
        assert case_files[0]["id"] == str(case_asset.id)
        assert case_files[0]["name"] == "evidence.png"
        assert case_files[0]["url"] == SIGNED_URL

        # The file that was exported with a failed step is the file that
        # was bound to it — one signature per file, none for a foreign row.
        signed = mock_storage.return_value.generate_presigned_url
        assert signed.call_count == 2
        signed_ids = {
            call.kwargs["object_name"]
            for call in signed.call_args_list
        }
        assert step_asset.asset in signed_ids
        assert case_asset.asset in signed_ids

    def test_a_deleted_file_is_not_exported(
        self, session_client, workspace, project, module, create_user
    ):
        run, run_case, step_asset, _ = make_batch(
            session_client, workspace, project, module, create_user
        )
        session_client.delete(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/{run_case['id']}/"
            f"steps/1/attachments/{step_asset.id}/"
        )

        response, mock_storage = exported(session_client, workspace, project, run["id"])
        case = response.json()["cases"][0]
        assert case["steps"][0]["attachments"] == []
        assert mock_storage.return_value.generate_presigned_url.call_count == 1


# --------------------------------------------------------------------------- #
# The summary block                                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestExportSummary:
    def test_the_summary_is_the_answer_of_the_summary_endpoint(
        self, session_client, workspace, project, module, create_user
    ):
        """The export never recounts: it hands on what ``/summary/`` answers.

        A batch of one PASS case and one FAIL case with a failed step: the
        numbers and the deviation list have to be identical on both
        surfaces, so a count can never drift from the one the report of the
        standard (STD-SW-003 §6.2 rule 3) shows.
        """
        passing = create_case(
            session_client,
            workspace,
            project,
            module,
            title="Log in with a valid account",
            steps=[{"action": "Start pbot", "expected_result": "The login form is shown"}],
        )
        failing = create_case(
            session_client,
            workspace,
            project,
            module,
            title="Log in with a wrong password",
            steps=[{"action": "Enter a wrong password", "expected_result": "An error is shown"}],
        )
        run = create_run(session_client, workspace, project, create_user)
        run_passing = add_case(session_client, workspace, project, run["id"], passing["id"])
        run_failing = add_case(session_client, workspace, project, run["id"], failing["id"])

        mark_step(
            session_client,
            workspace,
            project,
            run["id"],
            run_passing["id"],
            1,
            conclusion="pass",
            actual_value="the login form was shown",
        )
        conclude(
            session_client,
            workspace,
            project,
            run["id"],
            run_passing["id"],
            conclusion="PASS",
            evidence="the login succeeded",
        )

        mark_step(
            session_client,
            workspace,
            project,
            run["id"],
            run_failing["id"],
            1,
            conclusion="fail",
            actual_value="no error was shown",
        )
        conclude(
            session_client,
            workspace,
            project,
            run["id"],
            run_failing["id"],
            conclusion="FAIL",
            evidence="the wrong password logged in",
            actual_result_summary="no error was shown",
        )

        response, _ = exported(session_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_200_OK, response.content
        exported_summary = response.json()["summary"]

        summary_response = session_client.get(summary_url(workspace.slug, project.id, run["id"]))
        assert summary_response.status_code == status.HTTP_200_OK, summary_response.content

        assert exported_summary == summary_response.json()
        assert exported_summary["total"] == 2
        assert exported_summary["pass"] == 1
        assert exported_summary["fail"] == 1
        assert [entry["case_number"] for entry in exported_summary["deviations"]] == [
            "TC-CLI-002"
        ]


# --------------------------------------------------------------------------- #
# Who may read it, and what answers an unknown batch                          #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestExportAccess:
    def test_a_guest_of_the_project_may_export(
        self, session_client, guest_client, workspace, project, module, create_user
    ):
        run, _, _, _ = make_batch(session_client, workspace, project, module, create_user)
        response, _ = exported(guest_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_200_OK, response.content

    def test_an_unknown_batch_is_a_404(
        self, session_client, workspace, project, create_user
    ):
        response, _ = exported(session_client, workspace, project, uuid4())
        assert response.status_code == status.HTTP_404_NOT_FOUND, response.content

    def test_a_batch_of_another_project_is_a_404(
        self, session_client, workspace, project, module, create_user
    ):
        run, _, _, _ = make_batch(session_client, workspace, project, module, create_user)
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

        # The batch exists, but not under this project: the reader who may
        # see both projects still gets a 404 on the wrong one.
        response, _ = exported(session_client, workspace, other_project, run["id"])
        assert response.status_code == status.HTTP_404_NOT_FOUND, response.content

    def test_the_export_is_a_get_only(
        self, session_client, workspace, project, module, create_user
    ):
        run, run_case, _, _ = make_batch(session_client, workspace, project, module, create_user)
        url = export_url(workspace.slug, project.id, run["id"])

        for method in ("post", "put", "patch", "delete"):
            response = getattr(session_client, method)(url, {}, format="json")
            assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED, (
                method,
                response.content,
            )

        # The batch is untouched by the refused writes.
        response, _ = exported(session_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["cases"][0]["conclusion"] == "FAIL"


# --------------------------------------------------------------------------- #
# The service principal                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestExportServicePrincipal:
    def test_a_service_principal_with_the_read_scope_may_export(
        self,
        session_client,
        sp_key_client,
        workspace,
        project,
        module,
        create_user,
        sp_read_grant,
    ):
        run, _, step_asset, case_asset = make_batch(
            session_client, workspace, project, module, create_user
        )

        with mock.patch(S3_STORAGE_PATH) as mock_storage:
            mock_storage.return_value.generate_presigned_url.return_value = SIGNED_URL
            response = sp_key_client.get(export_url(workspace.slug, project.id, run["id"]))

        assert response.status_code == status.HTTP_200_OK, response.content
        document = response.json()
        assert document["batch"]["number"] == "RUN-20261109-01"
        case = document["cases"][0]
        assert case["attachments"][0]["id"] == str(case_asset.id)
        assert case["steps"][0]["attachments"][0]["id"] == str(step_asset.id)
        assert document["summary"]["total"] == 1

    def test_a_service_principal_without_the_read_scope_is_refused(
        self,
        session_client,
        sp_key_client,
        workspace,
        project,
        module,
        create_user,
        sp_grant_without_scopes,
    ):
        run, _, _, _ = make_batch(session_client, workspace, project, module, create_user)

        response = sp_key_client.get(export_url(workspace.slug, project.id, run["id"]))
        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
