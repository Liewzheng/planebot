# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: the v1 surface of the test-case feature (M76 / TCASE-3RB).

The surface under test is the public v1 API
(``/api/v1/workspaces/<slug>/projects/<project_id>/test-cases/`` and
``…/test-runs/``) — the surface the pbot CLI drives, and the surface a
service principal may use.

The tests pin the behaviour the mission asks for:

* a service principal that holds the scope and the grant registers a
  module, writes a case, opens a batch, adds the case to it (the case is
  snapshotted) and marks a step;
* a member-tier service principal may not write a row a human created —
  the refusal comes from the ownership step of the authz chain, with the
  wording of that step;
* the same endpoints serve a human token;
* a service principal without the scope is refused with the wording of
  the scope step (``DENY_SCOPE_MISS``) and its remedy;
* the rows a service principal writes are credited to its shadow bot;
* a service principal uploads a picture through the v1 asset endpoint
  and binds it to a step — and a failed step whose measured value is
  nothing but that picture is a valid step (the picture *is* the
  measured value);
* a service principal without the grant may not upload into the project.

The reason codes the tests pin are the ones of the authz chain
(:mod:`plane.core.authz.decision`) and of the validity rules
(:mod:`plane.db.testcase_validity`) — they are the contract a client
keys on.
"""

from unittest import mock
from uuid import uuid4

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import (
    FileAsset,
    Project,
    ProjectMember,
)
# The model rows are imported under an alias: a class whose name starts
# with ``Test`` is collected by pytest as a test class, and the warning
# it produces would drown the report of this file.
from plane.db.models.test_case import (
    TestCase as TestCaseModel,
    TestCaseModule as TestCaseModuleModel,
    TestRun as TestRunModel,
    TestRunCase as TestRunCaseModel,
    TestRunStepRecord as TestRunStepRecordModel,
)
from plane.db.models.api import APIToken
from plane.service_principals.constants import PrincipalType
from plane.service_principals.models import (
    ProjectGrant,
    ServicePrincipal,
    ServiceScope,
)


#: The prefix every v1 path of this feature carries.
BASE = "/api/v1/workspaces/{slug}/projects/{project_id}"


def modules_url(slug, project_id):
    return f"{BASE.format(slug=slug, project_id=project_id)}/test-case-modules/"


def cases_url(slug, project_id):
    return f"{BASE.format(slug=slug, project_id=project_id)}/test-cases/"


def runs_url(slug, project_id):
    return f"{BASE.format(slug=slug, project_id=project_id)}/test-runs/"


def assets_url(slug):
    return f"/api/v1/workspaces/{slug}/assets/"


def error_codes(response):
    return {entry.get("code") for entry in response.json().get("errors", [])}


def _scope(sp, project, resource_type, action):
    return ServiceScope.objects.create(
        service_principal=sp,
        project=project,
        resource_type=resource_type,
        action=action,
    )


def _grant(sp, project, role_cap=15):
    return ProjectGrant.objects.create(
        service_principal=sp,
        project=project,
        role_cap=role_cap,
        is_active=True,
    )


def storage_stub(request=None):
    """Stand-in for ``S3Storage`` so the presign call doesn't round-trip."""
    storage = mock.MagicMock()
    storage.generate_presigned_post.return_value = {
        "url": "https://signed.example/upload",
        "fields": {},
    }
    storage.generate_presigned_url.return_value = "https://signed.example/get"
    return storage


def module_payload(code="NET", description="The network stack"):
    return {"code": code, "description": description}


def module_id(module):
    """The id of a module — a model row or the answer of the endpoint."""
    if isinstance(module, dict):
        return str(module["id"])
    return str(module.id)


