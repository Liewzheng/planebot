# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: adding a case to a batch on the v1 surface (M94).

The surface under test is the public v1 API
(``/api/v1/workspaces/<slug>/projects/<project_id>/test-runs/<id>/cases/``)
— the surface the pbot CLI drives. The standard's rule lives on both
surfaces, so the add path of v1 has to refuse exactly what the app-side
add path refuses:

* a case whose expected result carries a wording a reader cannot judge
  is not written yet and must not enter an execution batch
  (STD-SW-003 §5.6) — checked again at add time, over the live row that
  would be snapshotted, because the authoring rules only run on the
  case-write path: a case written before those rules existed, or
  written through a path that skips the serializer, would otherwise
  enter the batch and only fail at ``finalize``;
* the refusal uses the write surface's envelope — ``code
  invalid_test_case``, the same Chinese sentences, one entry per
  problem with the case number, the step and the field path;
* a batch add is **all or nothing**: a mixed payload inserts neither
  case, and the same case id twice in one payload inserts nothing
  either (the duplicate is found before the first insert).
"""

from uuid import uuid4

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import (
    Project,
    ProjectMember,
    TestCase,
    TestCaseModule,
    TestCaseStep,
    TestRunCase,
)
from plane.db.models.api import APIToken
from plane.db.testcase_validity import EXPECTED_IS_UNJUDGEABLE
from plane.service_principals.constants import PrincipalType
from plane.service_principals.models import (
    ProjectGrant,
    ServicePrincipal,
    ServiceScope,
)


#: The prefix every v1 path of this feature carries.
BASE = "/api/v1/workspaces/{slug}/projects/{project_id}"


def runs_url(slug, project_id):
    return f"{BASE.format(slug=slug, project_id=project_id)}/test-runs/"


def cases_url(slug, project_id):
    return f"{BASE.format(slug=slug, project_id=project_id)}/test-cases/"


def error_codes(response):
    return {entry.get("code") for entry in response.json().get("errors", [])}


def case_payload(module, **overrides):
    payload = {
        "module": str(module.id),
        "title": "Switch the language",
        "purpose": "Make sure the language switch of the settings page works",
        "object_overview": "The language menu of the settings page",
        "priority": "P1",
        "preconditions": "The settings page is open",
        "environment": "Windows 10, pbot 1.2.3",
        "steps": [
            {"action": "Switch the language to English", "expected_result": "切换语言后界面显示正常"},
        ],
    }
    payload.update(overrides)
    return payload


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


def create_case(client, workspace, project, module, **overrides):
    response = client.post(
        cases_url(workspace.slug, project.id), case_payload(module, **overrides), format="json"
    )
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


def make_stored_case(project, module, create_user, steps=None):
    """Write a case straight into the database, bypassing the serializer.

    This is the state the add-time check exists for: a case written
    before the authoring rules existed, or written through a path that
    never runs them. Going through the case API would be refused by the
    write-side rule first, so the case is constructed at the model level
    to exercise the add path.
    """
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
    for index, step in enumerate(
        steps
        or [
            {
                "action": "Switch the language to English",
                "expected_result": "切换语言后界面显示正常",
            }
        ],
        start=1,
    ):
        TestCaseStep.objects.create(
            project=project,
            test_case=case,
            sequence_id=index,
            action=step["action"],
            expected_result=step["expected_result"],
        )
    return case


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="Test Run Add Project",
        identifier="TRA",
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
def sp_token():
    """A service token of its own for every test.

    The throttle of the v1 surface is keyed on the API-key string and
    its counter lives in the cache, so a token shared by many tests
    spends one bucket for all of them. A token per test keeps every test
    in its own bucket and the file independent of the order it runs in.
    """
    return f"plane_svc_token_run-add-{uuid4().hex}"


@pytest.fixture
def sp(workspace, create_user, sp_token):
    sp = ServicePrincipal.objects.create(
        workspace=workspace,
        owner=create_user,
        name="run-add-bot",
    )
    APIToken.objects.create(
        label="svc:run-add-bot",
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
def granted_sp(sp, project):
    """A member-tier principal that may read, create and update the feature."""
    for resource_type in ("test_case", "test_run"):
        for action in ("read", "create", "update", "delete"):
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


@pytest.mark.contract
@pytest.mark.django_db
class TestAddCasesOnV1:
    def test_a_case_with_an_unjudgeable_expected_result_is_refused(
        self, sp_client, workspace, project, module, create_user, granted_sp
    ):
        """§5.6: an unfinished case must not enter an execution batch.

        The case is written at the model level, so the write-side rule
        could not have refused it — the case the add-time check exists
        for.
        """
        case = make_stored_case(project, module, create_user)
        run = create_run(sp_client, workspace, project, granted_sp.ensure_bot_user())

        response = sp_client.post(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/",
            {"test_case_ids": [str(case.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        body = response.json()
        assert body["code"] == "invalid_test_case", body
        entry = next(
            entry for entry in body["errors"] if entry["code"] == EXPECTED_IS_UNJUDGEABLE
        )
        # The entry names the case and the step it was refused for.
        assert entry["case_number"] == case.number, entry
        assert entry["step_sequence"] == 1, entry
        assert entry["path"] == f"cases[{case.number}].steps[1].expected_result", entry

        # The sentence is the one the write surface returns for the same
        # wording: same code, same sentence, whichever side refuses.
        refused = sp_client.post(
            cases_url(workspace.slug, project.id),
            case_payload(module),
            format="json",
        )
        assert refused.status_code == status.HTTP_400_BAD_REQUEST, refused.content
        write_entry = next(
            entry
            for entry in refused.json()["errors"]
            if entry["code"] == EXPECTED_IS_UNJUDGEABLE
        )
        assert entry["message"] == write_entry["message"], entry
        assert "§5.6" in entry["message"], entry

        # Nothing entered the batch.
        assert TestRunCase.objects.filter(test_run_id=run["id"]).count() == 0

    def test_a_stored_case_with_an_observable_expected_result_is_added(
        self, sp_client, workspace, project, module, create_user, granted_sp
    ):
        """The add-time check refuses the wording, not the case itself."""
        case = make_stored_case(
            project,
            module,
            create_user,
            steps=[
                {
                    "action": "Switch the language to English",
                    "expected_result": "The menu shows Settings, Language and Done in that order",
                }
            ],
        )
        run = create_run(sp_client, workspace, project, granted_sp.ensure_bot_user())

        run_case = add_case(sp_client, workspace, project, run["id"], case.id)
        assert run_case["case_number"] == case.number
        assert run_case["steps"][0]["action_snapshot"] == "Switch the language to English"
        assert run_case["steps"][0]["expected_result_snapshot"] == (
            "The menu shows Settings, Language and Done in that order"
        )

    def test_every_offending_case_is_reported_in_one_answer(
        self, sp_client, workspace, project, module, create_user, granted_sp
    ):
        """One answer carries every reason, each naming its own case."""
        first = make_stored_case(project, module, create_user)
        second = make_stored_case(project, module, create_user)
        run = create_run(sp_client, workspace, project, granted_sp.ensure_bot_user())

        response = sp_client.post(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/",
            {"test_case_ids": [str(first.id), str(second.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        entries = [
            entry
            for entry in response.json()["errors"]
            if entry["code"] == EXPECTED_IS_UNJUDGEABLE
        ]
        assert {entry["case_number"] for entry in entries} == {
            first.number,
            second.number,
        }, entries
        assert TestRunCase.objects.filter(test_run_id=run["id"]).count() == 0

    def test_a_mixed_add_of_a_bad_and_a_good_case_adds_neither(
        self, sp_client, workspace, project, module, create_user, granted_sp
    ):
        good = create_case(
            sp_client,
            workspace,
            project,
            module,
            steps=[
                {
                    "action": "Switch the language to English",
                    "expected_result": "The menu shows Settings, Language and Done in that order",
                }
            ],
        )
        bad = make_stored_case(project, module, create_user)
        run = create_run(sp_client, workspace, project, granted_sp.ensure_bot_user())

        response = sp_client.post(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/",
            {"test_case_ids": [str(good["id"]), str(bad.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert error_codes(response) == {EXPECTED_IS_UNJUDGEABLE}, response.content
        assert TestRunCase.objects.filter(test_run_id=run["id"]).count() == 0

        # The good case is not poisoned: it still adds on its own.
        added = add_case(sp_client, workspace, project, run["id"], good["id"])
        assert added["case_number"] == good["number"]

    def test_the_same_case_twice_in_one_payload_adds_nothing(
        self, sp_client, workspace, project, module, create_user, granted_sp
    ):
        """The duplicate is found before the first insert, not after it."""
        case = create_case(
            sp_client,
            workspace,
            project,
            module,
            steps=[
                {
                    "action": "Switch the language to English",
                    "expected_result": "The menu shows Settings, Language and Done in that order",
                }
            ],
        )
        run = create_run(sp_client, workspace, project, granted_sp.ensure_bot_user())

        response = sp_client.post(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/cases/",
            {"test_case_ids": [str(case["id"]), str(case["id"])]},
            format="json",
        )
        assert response.status_code == status.HTTP_409_CONFLICT, response.content
        assert "duplicate_case_number" in error_codes(response), response.content
        assert TestRunCase.objects.filter(test_run_id=run["id"]).count() == 0
