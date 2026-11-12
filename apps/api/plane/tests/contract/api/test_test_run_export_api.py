# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: the raw-data export of a batch on the v1 surface (M100).

The pbot CLI talks **only** to the public v1 API, so the export an operator
reads from the app surface has to be reachable, and identical, on v1. The
v1 endpoint is a mirror: the document is built by the app-side builders
(:func:`plane.app.views.test_run.export.export_document` over
:class:`~plane.app.views.test_run.export.AssetUrlSigner`), not re-derived,
so the two surfaces cannot drift. These tests pin exactly that:

* the top-level, per-case, per-step and per-attachment key sets are the
  app surface's — a consumer may rely on every one of them and on nothing
  else — and ``schema_version`` is part of the document;
* the v1 answer and the app answer are the **same document** for the same
  batch;
* a file travels with a short-lived signed URL of the existing
  asset-signing path: an absolute URL of the storage host carrying a
  signature query, never the bucket key on its own;
* ``summary`` equals ``GET …/summary/`` for the same batch — the export
  never recounts what :func:`summarize_run` already counted;
* the reader is the reader of the v1 batch reads: a service principal
  holding the ``read test_run`` scope (the CLI's principal), 403 without
  it; an unknown batch and another project's batch are 404;
* the endpoint is a GET: a write on it is a 405.

Every request is made with a service-principal token, and each test mints
its own token: the v1 throttle is keyed on the token string and its
counter lives in the cache, so a shared token would put the whole file
into one bucket.
"""

from __future__ import annotations

from unittest import mock
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import (
    APIToken,
    FileAsset,
    Project,
    ProjectMember,
    TestCaseModule,
)
from plane.service_principals.constants import PrincipalType
from plane.service_principals.models import (
    ProjectGrant,
    ServicePrincipal,
    ServiceScope,
)


#: The v1 prefix of the feature, and the app prefix the export has to
#: agree with.
BASE = "/api/v1/workspaces/{slug}/projects/{project_id}"
APP_BASE = "/api/workspaces/{slug}/projects/{project_id}"

#: Where both surfaces import the storage class they sign with.
S3_STORAGE_PATH = "plane.app.views.test_run.export.S3Storage"

SIGNED_URL = "https://signed.example/get"


# --------------------------------------------------------------------------- #
# URLs                                                                        #
# --------------------------------------------------------------------------- #


def runs_url(slug, project_id, base=BASE):
    return f"{base.format(slug=slug, project_id=project_id)}/test-runs/"


def cases_url(slug, project_id, base=BASE):
    return f"{base.format(slug=slug, project_id=project_id)}/test-cases/"


def export_url(slug, project_id, run_id, base=BASE):
    return f"{runs_url(slug, project_id, base)}{run_id}/export/"


def summary_url(slug, project_id, run_id, base=BASE):
    return f"{runs_url(slug, project_id, base)}{run_id}/summary/"


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="V1 Export Project",
        identifier="VEX",
        workspace=workspace,
        created_by=create_user,
        network=2,
    )
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    return project


@pytest.fixture
def other_project(db, workspace, create_user):
    project = Project.objects.create(
        name="V1 Export Other Project",
        identifier="VEO",
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


def create_principal(workspace, create_user, token, name):
    """A service principal of the workspace and the token that authenticates it."""
    principal = ServicePrincipal.objects.create(
        workspace=workspace,
        owner=create_user,
        name=name,
    )
    APIToken.objects.create(
        label=f"svc:{name}",
        token=token,
        user=create_user,
        principal_type=PrincipalType.SERVICE,
        service_principal=principal,
        workspace=workspace,
    )
    return principal


def client_for(token):
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=token)
    return client


def grant_scopes(principal, project, actions=("read", "create", "update", "delete")):
    for resource_type in ("test_case", "test_run"):
        for action in actions:
            ServiceScope.objects.create(
                service_principal=principal,
                project=project,
                resource_type=resource_type,
                action=action,
            )


def grant_project(principal, project, role_cap):
    return ProjectGrant.objects.create(
        service_principal=principal,
        project=project,
        role_cap=role_cap,
        is_active=True,
    )


@pytest.fixture
def sp_token():
    """A service token of its own for every test (see the module docstring)."""
    return f"plane_svc_token_run-export-{uuid4().hex}"


@pytest.fixture
def sp(db, workspace, create_user, sp_token):
    """The principal the whole file drives: the CLI's own kind of principal."""
    return create_principal(workspace, create_user, sp_token, "run-export-bot")


@pytest.fixture
def sp_client(sp_token):
    return client_for(sp_token)


@pytest.fixture
def granted_sp(sp, project):
    """A member-tier principal that may write and read the feature."""
    grant_scopes(sp, project)
    grant_project(sp, project, role_cap=15)
    return sp


@pytest.fixture
def admin_client(db, workspace, create_user, project):
    """A client of a second, admin-tier principal.

    The 405 test needs a principal the authz chain does not refuse at the
    permission step: a member-tier grant is fail-closed on an update or
    delete with no row to own, so it would answer 403 before the method
    gate of the view is ever reached.
    """
    token = f"plane_svc_token_run-export-admin-{uuid4().hex}"
    principal = create_principal(workspace, create_user, token, "run-export-admin-bot")
    grant_scopes(principal, project)
    grant_project(principal, project, role_cap=20)
    return client_for(token)


@pytest.fixture
def unscoped_client(db, workspace, create_user, project):
    """A client of a principal that holds the project but no scope at all."""
    token = f"plane_svc_token_run-export-unscoped-{uuid4().hex}"
    principal = create_principal(workspace, create_user, token, "run-export-unscoped-bot")
    grant_project(principal, project, role_cap=15)
    return client_for(token)


# --------------------------------------------------------------------------- #
# Building a batch through v1                                                 #
# --------------------------------------------------------------------------- #


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


def exported(client, workspace, project, run_id):
    """GET the v1 export of a batch with the asset signer mocked.

    Only the tests that read a document holding files mock the signer;
    the URL it mints in production is asserted against the real signing
    path in ``test_an_attachment_url_is_a_signed_url``.
    """
    with mock.patch(S3_STORAGE_PATH) as mock_storage:
        mock_storage.return_value.generate_presigned_url.return_value = SIGNED_URL
        response = client.get(export_url(workspace.slug, project.id, run_id))
    return response, mock_storage


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
class TestExportShapeOnV1:
    def test_the_document_pins_its_key_sets(
        self, sp_client, workspace, project, module, granted_sp
    ):
        """Every level of the document carries exactly the pinned keys.

        A consumer reads this document without asking the server again;
        an added key is as much a change of the contract as a removed one,
        so both directions are pinned by the set comparison.
        """
        run, _, _, _ = make_batch(
            sp_client, workspace, project, module, granted_sp.ensure_bot_user()
        )

        response, _ = exported(sp_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_200_OK, response.content
        document = response.json()

        assert set(document) == TOP_LEVEL_KEYS
        assert document["schema_version"] == 1
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

    def test_the_v1_document_is_the_app_document(
        self, sp_client, workspace, project, module, granted_sp
    ):
        """The mirror is not a reimplementation: one batch, one document.

        Both surfaces are read with the same principal and the same
        signer, so any difference in the loader — the order of the cases,
        the header of the batch, the snapshot of a step — shows up here.
        """
        run, _, _, _ = make_batch(
            sp_client, workspace, project, module, granted_sp.ensure_bot_user()
        )

        with mock.patch(S3_STORAGE_PATH) as mock_storage:
            mock_storage.return_value.generate_presigned_url.return_value = SIGNED_URL
            v1_response = sp_client.get(export_url(workspace.slug, project.id, run["id"]))
            app_response = sp_client.get(
                export_url(workspace.slug, project.id, run["id"], base=APP_BASE)
            )

        assert v1_response.status_code == status.HTTP_200_OK, v1_response.content
        assert app_response.status_code == status.HTTP_200_OK, app_response.content
        assert v1_response.json() == app_response.json()

    def test_a_batch_without_cases_exports_an_empty_list(
        self, sp_client, workspace, project, granted_sp
    ):
        run = create_run(sp_client, workspace, project, granted_sp.ensure_bot_user())
        response, _ = exported(sp_client, workspace, project, run["id"])
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
# The files of the document                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestExportFilesOnV1:
    def test_an_attachment_url_is_a_signed_url(
        self, sp_client, workspace, project, module, granted_sp
    ):
        """A file travels as a signed URL of the storage host, not as a key.

        The signing path is the real one here (no mock): the document must
        carry an absolute URL of the storage endpoint with a signature in
        its query, so a consumer can fetch the file without credentials
        and without knowing the bucket layout. What must never appear is
        the storage key on its own.
        """
        run, _, step_asset, case_asset = make_batch(
            sp_client, workspace, project, module, granted_sp.ensure_bot_user()
        )

        response = sp_client.get(export_url(workspace.slug, project.id, run["id"]))
        assert response.status_code == status.HTTP_200_OK, response.content
        case = response.json()["cases"][0]

        for attachment, asset in (
            (case["steps"][0]["attachments"][0], step_asset),
            (case["attachments"][0], case_asset),
        ):
            url = attachment["url"]
            parsed = urlparse(url)
            assert parsed.scheme in ("http", "https"), url
            assert parsed.netloc, url
            query = parse_qs(parsed.query)
            assert "X-Amz-Signature" in query, url
            assert "X-Amz-Credential" in query, url
            # The key on its own is not a URL: the fallback of the signer
            # hands out the API path, never the bucket key.
            assert url != asset.asset.name, url
            # Signed for rendering in the document, not for a download.
            assert "inline" in parsed.query, url


# --------------------------------------------------------------------------- #
# The summary block                                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestExportSummaryOnV1:
    def test_the_summary_is_the_answer_of_the_summary_endpoint(
        self, sp_client, workspace, project, module, granted_sp
    ):
        """The export never recounts: it hands on what ``/summary/`` answers."""
        passing = create_case(
            sp_client,
            workspace,
            project,
            module,
            title="Log in with a valid account",
            steps=[{"action": "Start pbot", "expected_result": "The login form is shown"}],
        )
        failing = create_case(
            sp_client,
            workspace,
            project,
            module,
            title="Log in with a wrong password",
            steps=[{"action": "Enter a wrong password", "expected_result": "An error is shown"}],
        )
        run = create_run(sp_client, workspace, project, granted_sp.ensure_bot_user())
        run_passing = add_case(sp_client, workspace, project, run["id"], passing["id"])
        run_failing = add_case(sp_client, workspace, project, run["id"], failing["id"])

        mark_step(
            sp_client,
            workspace,
            project,
            run["id"],
            run_passing["id"],
            1,
            conclusion="pass",
            actual_value="the login form was shown",
        )
        conclude(
            sp_client,
            workspace,
            project,
            run["id"],
            run_passing["id"],
            conclusion="PASS",
            evidence="the login succeeded",
        )

        mark_step(
            sp_client,
            workspace,
            project,
            run["id"],
            run_failing["id"],
            1,
            conclusion="fail",
            actual_value="no error was shown",
        )
        conclude(
            sp_client,
            workspace,
            project,
            run["id"],
            run_failing["id"],
            conclusion="FAIL",
            evidence="the wrong password logged in",
            actual_result_summary="no error was shown",
        )

        response, _ = exported(sp_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_200_OK, response.content
        exported_summary = response.json()["summary"]

        summary_response = sp_client.get(summary_url(workspace.slug, project.id, run["id"]))
        assert summary_response.status_code == status.HTTP_200_OK, summary_response.content

        assert exported_summary == summary_response.json()
        assert exported_summary["total"] == 2
        assert exported_summary["pass"] == 1
        assert exported_summary["fail"] == 1
        assert [entry["case_number"] for entry in exported_summary["deviations"]] == [
            "TC-CLI-002"
        ]


# --------------------------------------------------------------------------- #
# Who may read it, and what answers an unknown or a foreign batch              #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestExportAccessOnV1:
    def test_an_unknown_batch_is_a_404(self, sp_client, workspace, project, granted_sp):
        response = sp_client.get(export_url(workspace.slug, project.id, uuid4()))
        assert response.status_code == status.HTTP_404_NOT_FOUND, response.content

    def test_a_batch_of_another_project_is_a_404(
        self, sp_client, workspace, project, other_project, module, granted_sp, sp
    ):
        """A principal of both projects still may not read the batch on the wrong one."""
        run, _, _, _ = make_batch(
            sp_client, workspace, project, module, granted_sp.ensure_bot_user()
        )

        # The same principal is granted on the other project too, so the
        # 404 below is the project scope of the batch, not a deny.
        grant_scopes(sp, other_project)
        grant_project(sp, other_project, role_cap=15)

        response = sp_client.get(export_url(workspace.slug, other_project.id, run["id"]))
        assert response.status_code == status.HTTP_404_NOT_FOUND, response.content

    def test_a_service_principal_without_the_read_scope_is_refused(
        self, sp_client, unscoped_client, workspace, project, module, granted_sp
    ):
        run, _, _, _ = make_batch(
            sp_client, workspace, project, module, granted_sp.ensure_bot_user()
        )

        response = unscoped_client.get(export_url(workspace.slug, project.id, run["id"]))
        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content

    def test_the_export_is_a_get_only(
        self, sp_client, admin_client, workspace, project, module, granted_sp
    ):
        run, _, _, _ = make_batch(
            sp_client, workspace, project, module, granted_sp.ensure_bot_user()
        )
        url = export_url(workspace.slug, project.id, run["id"])

        for method in ("post", "put", "patch", "delete"):
            response = getattr(admin_client, method)(url, {}, format="json")
            assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED, (
                method,
                response.content,
            )

        # The batch is untouched by the refused writes.
        response, _ = exported(sp_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["cases"][0]["conclusion"] == "FAIL"