def case_payload(module, **overrides):
    payload = {
        "module": module_id(module),
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


def run_payload(executor, **overrides):
    payload = {
        "run_date": "2026-11-09",
        "version_number": "1.2.3",
        "build_number": "b20261109",
        "commit_hash": "abcdef1234",
        "environment": "Windows 10, pbot 1.2.3",
        "executed_by": str(executor.id),
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="Test Case Project",
        identifier="TCP",
        workspace=workspace,
        created_by=create_user,
        network=2,
    )
    ProjectMember.objects.create(
        project=project, member=create_user, role=20, is_active=True
    )
    return project


@pytest.fixture
def sp_token():
    """A service token of its own for every test.

    The throttle of the v1 surface is keyed on the API-key string
    (:mod:`plane.api.rate_limit`) and its counter lives in the cache
    (Redis in the test stack), so a token that is shared by many tests
    spends one bucket for all of them and the later tests are answered
    with 429. A token per test gives every test its own bucket — and it
    keeps this file independent of the order it runs in.
    """
    return f"plane_svc_token_case-bot-{uuid4().hex}"


@pytest.fixture
def sp(workspace, create_user, sp_token):
    sp = ServicePrincipal.objects.create(
        workspace=workspace,
        owner=create_user,
        name="case-bot",
    )
    APIToken.objects.create(
        label="svc:case-bot",
        token=sp_token,
        user=create_user,
        principal_type=PrincipalType.SERVICE,
        service_principal=sp,
        workspace=workspace,
    )
    return sp


@pytest.fixture
def sp_client(sp_token):
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=sp_token)
    return client


@pytest.fixture
def human_client(db, create_user):
    """A client with a human API token of its own.

    The shared ``api_key_client`` fixture hands every test the same
    token, so the throttle bucket of that token is spent by the whole
    suite. A token per test keeps the human path of this file
    independent of the rest of the suite.
    """
    token = APIToken.objects.create(
        user=create_user,
        label="Test API Token",
        token=f"plane_api_case-bot-{uuid4().hex}",
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=token.token)
    return client


@pytest.fixture
def granted_sp(sp, project):
    """The service principal of the production shape: the scope on the
    three resource types of the feature and a grant at member tier."""
    for resource_type in ("test_case", "test_run", "asset"):
        for action in ("read", "create", "update", "delete"):
            _scope(sp, project, resource_type, action)
    _grant(sp, project, role_cap=15)
    return sp


def _build_case(client, workspace, project, **overrides):
    """A module and a case, written through the v1 surface."""
    response = client.post(
        modules_url(workspace.slug, project.id), module_payload(), format="json"
    )
    assert response.status_code == status.HTTP_201_CREATED, response.content
    module = response.json()

    response = client.post(
        cases_url(workspace.slug, project.id), case_payload(module, **overrides), format="json"
    )
    assert response.status_code == status.HTTP_201_CREATED, response.content
    return module, response.json()


def _build_run(client, workspace, project, executor):
    """A batch, written through the v1 surface."""
    response = client.post(
        runs_url(workspace.slug, project.id), run_payload(executor), format="json"
    )
    assert response.status_code == status.HTTP_201_CREATED, response.content
    return response.json()


