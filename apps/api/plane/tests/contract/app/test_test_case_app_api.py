# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for the app API of the test-case feature (M75 / TCASE-2R).

The surface under test is the internal app API
(``/api/workspaces/<slug>/projects/<project_id>/test-cases/`` and
``.../test-case-modules/``). The tests pin the behaviour the standard
(STD-SW-003) demands and the mission demanded:

* the server assigns the number of a case; the number increases inside
  the module and a voided case keeps its number (it is not handed out
  a second time);
* the server checks the mandatory items of the case header and the step
  table itself — the front end is not part of the contract;
* the step table is replaced as a whole, and the links to work items
  are created and removed;
* a service principal may only write the rows it created itself, while
  a human admin is not restricted — and its write is credited to the
  shadow bot, never to the human the token is filed under;
* the read side filters by module, priority and work item, and the
  optimistic concurrency of the case header is honoured.

Each check has a counterpart in the run-side suite
(``test_test_run_app_api.py``) that pins the execution records.
"""

from __future__ import annotations

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import (
    APIToken,
    Issue,
    Project,
    ProjectMember,
    State,
    TestCase,
    TestCaseModule,
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
        name="Test Case Project",
        identifier="TCP",
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
def other_module(db, project):
    return TestCaseModule.objects.create(
        project=project,
        workspace=project.workspace,
        code="UI",
        description="The desktop interface of pbot",
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
        name="testcase-bot",
    )


@pytest.fixture
def sp_token(db, sp):
    return APIToken.objects.create(
        user=sp.owner,
        label="svc:testcase-bot",
        user_type=1,
        is_service=True,
        principal_type=PrincipalType.SERVICE,
        service_principal=sp,
        workspace=sp.workspace,
        token="plane_svc_testcase_bot",
    )


@pytest.fixture
def sp_key_client(api_client, sp_token):
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=sp_token.token)
    return client


@pytest.fixture
def sp_granted(db, sp, project):
    """The SP may read, create and update the test cases of the project."""
    for action in ("read", "create", "update"):
        ServiceScope.objects.create(
            service_principal=sp,
            project=project,
            resource_type="test_case",
            action=action,
        )
    ProjectGrant.objects.create(
        service_principal=sp,
        project=project,
        role_cap=15,
        is_active=True,
    )
    return sp


def cases_url(slug, project_id):
    return f"/api/workspaces/{slug}/projects/{project_id}/test-cases/"


def modules_url(slug, project_id):
    return f"/api/workspaces/{slug}/projects/{project_id}/test-case-modules/"


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
            {"action": "Type the account and the password", "expected_result": "The form accepts them"},
        ],
    }
    payload.update(overrides)
    return payload


def create_case(client, workspace, project, module, **overrides):
    response = client.post(
        cases_url(workspace.slug, project.id),
        case_payload(module, **overrides),
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED, response.content
    return response.json()


def error_codes(response):
    return {entry.get("code") for entry in response.json().get("errors", [])}


# --------------------------------------------------------------------------- #
# The module registry                                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestModuleRegistry:
    def test_the_registry_is_listed(self, session_client, workspace, project, module):
        response = session_client.get(modules_url(workspace.slug, project.id))
        assert response.status_code == status.HTTP_200_OK
        codes = [row["code"] for row in response.json()]
        assert "CLI" in codes

    def test_a_module_is_registered(self, session_client, workspace, project):
        response = session_client.post(
            modules_url(workspace.slug, project.id),
            {"code": "net", "description": "The network layer of pbot"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["code"] == "NET"

    def test_a_malformed_code_is_rejected(self, session_client, workspace, project):
        response = session_client.post(
            modules_url(workspace.slug, project.id),
            {"code": "1", "description": "A code that is not a code"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "module_code_format" in error_codes(response)

    def test_a_code_is_unique_inside_the_project(self, session_client, workspace, project, module):
        response = session_client.post(
            modules_url(workspace.slug, project.id),
            {"code": "CLI", "description": "A second registration of the same code"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "module_code_taken" in error_codes(response)


# --------------------------------------------------------------------------- #
# ① The number of a case                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestCaseNumbering:
    def test_the_server_assigns_the_number(
        self, session_client, workspace, project, module
    ):
        body = create_case(session_client, workspace, project, module)
        assert body["number"] == "TC-CLI-001"

    def test_the_number_increases_inside_the_module(
        self, session_client, workspace, project, module
    ):
        first = create_case(session_client, workspace, project, module)
        second = create_case(session_client, workspace, project, module)
        assert first["number"] == "TC-CLI-001"
        assert second["number"] == "TC-CLI-002"

    def test_the_number_restarts_in_another_module(
        self, session_client, workspace, project, module, other_module
    ):
        create_case(session_client, workspace, project, module)
        other = create_case(session_client, workspace, project, other_module)
        assert other["number"] == "TC-UI-001"

    def test_a_voided_case_keeps_its_number(
        self, session_client, workspace, project, module
    ):
        first = create_case(session_client, workspace, project, module)
        response = session_client.patch(
            f"{cases_url(workspace.slug, project.id)}{first['id']}/",
            {"status": "void"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == "void"

        third = create_case(session_client, workspace, project, module)
        assert third["number"] == "TC-CLI-002"

        # The voided case is still there and still carries its number.
        stored = TestCase.objects.get(pk=first["id"])
        assert stored.number == "TC-CLI-001"


# --------------------------------------------------------------------------- #
# ② The mandatory items are checked by the server                             #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestMandatoryItems:
    @pytest.mark.parametrize(
        "field,code",
        [
            ("title", "title_missing"),
            ("purpose", "purpose_missing"),
            ("object_overview", "object_overview_missing"),
            ("preconditions", "preconditions_missing"),
            ("environment", "environment_missing"),
            ("priority", "priority_invalid"),
        ],
    )
    def test_a_missing_header_item_is_rejected(
        self, session_client, workspace, project, module, field, code
    ):
        response = session_client.post(
            cases_url(workspace.slug, project.id),
            case_payload(module, **{field: ""}),
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert code in error_codes(response)

    def test_a_case_without_a_step_is_rejected(
        self, session_client, workspace, project, module
    ):
        response = session_client.post(
            cases_url(workspace.slug, project.id),
            case_payload(module, steps=[]),
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "step_count_zero" in error_codes(response)

    def test_a_step_without_an_action_is_rejected(
        self, session_client, workspace, project, module
    ):
        response = session_client.post(
            cases_url(workspace.slug, project.id),
            case_payload(
                module,
                steps=[{"action": "", "expected_result": "The form is shown"}],
            ),
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "action_empty" in error_codes(response)

    def test_a_step_without_an_expected_result_is_rejected(
        self, session_client, workspace, project, module
    ):
        response = session_client.post(
            cases_url(workspace.slug, project.id),
            case_payload(
                module,
                steps=[{"action": "Start pbot", "expected_result": ""}],
            ),
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "expected_empty" in error_codes(response)

    def test_the_error_carries_the_field_path(
        self, session_client, workspace, project, module
    ):
        response = session_client.post(
            cases_url(workspace.slug, project.id),
            case_payload(module, title=""),
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        paths = {entry.get("path") for entry in response.json()["errors"]}
        assert "title" in paths
        # The reason is Chinese — the front end shows it as it is.
        messages = {entry.get("message") for entry in response.json()["errors"]}
        assert any("用例标题" in (message or "") for message in messages)


# --------------------------------------------------------------------------- #
# The step table and the links to work items                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestStepsAndLinks:
    def test_the_step_table_is_replaced_as_a_whole(
        self, session_client, workspace, project, module
    ):
        body = create_case(session_client, workspace, project, module)
        response = session_client.put(
            f"{cases_url(workspace.slug, project.id)}{body['id']}/steps/",
            {"steps": [{"action": "Only one step", "expected_result": "It works"}]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()) == 1
        assert response.json()[0]["sequence_id"] == 1

    def test_an_empty_step_table_is_rejected(
        self, session_client, workspace, project, module
    ):
        body = create_case(session_client, workspace, project, module)
        response = session_client.put(
            f"{cases_url(workspace.slug, project.id)}{body['id']}/steps/",
            {"steps": []},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "step_count_zero" in error_codes(response)

    def test_a_work_item_is_linked_and_unlinked(
        self, session_client, workspace, project, module, issue
    ):
        body = create_case(session_client, workspace, project, module)
        url = f"{cases_url(workspace.slug, project.id)}{body['id']}/issues/"

        response = session_client.post(url, {"issue_id": str(issue.id)}, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["issue_id"] == str(issue.id)

        response = session_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()) == 1

        response = session_client.delete(f"{url}{issue.id}/")
        assert response.status_code == status.HTTP_204_NO_CONTENT

        response = session_client.get(url)
        assert response.json() == []

    def test_a_work_item_of_another_project_is_refused(
        self, session_client, workspace, project, module, db
    ):
        body = create_case(session_client, workspace, project, module)
        foreign = Issue.objects.create(
            name="A work item of another project",
            project=project,
            workspace=workspace,
            state=State.objects.filter(project=project).first(),
            created_by=project.created_by,
        )
        # Move it to another project of the same workspace.
        other_project = Project.objects.create(
            name="Other Project",
            identifier="OTH",
            workspace=workspace,
            created_by=project.created_by,
            network=2,
        )
        foreign.project = other_project
        foreign.save()

        response = session_client.post(
            f"{cases_url(workspace.slug, project.id)}{body['id']}/issues/",
            {"issue_id": str(foreign.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "issue_not_in_project" in error_codes(response)


# --------------------------------------------------------------------------- #
# The read side: filters, pagination, optimistic concurrency                   #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestReadSide:
    def test_the_list_is_filtered_by_module(
        self, session_client, workspace, project, module, other_module
    ):
        create_case(session_client, workspace, project, module)
        create_case(session_client, workspace, project, other_module)

        response = session_client.get(
            f"{cases_url(workspace.slug, project.id)}?module={module.id}"
        )
        assert response.status_code == status.HTTP_200_OK
        numbers = [row["number"] for row in response.json()["results"]]
        assert numbers == ["TC-CLI-001"]

    def test_the_list_is_filtered_by_priority(
        self, session_client, workspace, project, module
    ):
        create_case(session_client, workspace, project, module, priority="P0")
        create_case(session_client, workspace, project, module, priority="P3")

        response = session_client.get(
            f"{cases_url(workspace.slug, project.id)}?priority=P3"
        )
        numbers = [row["number"] for row in response.json()["results"]]
        assert numbers == ["TC-CLI-002"]

    def test_the_list_is_filtered_by_work_item(
        self, session_client, workspace, project, module, issue
    ):
        first = create_case(session_client, workspace, project, module)
        create_case(session_client, workspace, project, module)
        session_client.post(
            f"{cases_url(workspace.slug, project.id)}{first['id']}/issues/",
            {"issue_id": str(issue.id)},
            format="json",
        )

        response = session_client.get(
            f"{cases_url(workspace.slug, project.id)}?issue={issue.id}"
        )
        numbers = [row["number"] for row in response.json()["results"]]
        assert numbers == ["TC-CLI-001"]

    def test_a_voided_case_is_out_of_the_working_set(
        self, session_client, workspace, project, module
    ):
        body = create_case(session_client, workspace, project, module)
        session_client.patch(
            f"{cases_url(workspace.slug, project.id)}{body['id']}/",
            {"status": "void"},
            format="json",
        )

        response = session_client.get(cases_url(workspace.slug, project.id))
        assert response.json()["results"] == []

        response = session_client.get(f"{cases_url(workspace.slug, project.id)}?status=void")
        assert len(response.json()["results"]) == 1

    def test_a_stale_update_is_a_conflict(
        self, session_client, workspace, project, module
    ):
        body = create_case(session_client, workspace, project, module)
        stale = body["updated_at"]

        # Somebody else writes the case in between.
        session_client.patch(
            f"{cases_url(workspace.slug, project.id)}{body['id']}/",
            {"title": "A newer title"},
            format="json",
        )

        response = session_client.patch(
            f"{cases_url(workspace.slug, project.id)}{body['id']}/",
            {"title": "My title", "updated_at": stale},
            format="json",
        )
        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json()["code"] == "conflict"
        # The current value travels with the conflict.
        assert response.json()["current"]["title"] == "A newer title"

    def test_a_fresh_update_is_written(
        self, session_client, workspace, project, module
    ):
        body = create_case(session_client, workspace, project, module)
        response = session_client.patch(
            f"{cases_url(workspace.slug, project.id)}{body['id']}/",
            {"title": "A newer title", "updated_at": body["updated_at"]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["title"] == "A newer title"


# --------------------------------------------------------------------------- #
# ⑦ + ⑧ The service principal                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestServicePrincipal:
    def test_a_service_principal_creates_a_case(
        self, sp_key_client, workspace, project, module, sp_granted
    ):
        response = sp_key_client.post(
            cases_url(workspace.slug, project.id),
            case_payload(module),
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["number"] == "TC-CLI-001"

    def test_the_write_is_credited_to_the_shadow_bot(
        self, sp_key_client, workspace, project, module, sp_granted, sp
    ):
        response = sp_key_client.post(
            cases_url(workspace.slug, project.id),
            case_payload(module),
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        stored = TestCase.objects.get(pk=response.json()["id"])
        assert stored.created_by is not None
        assert stored.created_by.is_bot is True
        assert stored.created_by.bot_type == "SERVICE_PRINCIPAL"
        assert stored.created_by_id != sp.owner_id

    def test_a_service_principal_may_write_its_own_row(
        self, sp_key_client, workspace, project, module, sp_granted
    ):
        created = sp_key_client.post(
            cases_url(workspace.slug, project.id),
            case_payload(module),
            format="json",
        )
        assert created.status_code == status.HTTP_201_CREATED

        response = sp_key_client.patch(
            f"{cases_url(workspace.slug, project.id)}{created.json()['id']}/",
            {"title": "A title the bot wrote"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["title"] == "A title the bot wrote"

    def test_a_service_principal_may_not_write_a_foreign_row(
        self, sp_key_client, session_client, workspace, project, module, sp_granted
    ):
        foreign = create_case(session_client, workspace, project, module)

        response = sp_key_client.patch(
            f"{cases_url(workspace.slug, project.id)}{foreign['id']}/",
            {"title": "A title the bot wants to write"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # The row is untouched.
        stored = TestCase.objects.get(pk=foreign["id"])
        assert stored.title == "Log in with a valid account"

    def test_a_human_admin_is_not_restricted(
        self, sp_key_client, session_client, workspace, project, module, sp_granted
    ):
        created = sp_key_client.post(
            cases_url(workspace.slug, project.id),
            case_payload(module),
            format="json",
        )
        assert created.status_code == status.HTTP_201_CREATED

        response = session_client.patch(
            f"{cases_url(workspace.slug, project.id)}{created.json()['id']}/",
            {"title": "A title the admin wrote"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["title"] == "A title the admin wrote"

    def test_a_service_principal_without_a_grant_is_denied(
        self, sp_key_client, workspace, project, module, sp
    ):
        response = sp_key_client.post(
            cases_url(workspace.slug, project.id),
            case_payload(module),
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
