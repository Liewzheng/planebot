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
* a service principal without the grant may not upload into the project;
* a human project admin removes a case and a batch (204) and a plain
  project member is refused (403, ``detail``) — the removal is the one
  write the chain does not decide for a human;
* the two unlinks (a work item of a case, a file of a record) are
  judged as ``update`` — a member-tier principal without a single
  ``delete`` scope may perform them, while the removal of the case or
  the batch itself keeps the ``delete`` action and its admin tier;
* a case is taken out of a batch by a ``DELETE`` on its record, judged
  as an ``update`` of the batch's composition (the same action the add
  asks for); a record of another batch answers 404, a completed batch
  refuses it with the 409 envelope, and a second removal answers 404
  rather than crashing for every principal that passes the authz chain;
* a completed batch refuses the upload with the same 409 envelope
  (``code`` / ``errors``) the bind path uses;
* the field set of every answer is pinned (sorted key lists), so a
  refactor of the app serializers turns this branch red instead of
  changing the public v1 API silently.

The reason codes the tests pin are the ones of the authz chain
(:mod:`plane.core.authz.decision`) and of the validity rules
(:mod:`plane.db.testcase_validity`) — they are the contract a client
keys on.
"""

from unittest import mock
from uuid import uuid4

from django.db import connection
from django.test.utils import CaptureQueriesContext

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import (
    FileAsset,
    Issue,
    Project,
    ProjectMember,
    State,
    User,
    WorkspaceMember,
)
# The model rows are imported under an alias: pytest collects every class
# whose name starts with ``Test`` as a test class, and the warning it
# produces for a model would drown the report of this file.
from plane.db.models.test_case import (
    TestCase as CaseRow,
    TestCaseModule as ModuleRow,
    TestRun as RunRow,
    TestRunCase as RunCaseRow,
    TestRunStepRecord as RunStepRecordRow,
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


@pytest.fixture
def member_tier_sp(sp, project):
    """A member-tier principal without a single ``delete`` scope.

    The app surface asks an ``update`` (ADMIN or MEMBER) for the two
    unlinks and a ``delete`` (ADMIN) only for the removal of a case or a
    batch. A principal that holds read/create/update and a role-15 grant
    is therefore able to unlink a work item and unbind a file, and is
    refused when it removes the entity itself.
    """
    for resource_type in ("test_case", "test_run"):
        for action in ("read", "create", "update"):
            _scope(sp, project, resource_type, action)
    _scope(sp, project, "asset", "create")
    _grant(sp, project, role_cap=15)
    return sp


@pytest.fixture
def member_client(workspace, project):
    """A human client of a project **member** (role 15).

    ``create_user`` — the user of ``human_client`` — is the admin of the
    project; this user is a plain member, so the removal of a case or of
    a batch has to refuse it.
    """
    member = User.objects.create(
        email=f"member-{uuid4().hex}@plane.so",
        username=f"member_{uuid4().hex}",
        first_name="Project",
        last_name="Member",
    )
    WorkspaceMember.objects.create(workspace=workspace, member=member, role=15)
    ProjectMember.objects.create(
        project=project, member=member, role=15, is_active=True
    )
    token = APIToken.objects.create(
        user=member,
        label="Test API Token",
        token=f"plane_api_case-member-{uuid4().hex}",
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=token.token)
    return client


@pytest.fixture
def guest_client(workspace, project):
    """A human client of a project **guest** (role 5).

    A guest reads the batches of the project but does not change the
    composition of one — adding a case and removing it are the same
    write.
    """
    guest = User.objects.create(
        email=f"guest-{uuid4().hex}@plane.so",
        username=f"guest_{uuid4().hex}",
        first_name="Project",
        last_name="Guest",
    )
    WorkspaceMember.objects.create(workspace=workspace, member=guest, role=5)
    ProjectMember.objects.create(project=project, member=guest, role=5, is_active=True)
    token = APIToken.objects.create(
        user=guest,
        label="Test API Token",
        token=f"plane_api_case-guest-{uuid4().hex}",
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=token.token)
    return client


def _issue(workspace, project, create_user):
    """A work item of the project, to link a case to."""
    state = State.objects.create(
        name="Todo",
        project=project,
        workspace=workspace,
        group="backlog",
        default=True,
    )
    return Issue.objects.create(
        name="The requirement of the login path",
        workspace=workspace,
        project=project,
        state=state,
        created_by=create_user,
    )


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


def _run_case_url(workspace, project, run_id, run_case_id):
    return f"{runs_url(workspace.slug, project.id)}{run_id}/cases/{run_case_id}/"


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

        assert ModuleRow.objects.get(pk=_module["id"]).created_by_id == bot.pk
        assert CaseRow.objects.get(pk=case["id"]).created_by_id == bot.pk
        assert RunRow.objects.get(pk=run["id"]).created_by_id == bot.pk
        assert RunCaseRow.objects.get(pk=run_case["id"]).created_by_id == bot.pk
        step = RunStepRecordRow.objects.get(
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
        assert CaseRow.objects.get(pk=case["id"]).title == case["title"]

    def test_sp_cannot_mark_a_step_of_a_case_a_human_created(
        self, workspace, create_user, project, granted_sp, sp_client, human_client
    ):
        """The step record of a human's batch is out of reach as well:
        the ownership is judged on the case record the step belongs to."""
        granted_sp.ensure_bot_user()
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
        assert CaseRow.objects.get(pk=case["id"]).created_by_id == create_user.pk
        assert RunRow.objects.get(pk=run["id"]).created_by_id == create_user.pk
        assert RunCaseRow.objects.get(pk=run_case["id"]).created_by_id == create_user.pk

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

        step = RunStepRecordRow.objects.get(
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

@pytest.mark.contract
@pytest.mark.django_db
class TestHumanDeletes:
    """A case and a batch are removed by a project admin only.

    The removal is the one write whose authority the authz chain cannot
    decide for a human (it judges service principals): the view asks for
    a project admin itself, and the refusal is a 403 with DRF's
    ``detail`` — the shape every other 403 of this surface carries. The
    app surface asks for exactly the same role.
    """

    def test_a_project_admin_deletes_a_case_and_a_batch(
        self, workspace, create_user, project, human_client
    ):
        _module, case = _build_case(human_client, workspace, project)
        run = _build_run(human_client, workspace, project, create_user)

        response = human_client.delete(
            f"{cases_url(workspace.slug, project.id)}{case['id']}/"
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT, response.content
        assert CaseRow.objects.filter(pk=case["id"]).count() == 0

        response = human_client.delete(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/"
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT, response.content
        assert RunRow.objects.filter(pk=run["id"]).count() == 0

    def test_a_project_member_may_not_delete_a_case(
        self, workspace, create_user, project, human_client, member_client
    ):
        _module, case = _build_case(human_client, workspace, project)

        response = member_client.delete(
            f"{cases_url(workspace.slug, project.id)}{case['id']}/"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
        detail = response.json()["detail"]
        assert "Only a project admin can delete a test case." in detail, detail
        # The row survives the refused removal.
        assert CaseRow.objects.filter(pk=case["id"]).count() == 1

    def test_a_project_member_may_not_delete_a_batch(
        self, workspace, create_user, project, human_client, member_client
    ):
        run = _build_run(human_client, workspace, project, create_user)

        response = member_client.delete(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
        detail = response.json()["detail"]
        assert "Only a project admin can delete a test run." in detail, detail
        assert RunRow.objects.filter(pk=run["id"]).count() == 1


@pytest.mark.contract
@pytest.mark.django_db
class TestUnlinkIsAnUpdate:
    """The unlinks are updates of the entity they act on.

    Unbinding a file of a record and unlinking a work item from a case
    remove a link, not the case or the batch: the chain judges them as
    ``update`` (ADMIN or MEMBER, ``update`` scope), exactly as the app
    surface does. The removal of the entity itself keeps the ``delete``
    action and its admin tier.
    """

    def test_a_member_tier_sp_unbinds_a_file_and_unlinks_a_work_item(
        self, workspace, create_user, project, member_tier_sp, sp_client
    ):
        bot = member_tier_sp.ensure_bot_user()
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
                    "entity_type": FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD,
                    "project_id": str(project.id),
                },
                format="json",
            )
        assert response.status_code == status.HTTP_200_OK, response.content
        asset_id = response.json()["asset_id"]

        step_attachments = (
            f"{runs_url(workspace.slug, project.id)}{run['id']}"
            f"/cases/{run_case['id']}/steps/1/attachments/"
        )
        response = sp_client.post(
            step_attachments, {"asset_id": asset_id}, format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED, response.content

        # The unbind needs ``update test_run`` — the principal holds no
        # ``delete`` scope at all.
        response = sp_client.delete(f"{step_attachments}{asset_id}/")
        assert response.status_code == status.HTTP_204_NO_CONTENT, response.content

        # The same for the link of a case and a work item.
        issue = _issue(workspace, project, create_user)
        response = sp_client.post(
            f"{cases_url(workspace.slug, project.id)}{case['id']}/issues/",
            {"issue_id": str(issue.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.content

        response = sp_client.delete(
            f"{cases_url(workspace.slug, project.id)}{case['id']}/issues/{issue.id}/"
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT, response.content

    def test_the_removal_of_the_entity_keeps_the_admin_tier(
        self, workspace, create_user, project, member_tier_sp, sp_client
    ):
        """A case and a batch are not links: removing them is a
        ``delete``, which the matrix reserves to an admin-tier grant —
        even for a row the principal created itself."""
        bot = member_tier_sp.ensure_bot_user()
        _module, case = _build_case(sp_client, workspace, project)
        run = _build_run(sp_client, workspace, project, bot)

        # The delete scope is granted here so the refusal names the tier
        # and not the scope: without it the scope step answers first.
        for resource_type in ("test_case", "test_run"):
            _scope(member_tier_sp, project, resource_type, "delete")

        response = sp_client.delete(
            f"{cases_url(workspace.slug, project.id)}{case['id']}/"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
        detail = response.json()["detail"]
        assert "delete test_case" in detail, detail
        assert "role_cap (15) is below the required role (20)" in detail, detail
        assert CaseRow.objects.filter(pk=case["id"]).count() == 1

        response = sp_client.delete(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
        detail = response.json()["detail"]
        assert "delete test_run" in detail, detail
        assert RunRow.objects.filter(pk=run["id"]).count() == 1


@pytest.mark.contract
@pytest.mark.django_db
class TestRemovingACaseFromABatch:
    """Removing a case from a batch is an ``update`` of its composition.

    The removal of the *entity* (a case, a batch) is a ``delete`` with an
    admin-tier grant; taking a case out of a batch is the undo of adding
    it, so it asks for what the add asks for — ``update test_run`` — and
    a member-tier principal without a single ``delete`` scope may
    perform it.
    """

    def test_a_member_tier_sp_removes_a_case_without_a_delete_scope(
        self, workspace, create_user, project, member_tier_sp, sp_client
    ):
        bot = member_tier_sp.ensure_bot_user()
        _module, case = _build_case(sp_client, workspace, project)
        run = _build_run(sp_client, workspace, project, bot)
        run_case = _add_case(sp_client, workspace, project, run["id"], case["id"])
        url = _run_case_url(workspace, project, run["id"], run_case["id"])

        response = sp_client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT, response.content

        # The record is out of the batch …
        assert sp_client.get(url).status_code == status.HTTP_404_NOT_FOUND
        response = sp_client.get(f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/")
        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.json() == []

        # … the case row itself is untouched …
        assert CaseRow.objects.filter(pk=case["id"]).count() == 1
        # … and the record was soft-deleted, not destroyed: the batch
        # keeps what was executed, and the row cannot be removed twice.
        removed = RunCaseRow.all_objects.get(pk=run_case["id"])
        assert removed.deleted_at is not None

    def test_the_removed_case_can_be_added_again(
        self, workspace, create_user, project, granted_sp, sp_client
    ):
        bot = granted_sp.ensure_bot_user()
        _module, case = _build_case(sp_client, workspace, project)
        run = _build_run(sp_client, workspace, project, bot)
        run_case = _add_case(sp_client, workspace, project, run["id"], case["id"])

        assert sp_client.delete(
            _run_case_url(workspace, project, run["id"], run_case["id"])
        ).status_code == (status.HTTP_204_NO_CONTENT)

        again = _add_case(sp_client, workspace, project, run["id"], case["id"])
        assert again["id"] != run_case["id"]

        response = sp_client.get(f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/")
        assert [row["id"] for row in response.json()] == [again["id"]]

    def test_a_second_removal_is_a_404(
        self, workspace, create_user, project, human_client
    ):
        """The row is already gone: the second removal answers 404, not 500.

        The principal here is a human project admin — a member-tier
        service principal stops one step earlier, at the ownership step
        of the authz chain, because the row it would own is soft-deleted
        (403, still never a 500).
        """
        _module, case = _build_case(human_client, workspace, project)
        run = _build_run(human_client, workspace, project, create_user)
        run_case = _add_case(human_client, workspace, project, run["id"], case["id"])
        url = _run_case_url(workspace, project, run["id"], run_case["id"])

        assert human_client.delete(url).status_code == status.HTTP_204_NO_CONTENT
        response = human_client.delete(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND, response.content

    def test_a_case_of_another_batch_is_a_404(
        self, workspace, create_user, project, granted_sp, sp_client
    ):
        """The lookup is scoped by the batch of the URL: no cross-batch removal."""
        bot = granted_sp.ensure_bot_user()
        _module, case = _build_case(sp_client, workspace, project)
        first = _build_run(sp_client, workspace, project, bot)
        second = _build_run(sp_client, workspace, project, bot)
        run_case = _add_case(sp_client, workspace, project, first["id"], case["id"])

        response = sp_client.delete(
            _run_case_url(workspace, project, second["id"], run_case["id"])
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND, response.content

        # The record is still in its own batch.
        response = sp_client.get(f"{runs_url(workspace.slug, project.id)}{first['id']}/cases/")
        assert [row["id"] for row in response.json()] == [run_case["id"]]

    def test_a_guest_may_not_remove_a_case(
        self, workspace, create_user, project, human_client, guest_client
    ):
        """A guest reads the batch but does not change its composition."""
        _module, case = _build_case(human_client, workspace, project)
        run = _build_run(human_client, workspace, project, create_user)
        run_case = _add_case(human_client, workspace, project, run["id"], case["id"])
        url = _run_case_url(workspace, project, run["id"], run_case["id"])

        response = guest_client.delete(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content

        # The record survived the refused removal.
        response = human_client.get(f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/")
        assert [row["id"] for row in response.json()] == [run_case["id"]]

    def test_a_completed_batch_refuses_the_removal(
        self, workspace, create_user, project, granted_sp, sp_client
    ):
        """The same 409 envelope the other writes of a frozen batch carry."""
        bot = granted_sp.ensure_bot_user()
        _module, case = _build_case(sp_client, workspace, project)
        run = _build_run(sp_client, workspace, project, bot)
        run_case = _add_case(sp_client, workspace, project, run["id"], case["id"])

        run_url = f"{runs_url(workspace.slug, project.id)}{run['id']}"
        response = sp_client.patch(
            f"{run_url}/cases/{run_case['id']}/steps/1/",
            {"conclusion": "pass", "actual_value": "the form was shown"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        response = sp_client.patch(
            f"{run_url}/cases/{run_case['id']}/",
            {"conclusion": "PASS", "evidence": "the form was shown"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        response = sp_client.post(f"{run_url}/finalize/", {}, format="json")
        assert response.status_code == status.HTTP_200_OK, response.content

        response = sp_client.delete(f"{run_url}/cases/{run_case['id']}/")
        assert response.status_code == status.HTTP_409_CONFLICT, response.content
        body = response.json()
        assert body["code"] == "run_completed", body
        assert error_codes(response) == {"run_completed"}, body

        # The record is the history of the execution — it stays.
        response = sp_client.get(f"{run_url}/cases/")
        assert [row["id"] for row in response.json()] == [run_case["id"]]

    def test_a_service_principal_without_the_update_scope_is_refused(
        self, workspace, create_user, project, sp, sp_client, human_client
    ):
        """The scope step answers first, and it asks for ``update`` —
        the action the removal is judged as."""
        _grant(sp, project, role_cap=15)
        _scope(sp, project, "test_run", "read")
        _module, case = _build_case(human_client, workspace, project)
        run = _build_run(human_client, workspace, project, create_user)
        run_case = _add_case(human_client, workspace, project, run["id"], case["id"])

        response = sp_client.delete(
            _run_case_url(workspace, project, run["id"], run_case["id"])
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
        assert "lacks scope for update test_run" in response.json()["detail"]

        # The record survived the refused removal.
        response = human_client.get(f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/")
        assert [row["id"] for row in response.json()] == [run_case["id"]]


@pytest.mark.contract
@pytest.mark.django_db
class TestTheFrozenBatchEnvelope:
    """A completed batch refuses every write with the same 409 body.

    The CLI branches on ``errors[].code``, so the upload path answers the
    refusal in the envelope of the bind path — a bare ``{"error": …}``
    would leave the client without a code to key on.
    """

    def test_the_upload_of_a_frozen_batch_carries_the_reason_code(
        self, workspace, create_user, project, granted_sp, sp_client
    ):
        bot = granted_sp.ensure_bot_user()
        _module, case = _build_case(sp_client, workspace, project)
        run = _build_run(sp_client, workspace, project, bot)
        run_case = _add_case(sp_client, workspace, project, run["id"], case["id"])

        run_url = f"{runs_url(workspace.slug, project.id)}{run['id']}"
        response = sp_client.patch(
            f"{run_url}/cases/{run_case['id']}/steps/1/",
            {"conclusion": "pass", "actual_value": "the form was shown"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        response = sp_client.patch(
            f"{run_url}/cases/{run_case['id']}/",
            {"conclusion": "PASS", "evidence": "the form was shown"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        response = sp_client.post(f"{run_url}/finalize/", {}, format="json")
        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.json()["status"] == "completed"

        step = RunStepRecordRow.objects.get(
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

        assert response.status_code == status.HTTP_409_CONFLICT, response.content
        body = response.json()
        assert body["code"] == "run_completed", body
        assert error_codes(response) == {"run_completed"}, body
        assert body["errors"][0]["path"] == "", body
        # No file row was left behind.
        assert not FileAsset.objects.filter(workspace=workspace).exists()


@pytest.mark.contract
@pytest.mark.django_db
class TestTheWireFormatIsPinned:
    """The field set of every answer is part of the contract.

    The serializers are the app's — one contract, not two — so a refactor
    of the app serializers reaches this surface too. These assertions
    make such a change turn *this* branch red instead of changing the
    public v1 API silently under a CLI that implements from the contract
    table.
    """

    def test_every_answer_carries_exactly_the_documented_fields(
        self, workspace, create_user, project, granted_sp, sp_client
    ):
        bot = granted_sp.ensure_bot_user()

        response = sp_client.post(
            modules_url(workspace.slug, project.id), module_payload(), format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED, response.content
        module = response.json()
        assert sorted(module.keys()) == sorted(
            ["id", "code", "description", "created_at", "updated_at"]
        )

        response = sp_client.post(
            cases_url(workspace.slug, project.id), case_payload(module), format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED, response.content
        case = response.json()
        assert sorted(case.keys()) == sorted(
            [
                "id",
                "number",
                "module",
                "module_code",
                "title",
                "purpose",
                "object_overview",
                "priority",
                "preconditions",
                "environment",
                # STD-SW-003 §4.1 fields 10 and 11: the two recommended
                # header items the case serializer carries (M82).
                "related_feature",
                "remarks",
                "status",
                "steps",
                "issue_links",
                "created_at",
                "updated_at",
                "created_by",
            ]
        )
        assert sorted(case["steps"][0].keys()) == sorted(
            [
                "id",
                "sequence_id",
                "action",
                "expected_result",
                "created_at",
                "updated_at",
            ]
        )

        run = _build_run(sp_client, workspace, project, bot)
        assert sorted(run.keys()) == sorted(
            [
                "id",
                "number",
                "run_date",
                "version_number",
                "build_number",
                "commit_hash",
                "environment",
                "executed_by",
                "executed_by_name",
                "status",
                # STD-SW-003 §6.2 rule 2: the re-test link and the number of
                # the batch it points at (M82).
                "retest_of",
                "retest_of_number",
                "cases",
                "created_at",
                "updated_at",
                "created_by",
            ]
        )

        run_case = _add_case(sp_client, workspace, project, run["id"], case["id"])
        assert sorted(run_case.keys()) == sorted(
            [
                "id",
                "test_case",
                "case_number",
                "case_module_code",
                "case_title",
                "case_purpose",
                "case_object_overview",
                "case_priority",
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
                "created_at",
                "updated_at",
            ]
        )
        assert sorted(run_case["steps"][0].keys()) == sorted(
            [
                "id",
                "sequence_id",
                "action_snapshot",
                "expected_result_snapshot",
                "conclusion",
                "actual_value",
                "attachments",
                "created_at",
                "updated_at",
            ]
        )

        # The attachment, in the shape the bind returns it.
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

        response = sp_client.post(
            f"{runs_url(workspace.slug, project.id)}{run['id']}"
            f"/cases/{run_case['id']}/steps/1/attachments/",
            {"asset_id": asset_id},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.content
        assert sorted(response.json().keys()) == sorted(
            ["id", "asset_id", "name", "size", "url"]
        )


@pytest.mark.contract
@pytest.mark.django_db
class TestReTestReadCost:
    """The v1 read endpoints hand out the app serializer (one contract, not
    two), so the re-test link it carries has to be **joined** on this
    surface too. Otherwise every linked row costs one extra query, and a
    list grows with the number of re-tests rather than with a constant.

    Each measurement is preceded by a warm-up request, so the two counts
    compared below are both warm reads of the same process — the
    comparison is about the link, not about caches filling up.
    """

    def _cost(self, client, url):
        """The query count of ``url``, measured on a warm read."""
        warmup = client.get(url)
        assert warmup.status_code == status.HTTP_200_OK, warmup.content
        with CaptureQueriesContext(connection) as captured:
            response = client.get(url)
        assert response.status_code == status.HTTP_200_OK, response.content
        return len(captured.captured_queries), response.json()

    def test_the_list_cost_does_not_grow_with_the_number_of_links(
        self, workspace, create_user, project, granted_sp, sp_client
    ):
        bot = granted_sp.ensure_bot_user()
        original = _build_run(sp_client, workspace, project, bot)
        linked = [_build_run(sp_client, workspace, project, bot) for _ in range(2)]

        without_links, body = self._cost(
            sp_client, runs_url(workspace.slug, project.id)
        )
        assert len(body["results"]) == 3
        assert all(row["retest_of_number"] is None for row in body["results"])

        # The same three rows; two of them now name the original.
        for run in linked:
            response = sp_client.patch(
                f"{runs_url(workspace.slug, project.id)}{run['id']}/",
                {"retest_of": str(original["id"])},
                format="json",
            )
            assert response.status_code == status.HTTP_200_OK, response.content

        with_links, body = self._cost(
            sp_client, runs_url(workspace.slug, project.id)
        )
        numbers = [row["retest_of_number"] for row in body["results"]]
        assert numbers.count(original["number"]) == 2, numbers

        # Two extra links, and not a single extra query.
        assert with_links == without_links

    def test_the_detail_cost_does_not_grow_when_the_row_gets_a_link(
        self, workspace, create_user, project, granted_sp, sp_client
    ):
        bot = granted_sp.ensure_bot_user()
        original = _build_run(sp_client, workspace, project, bot)
        retest = _build_run(sp_client, workspace, project, bot)

        url = f"{runs_url(workspace.slug, project.id)}{retest['id']}/"
        unlinked, body = self._cost(sp_client, url)
        assert body["retest_of_number"] is None

        response = sp_client.patch(
            url, {"retest_of": str(original["id"])}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK, response.content

        linked, body = self._cost(sp_client, url)
        assert body["retest_of_number"] == original["number"]

        # The link is a join: the detail read costs the same before and
        # after the row names an original.
        assert linked == unlinked