def _add_case(client, workspace, project, run_id, case_id):
    response = client.post(
        f"{runs_url(workspace.slug, project.id)}{run_id}/cases/",
        {"test_case_ids": [str(case_id)]},
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED, response.content
    return response.json()[0]


@pytest.mark.contract
@pytest.mark.django_db
class TestSPWritesTheFeature:
    """A service principal with the scope and the grant drives the
    feature end to end."""

    def test_sp_registers_a_module_writes_a_case_and_marks_a_step(
        self, workspace, create_user, project, granted_sp, sp_client
    ):
        bot = granted_sp.ensure_bot_user()

        # 1. the module registry — a case may only use a registered code.
        response = sp_client.post(
            modules_url(workspace.slug, project.id), module_payload(), format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED, response.content
        module = response.json()
        assert module["code"] == "NET"

        # 2. the case with its step table.
        response = sp_client.post(
            cases_url(workspace.slug, project.id), case_payload(module), format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED, response.content
        case = response.json()
        assert case["number"].startswith("TC-NET-")
        assert len(case["steps"]) == 1

        # 3. the batch.
        response = sp_client.post(
            runs_url(workspace.slug, project.id), run_payload(bot), format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED, response.content
        run = response.json()
        assert run["number"].startswith("RUN-20261109-")

        # 4. the case goes into the batch — the POST acts on a batch that
        #    already exists, so it is judged as an update of that batch.
        response = sp_client.post(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/",
            {"test_case_ids": [case["id"]]},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.content
        run_case = response.json()[0]
        assert run_case["case_number"] == case["number"]
        assert len(run_case["steps"]) == 1

        # 5. the step is marked.
        response = sp_client.patch(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/{run_case['id']}/steps/1/",
            {"conclusion": "pass", "actual_value": "the form was shown"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.json()["conclusion"] == "pass"
        assert response.json()["actual_value"] == "the form was shown"

    def test_the_rows_the_sp_writes_are_credited_to_its_shadow_bot(
        self, workspace, create_user, project, granted_sp, sp_client
    ):
        """The attribution of a service-principal write is the shadow bot,
        never the human the token is filed under. The ownership step of
        the authz chain matches on that column, so it has to be right.
        """
        bot = granted_sp.ensure_bot_user()
        assert bot.pk != create_user.pk

        _module, case = _build_case(sp_client, workspace, project)
        run = _build_run(sp_client, workspace, project, bot)
        run_case = _add_case(sp_client, workspace, project, run["id"], case["id"])

        assert TestCaseModuleModel.objects.get(pk=_module["id"]).created_by_id == bot.pk
        assert TestCaseModel.objects.get(pk=case["id"]).created_by_id == bot.pk
        assert TestRunModel.objects.get(pk=run["id"]).created_by_id == bot.pk
        assert TestRunCaseModel.objects.get(pk=run_case["id"]).created_by_id == bot.pk
        step = TestRunStepRecordModel.objects.get(
            test_run_case_id=run_case["id"], sequence_id=1
        )
        assert step.created_by_id == bot.pk

    def test_sp_reads_the_cases_of_the_project(
        self, workspace, create_user, project, granted_sp, sp_client
    ):
        """The read path is open to every member of the project — a
        service principal at guest tier included."""
        _module, case = _build_case(sp_client, workspace, project)

        response = sp_client.get(cases_url(workspace.slug, project.id))
        assert response.status_code == status.HTTP_200_OK, response.content
        numbers = [row["number"] for row in response.json()["results"]]
        assert case["number"] in numbers


@pytest.mark.contract
@pytest.mark.django_db
class TestSPCannotWriteForeignRows:
    """A member-tier service principal may only write the rows its own
    bot user created."""

    def test_sp_cannot_patch_a_case_a_human_created(
        self, workspace, create_user, project, granted_sp, sp_client, human_client
    ):
        # The shadow bot of the principal — the identity the ownership
        # step compares the row's creator with.
        granted_sp.ensure_bot_user()
        _module, case = _build_case(human_client, workspace, project)

        response = sp_client.patch(
            f"{cases_url(workspace.slug, project.id)}{case['id']}/",
            {"title": "rewritten by the bot"},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
        detail = response.json()["detail"]
        # The wording of the ownership step of the authz chain.
        assert "may only update a test_case it created" in detail, detail
        # The remedy of that step names the grant role cap.
        assert "role cap" in detail, detail

        # And the row is untouched.
        assert TestCaseModel.objects.get(pk=case["id"]).title == case["title"]

    def test_sp_cannot_mark_a_step_of_a_case_a_human_created(
        self, workspace, create_user, project, granted_sp, sp_client, human_client
    ):
        """The step record of a human's batch is out of reach as well:
        the ownership is judged on the case record the step belongs to."""
        bot = granted_sp.ensure_bot_user()
        _module, case = _build_case(human_client, workspace, project)
        run = _build_run(human_client, workspace, project, create_user)
        run_case = _add_case(human_client, workspace, project, run["id"], case["id"])

        response = sp_client.patch(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/{run_case['id']}/steps/1/",
            {"conclusion": "pass", "actual_value": "written by the bot"},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
        detail = response.json()["detail"]
        assert "may only update a test_run it created" in detail, detail


@pytest.mark.contract
@pytest.mark.django_db
class TestHumanUsesTheSameEndpoints:
    """The v1 surface serves a human token as well — the service
    principal branch is engaged only when the request carries a service
    token."""

    def test_a_human_token_registers_a_module_writes_a_case_and_marks_a_step(
        self, workspace, create_user, project, human_client
    ):
        module, case = _build_case(human_client, workspace, project)
        assert module["code"] == "NET"

        run = _build_run(human_client, workspace, project, create_user)
        run_case = _add_case(human_client, workspace, project, run["id"], case["id"])

        response = human_client.patch(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/{run_case['id']}/steps/1/",
            {"conclusion": "pass", "actual_value": "the form was shown"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.content

        # The attribution of a human write is the user the token belongs
        # to — the same column the service principal writes its shadow
        # bot into.
        assert TestCaseModel.objects.get(pk=case["id"]).created_by_id == create_user.pk
        assert TestRunModel.objects.get(pk=run["id"]).created_by_id == create_user.pk
        assert TestRunCaseModel.objects.get(pk=run_case["id"]).created_by_id == create_user.pk

    def test_a_human_token_lists_the_cases(self, workspace, create_user, project, human_client):
        _module, case = _build_case(human_client, workspace, project)
        response = human_client.get(cases_url(workspace.slug, project.id))
        assert response.status_code == status.HTTP_200_OK, response.content
        assert case["number"] in [row["number"] for row in response.json()["results"]]


@pytest.mark.contract
@pytest.mark.django_db
class TestSPScopeAndGrant:
    """The scope step and the grant step of the authz chain, with the
    wording they carry."""

    def test_sp_without_the_scope_is_refused_with_the_scope_wording(
        self, workspace, create_user, project, sp, sp_client
    ):
        # A grant, but no scope: the request must stop at the scope step.
        _grant(sp, project, role_cap=15)

        response = sp_client.post(
            modules_url(workspace.slug, project.id), module_payload(), format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
        detail = response.json()["detail"]
        # The wording of the scope step (``DENY_SCOPE_MISS``).
        assert "lacks scope for create test_case" in detail, detail
        # And its remedy.
        assert "Manage scopes" in detail, detail

    def test_sp_without_the_scope_may_not_read_the_cases_either(
        self, workspace, create_user, project, sp, sp_client
    ):
        _grant(sp, project, role_cap=15)
        response = sp_client.get(cases_url(workspace.slug, project.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
        detail = response.json()["detail"]
        assert "lacks scope for read test_case" in detail, detail

    def test_sp_without_the_grant_may_not_upload_into_the_project(
        self, workspace, create_user, project, sp, sp_client
    ):
        """The upload path of the feature is the v1 asset endpoint. It is
        authorised at the project of the record the file hangs on, so a
        service principal without the grant is refused at the grant
        step — with the wording of that step."""
        for resource_type in ("test_case", "test_run", "asset"):
            for action in ("read", "create", "update", "delete"):
                _scope(sp, project, resource_type, action)
        # No ProjectGrant on the project.

        with mock.patch("plane.api.views.asset.S3Storage", side_effect=storage_stub):
            response = sp_client.post(
                assets_url(workspace.slug),
                {
                    "name": "shot.png",
                    "type": "image/png",
                    "size": 4096,
                    "project_id": str(project.id),
                },
                format="json",
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
        detail = response.json()["detail"]
        assert "has no active grant for project" in detail, detail
        assert "Manage grants" in detail, detail
        # No file row was left behind.
        assert FileAsset.objects.filter(workspace=workspace).count() == 0


@pytest.mark.contract
@pytest.mark.django_db
class TestSPUploadsAndBindsAnAttachment:
    """The measured value of a step may be a file: the service principal
    uploads it through the v1 asset endpoint and binds it to the step."""

    def test_a_failed_step_whose_measured_value_is_only_a_picture_is_valid(
        self, workspace, create_user, project, granted_sp, sp_client
    ):
        bot = granted_sp.ensure_bot_user()
        _module, case = _build_case(sp_client, workspace, project)
        run = _build_run(sp_client, workspace, project, bot)
        run_case = _add_case(sp_client, workspace, project, run["id"], case["id"])

        step_url = (
            f"{runs_url(workspace.slug, project.id)}{run['id']}"
            f"/cases/{run_case['id']}/steps/1/"
        )

        # The step fails and carries no text at all.
        response = sp_client.patch(step_url, {"conclusion": "fail"}, format="json")
        assert response.status_code == status.HTTP_200_OK, response.content

        # Without a file the step has no measured value, so the batch is
        # not valid — the rule names the problem.
        response = sp_client.post(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/finalize/", {}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert "fail_without_actual_value" in error_codes(response)

        # The picture is uploaded through the v1 asset endpoint. It
        # carries no record yet — the bind attaches it to the step.
        with mock.patch("plane.api.views.asset.S3Storage", side_effect=storage_stub):
            response = sp_client.post(
                assets_url(workspace.slug),
                {
                    "name": "shot.png",
                    "type": "image/png",
                    "size": 4096,
                    "entity_type": FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD,
                    "project_id": str(project.id),
                },
                format="json",
            )
        assert response.status_code == status.HTTP_200_OK, response.content
        asset_id = response.json()["asset_id"]

        asset = FileAsset.objects.get(id=asset_id)
        assert asset.entity_type == FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD
        assert asset.test_run_step_record_id is None
        assert asset.project_id == project.id

        # The bind attaches it to the step.
        response = sp_client.post(
            f"{step_url}attachments/", {"asset_id": asset_id}, format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED, response.content
        assert response.json()["asset_id"] == asset_id

        # The step now carries the file — the picture *is* the measured
        # value, so the rule is satisfied.
        response = sp_client.get(f"{step_url}attachments/")
        assert response.status_code == status.HTTP_200_OK, response.content
        assert len(response.json()) == 1

        # The conclusion of the case and its evidence.
        response = sp_client.patch(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/{run_case['id']}/",
            {"conclusion": "FAIL", "evidence": "the screen shows the error dialog"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.content

        # The batch is valid now and closes.
        response = sp_client.post(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/finalize/", {}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.json()["status"] == "completed"

    def test_the_upload_may_bind_the_file_to_the_step_right_away(
        self, workspace, create_user, project, granted_sp, sp_client
    ):
        """The one-shot form: the upload names the record the file hangs
        on, so the file is attached without a second call."""
        bot = granted_sp.ensure_bot_user()
        _module, case = _build_case(sp_client, workspace, project)
        run = _build_run(sp_client, workspace, project, bot)
        run_case = _add_case(sp_client, workspace, project, run["id"], case["id"])

        step = TestRunStepRecordModel.objects.get(
            test_run_case_id=run_case["id"], sequence_id=1
        )

        with mock.patch("plane.api.views.asset.S3Storage", side_effect=storage_stub):
            response = sp_client.post(
                assets_url(workspace.slug),
                {
                    "name": "shot.png",
                    "type": "image/png",
                    "size": 4096,
                    "entity_type": FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD,
                    "entity_identifier": str(step.id),
                },
                format="json",
            )
        assert response.status_code == status.HTTP_200_OK, response.content

        asset = FileAsset.objects.get(id=response.json()["asset_id"])
        assert asset.test_run_step_record_id == step.id
        assert asset.project_id == project.id

        # The step carries the file.
        response = sp_client.get(
            f"{runs_url(workspace.slug, project.id)}{run['id']}"
            f"/cases/{run_case['id']}/steps/1/attachments/"
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        assert len(response.json()) == 1

    def test_a_file_that_is_bound_may_not_be_bound_a_second_time(
        self, workspace, create_user, project, granted_sp, sp_client
    ):
        """A file that hangs on a record may not get a second owner."""
        bot = granted_sp.ensure_bot_user()
        _module, case = _build_case(sp_client, workspace, project)
        run = _build_run(sp_client, workspace, project, bot)
        run_case = _add_case(sp_client, workspace, project, run["id"], case["id"])

        with mock.patch("plane.api.views.asset.S3Storage", side_effect=storage_stub):
            response = sp_client.post(
                assets_url(workspace.slug),
                {
                    "name": "shot.png",
                    "type": "image/png",
                    "size": 4096,
                    "entity_type": FileAsset.EntityTypeContext.TEST_RUN_CASE_EVIDENCE,
                    "project_id": str(project.id),
                },
                format="json",
            )
        assert response.status_code == status.HTTP_200_OK, response.content
        asset_id = response.json()["asset_id"]

        response = sp_client.post(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/{run_case['id']}/attachments/",
            {"asset_id": asset_id},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.content

        # A second bind of the same file is refused.
        response = sp_client.post(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/{run_case['id']}/attachments/",
            {"asset_id": asset_id},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert "asset_already_bound" in error_codes(response)

    def test_the_evidence_of_a_case_may_be_a_file(
        self, workspace, create_user, project, granted_sp, sp_client
    ):
        """The evidence of a case record is a file as well — the same
        upload path with the other entity type."""
        bot = granted_sp.ensure_bot_user()
        _module, case = _build_case(sp_client, workspace, project)
        run = _build_run(sp_client, workspace, project, bot)
        run_case = _add_case(sp_client, workspace, project, run["id"], case["id"])

        with mock.patch("plane.api.views.asset.S3Storage", side_effect=storage_stub):
            response = sp_client.post(
                assets_url(workspace.slug),
                {
                    "name": "report.png",
                    "type": "image/png",
                    "size": 4096,
                    "entity_type": FileAsset.EntityTypeContext.TEST_RUN_CASE_EVIDENCE,
                    "entity_identifier": str(run_case["id"]),
                },
                format="json",
            )
        assert response.status_code == status.HTTP_200_OK, response.content

        asset = FileAsset.objects.get(id=response.json()["asset_id"])
        assert str(asset.test_run_case_id) == run_case["id"]

        response = sp_client.get(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/{run_case['id']}/attachments/"
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        assert len(response.json()) == 1
