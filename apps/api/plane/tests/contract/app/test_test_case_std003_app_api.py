# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for the app API half of STD-SW-003 conformance (M82).

The db layer of the standard landed with M81: the case header carries
``related_feature`` / ``remarks`` (§4.1 fields 10 and 11), a batch names
the batch it re-tests (§6.2 rule 2), the expected result is checked for
the ten unverifiable wordings of §5.6, and
:func:`plane.db.testcase_summary.summarize_run` answers §6.2 rule 3 /
§6.3. This module pins the **app API** surface on top of that layer:

* the two recommended header fields are read and written by the case
  serializer;
* an expected result that carries a §5.6 wording is refused on every
  authoring write (create, patch, replace the step table) with the
  stable code ``expected_is_unjudgeable``, the field path of the step's
  expected result and a Chinese sentence;
* ``retest_of`` is written and read, the read side carries the number of
  the referenced batch (the UI prints 「补测：RUN-…」 without a second
  call), and a link across projects or to the batch itself is refused
  with its own reason code;
* ``GET .../test-runs/<id>/summary/`` answers the four counts and the
  deviation list of the standard, computed by the pure function — the
  client cannot supply a count, and the endpoint is read-only.

The surface under test is the internal app API
(``/api/workspaces/<slug>/projects/<project_id>/...``). The v1 surface
is a later mission.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from rest_framework import status

from plane.db.models import (
    Project,
    ProjectMember,
    TestCase,
    TestCaseModule,
    TestRun,
    TestRunCase,
    TestRunStepRecord,
)
from plane.db.testcase_validity import (
    EXPECTED_IS_UNJUDGEABLE,
    RETEST_OF_CYCLE,
    RETEST_OF_PROJECT_MISMATCH,
    RETEST_OF_SELF,
)


# --------------------------------------------------------------------------- #
# Fixtures / helpers                                                          #
# --------------------------------------------------------------------------- #


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="STD-003 Project",
        identifier="S003",
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
def other_project(db, workspace, create_user):
    other = Project.objects.create(
        name="Another STD-003 Project",
        identifier="OTH",
        workspace=workspace,
        created_by=create_user,
        network=2,
    )
    ProjectMember.objects.create(project=other, member=create_user, role=20, is_active=True)
    return other


@pytest.fixture
def other_module(db, other_project):
    return TestCaseModule.objects.create(
        project=other_project,
        workspace=other_project.workspace,
        code="OTH",
        description="The module of the other project",
    )


def cases_url(slug, project_id):
    return f"/api/workspaces/{slug}/projects/{project_id}/test-cases/"


def runs_url(slug, project_id):
    return f"/api/workspaces/{slug}/projects/{project_id}/test-runs/"


def summary_url(slug, project_id, run_id):
    return f"{runs_url(slug, project_id)}{run_id}/summary/"


STEP = {"action": "Start pbot and open the settings window", "expected_result": "The settings window is shown"}


def case_payload(module, **overrides):
    payload = {
        "module": str(module.id),
        "title": "Log in with a valid account",
        "purpose": "Make sure the login path of pbot works",
        "object_overview": "The login form of the desktop client",
        "priority": "P1",
        "preconditions": "The account exists and is active",
        "environment": "Windows 10, pbot 1.2.3",
        "steps": [dict(STEP)],
    }
    payload.update(overrides)
    return payload


def create_case(client, workspace, project, module, **overrides):
    response = client.post(
        cases_url(workspace.slug, project.id), case_payload(module, **overrides), format="json"
    )
    assert response.status_code == status.HTTP_201_CREATED, response.content
    return response.json()


def run_payload(user, **overrides):
    payload = {
        "run_date": "2026-11-09",
        "version_number": "2.3.1",
        "build_number": "2310",
        "commit_hash": "8d5cf7e",
        "environment": "macOS 15.2 / MacBook Pro 14 inch",
        "executed_by": str(user.id),
    }
    payload.update(overrides)
    return payload


def create_run(client, workspace, project, user, **overrides):
    response = client.post(
        runs_url(workspace.slug, project.id), run_payload(user, **overrides), format="json"
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


def mark_step(client, workspace, project, run_id, run_case_id, sequence_id, **payload):
    response = client.patch(
        f"{runs_url(workspace.slug, project.id)}{run_id}/cases/{run_case_id}/steps/{sequence_id}/",
        payload,
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    return response


def conclude(client, workspace, project, run_id, run_case_id, **payload):
    response = client.patch(
        f"{runs_url(workspace.slug, project.id)}{run_id}/cases/{run_case_id}/",
        payload,
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    return response


def finalize(client, workspace, project, run_id):
    return client.post(
        f"{runs_url(workspace.slug, project.id)}{run_id}/finalize/", {}, format="json"
    )


def summary(client, workspace, project, run_id):
    return client.get(summary_url(workspace.slug, project.id, run_id))


def error_entries(response):
    return response.json().get("errors", [])


def error_codes(response):
    return {entry.get("code") for entry in error_entries(response)}


# --------------------------------------------------------------------------- #
# ① The two recommended header fields of §4.1                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestRecommendedHeaderFields:
    def test_the_fields_are_written_and_read_back(
        self, session_client, workspace, project, module
    ):
        body = create_case(
            session_client,
            workspace,
            project,
            module,
            related_feature=(
                "需求条目：通知联动规则 v2；取值来源：帮助页《快速上手》第 2 节"
            ),
            remarks="步骤 4 勾 ✘：开关已开启但说明文字未出现。",
        )
        assert body["related_feature"].startswith("需求条目：通知联动规则 v2")
        assert body["remarks"].startswith("步骤 4 勾 ✘")

        url = f"{cases_url(workspace.slug, project.id)}{body['id']}/"
        read = session_client.get(url)
        assert read.status_code == status.HTTP_200_OK
        assert read.json()["related_feature"] == body["related_feature"]
        assert read.json()["remarks"] == body["remarks"]

    def test_the_fields_are_updated(
        self, session_client, workspace, project, module
    ):
        body = create_case(session_client, workspace, project, module)
        assert body["related_feature"] == ""
        assert body["remarks"] == ""

        url = f"{cases_url(workspace.slug, project.id)}{body['id']}/"
        response = session_client.patch(
            url,
            {
                "related_feature": "提交号 8d5cf7e",
                "remarks": "未核实：系统通知权限的来源",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.json()["related_feature"] == "提交号 8d5cf7e"
        assert response.json()["remarks"] == "未核实：系统通知权限的来源"

    def test_an_omitted_recommended_field_stays_empty(
        self, session_client, workspace, project, module
    ):
        """The two fields are recommended, not mandatory: a case without
        them is valid, and a later header edit does not erase them."""
        body = create_case(
            session_client, workspace, project, module, remarks="顺带发现：入口图标偏小"
        )
        assert body["related_feature"] == ""

        url = f"{cases_url(workspace.slug, project.id)}{body['id']}/"
        response = session_client.patch(url, {"title": "A newer title"}, format="json")
        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.json()["remarks"] == "顺带发现：入口图标偏小"


# --------------------------------------------------------------------------- #
# ② §5.6: the expected result must be observable                              #
# --------------------------------------------------------------------------- #


#: The ten wordings of §5.6, in the spellings a person actually types:
#: the simplified characters of the table, the traditional characters of
#: the same words, and their English renderings.
BANNED_EXPECTED_RESULTS = [
    ("切换语言后界面显示正常", "正常"),
    ("The value is 正确", "正确"),
    ("The dialog says 無異常", "无异常"),
    ("The dialog is 友好", "友好"),
    ("Scrolling is 流畅", "流畅"),
    ("The button is 美觀", "美观"),
    ("The layout is 符合设计", "符合设计"),
    ("The window 应该 is 800 px wide", "应该"),
    ("大概 three items are shown", "大概"),
    ("A 一般 case is shown", "一般"),
    ("The label is correct", "correct"),
    ("Scrolling is smooth", "smooth"),
    ("The layout matches the design", "matches the design"),
    ("The dialog should appear", "should"),
    ("Roughly three items are shown", "roughly"),
    ("Generally two buttons are visible", "generally"),
]


@pytest.mark.contract
@pytest.mark.django_db
class TestUnverifiableWording:
    @pytest.mark.parametrize("expected_result,word", BANNED_EXPECTED_RESULTS)
    def test_a_banned_wording_is_refused_on_create(
        self, session_client, workspace, project, module, expected_result, word
    ):
        response = session_client.post(
            cases_url(workspace.slug, project.id),
            case_payload(
                module,
                steps=[{"action": "Open the settings window", "expected_result": expected_result}],
            ),
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, (word, response.content)
        assert EXPECTED_IS_UNJUDGEABLE in error_codes(response), (word, response.json())

        entry = next(
            entry for entry in error_entries(response) if entry["code"] == EXPECTED_IS_UNJUDGEABLE
        )
        # The field path points at the step's expected result, so the front
        # end can put the message next to the input.
        assert entry["path"] == "steps[1].expected_result", (word, entry)
        assert entry["field"] == "expected_result", (word, entry)
        # The sentence is Chinese and names the standard — the reason code
        # is what a client keys on, the sentence is what a person reads.
        assert "§5.6" in entry["message"], (word, entry)
        assert EXPECTED_IS_UNJUDGEABLE not in entry["message"], (word, entry)

        # Nothing was written.
        assert TestCase.objects.count() == 0

    def test_the_counter_example_of_5_6_is_refused(
        self, session_client, workspace, project, module
    ):
        response = session_client.post(
            cases_url(workspace.slug, project.id),
            case_payload(
                module,
                steps=[{"action": "Switch the language", "expected_result": "切换语言后界面显示正常"}],
            ),
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert EXPECTED_IS_UNJUDGEABLE in error_codes(response)

    def test_an_observable_expected_result_is_accepted(
        self, session_client, workspace, project, module
    ):
        body = create_case(
            session_client,
            workspace,
            project,
            module,
            steps=[
                {
                    "action": "Switch the language to English",
                    "expected_result": (
                        "The settings page shows the buttons Settings, Language and Done "
                        "in that order"
                    ),
                }
            ],
        )
        assert body["number"] == "TC-CLI-001"

    def test_the_update_path_checks_the_wording_too(
        self, session_client, workspace, project, module
    ):
        body = create_case(session_client, workspace, project, module)
        url = f"{cases_url(workspace.slug, project.id)}{body['id']}/"

        response = session_client.patch(
            url,
            {
                "steps": [
                    {"action": "Open the settings window", "expected_result": "The window shows 正常"}
                ]
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert EXPECTED_IS_UNJUDGEABLE in error_codes(response)

        # The stored step table is untouched.
        stored = session_client.get(f"{url}steps/").json()
        assert [step["expected_result"] for step in stored] == [STEP["expected_result"]]

    def test_the_step_table_endpoint_checks_the_wording_too(
        self, session_client, workspace, project, module
    ):
        body = create_case(session_client, workspace, project, module)
        response = session_client.put(
            f"{cases_url(workspace.slug, project.id)}{body['id']}/steps/",
            {"steps": [{"action": "Open the window", "expected_result": "The window is 美观"}]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert EXPECTED_IS_UNJUDGEABLE in error_codes(response)
        entry = next(
            entry for entry in error_entries(response) if entry["code"] == EXPECTED_IS_UNJUDGEABLE
        )
        assert entry["path"] == "steps[1].expected_result"


# --------------------------------------------------------------------------- #
# ③ §6.2 rule 2: the re-test link of a batch                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestRetestOf:
    def test_a_retest_names_the_original_batch(
        self, session_client, workspace, project, create_user
    ):
        original = create_run(session_client, workspace, project, create_user)
        retest = create_run(
            session_client, workspace, project, create_user, retest_of=str(original["id"])
        )

        assert retest["retest_of"] == original["id"]
        # The number travels with the link, so the UI prints
        # 「补测：RUN-…」 without a second call.
        assert retest["retest_of_number"] == original["number"]

    def test_the_link_is_read_back(
        self, session_client, workspace, project, create_user
    ):
        original = create_run(session_client, workspace, project, create_user)
        retest = create_run(
            session_client, workspace, project, create_user, retest_of=str(original["id"])
        )

        response = session_client.get(f"{runs_url(workspace.slug, project.id)}{retest['id']}/")
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["retest_of"] == original["id"]
        assert response.json()["retest_of_number"] == original["number"]

    def test_a_link_without_an_original_reads_as_null(
        self, session_client, workspace, project, create_user
    ):
        run = create_run(session_client, workspace, project, create_user)
        assert run["retest_of"] is None
        assert run["retest_of_number"] is None

    def test_the_link_is_written_by_a_patch(
        self, session_client, workspace, project, create_user
    ):
        original = create_run(session_client, workspace, project, create_user)
        retest = create_run(session_client, workspace, project, create_user)

        response = session_client.patch(
            f"{runs_url(workspace.slug, project.id)}{retest['id']}/",
            {"retest_of": str(original["id"])},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.json()["retest_of"] == original["id"]
        assert response.json()["retest_of_number"] == original["number"]

    def test_a_retest_may_name_a_completed_batch(
        self, session_client, workspace, project, module, create_user
    ):
        """§6.2 rule 2 appends the re-test **after** the original, so a
        completed original is the ordinary case, not an error."""
        case = create_case(session_client, workspace, project, module)
        original = create_run(session_client, workspace, project, create_user)
        run_case = add_case(session_client, workspace, project, original["id"], case["id"])
        mark_step(
            session_client,
            workspace,
            project,
            original["id"],
            run_case["id"],
            1,
            conclusion="pass",
            actual_value="the settings window opened at 300 ms",
        )
        conclude(
            session_client,
            workspace,
            project,
            original["id"],
            run_case["id"],
            conclusion="PASS",
            evidence="docs/evidence/step1.png",
        )
        assert finalize(session_client, workspace, project, original["id"]).status_code == (
            status.HTTP_200_OK
        )

        retest = create_run(
            session_client, workspace, project, create_user, retest_of=str(original["id"])
        )
        assert retest["retest_of"] == original["id"]
        assert retest["retest_of_number"] == original["number"]

    def test_a_link_across_projects_is_refused(
        self,
        session_client,
        workspace,
        project,
        other_project,
        create_user,
    ):
        foreign = create_run(session_client, workspace, other_project, create_user)

        response = session_client.post(
            runs_url(workspace.slug, project.id),
            run_payload(create_user, retest_of=str(foreign["id"])),
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert RETEST_OF_PROJECT_MISMATCH in error_codes(response)
        entry = next(
            entry for entry in error_entries(response) if entry["code"] == RETEST_OF_PROJECT_MISMATCH
        )
        assert entry["path"] == "retest_of"
        assert entry["field"] == "retest_of"
        assert entry["message"] == "补测指向的批次必须属于本项目。"
        # The refused batch was not created; the foreign one is untouched.
        assert TestRun.objects.filter(project=project).count() == 0
        assert TestRun.objects.filter(pk=foreign["id"]).exists()

    def test_a_batch_cannot_retest_itself(
        self, session_client, workspace, project, create_user
    ):
        run = create_run(session_client, workspace, project, create_user)

        response = session_client.patch(
            f"{runs_url(workspace.slug, project.id)}{run['id']}/",
            {"retest_of": str(run["id"])},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert RETEST_OF_SELF in error_codes(response)
        assert next(
            entry for entry in error_entries(response) if entry["code"] == RETEST_OF_SELF
        )["message"] == "一个批次不能补测它自己。"

    def test_a_retest_chain_cannot_run_in_a_ring(
        self, session_client, workspace, project, create_user
    ):
        first = create_run(session_client, workspace, project, create_user)
        second = create_run(
            session_client, workspace, project, create_user, retest_of=str(first["id"])
        )

        response = session_client.patch(
            f"{runs_url(workspace.slug, project.id)}{first['id']}/",
            {"retest_of": str(second["id"])},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert RETEST_OF_CYCLE in error_codes(response)

    def test_an_unknown_original_is_a_refusal_not_a_crash(
        self, session_client, workspace, project, create_user
    ):
        response = session_client.post(
            runs_url(workspace.slug, project.id),
            run_payload(create_user, retest_of=str(uuid4())),
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert response.json()["code"] == "invalid_payload"

    def test_a_link_that_is_not_an_id_is_refused(
        self, session_client, workspace, project, create_user
    ):
        response = session_client.post(
            runs_url(workspace.slug, project.id),
            run_payload(create_user, retest_of={"id": "RUN-20261109-01"}),
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert response.json()["code"] == "invalid_payload"


# --------------------------------------------------------------------------- #
# ④ §6.2 rule 3 / §6.3: the summary of a batch                                #
# --------------------------------------------------------------------------- #

#: The step table of REC-SW-001's filled sample, in the words of the
#: sample. Every expected result is observable (§5.6): the sample's own
#: record must validate, otherwise the API contradicts the standard.
SAMPLE_STEPS = [
    {
        "action": "Look at the switch in the second row of the right pane",
        "expected_result": "Off: the knob sits at the left end and the track is grey",
    },
    {
        "action": "Look at the switch in the next row",
        "expected_result": "Inoperable: lighter than the switch of step 1, a click does nothing",
    },
    {
        "action": "Click the first switch once",
        "expected_result": "On: the knob moves to the right end, the second switch turns darker",
    },
    {
        "action": "Click the second switch once",
        "expected_result": 'On, and the line reads "每天 09:00 汇总一次" word for word',
    },
    {
        "action": "Close the settings window and open it again",
        "expected_result": "Both switches are still on",
    },
    {
        "action": "Quit the app and start it again, then open the settings window",
        "expected_result": "Both switches are still on",
    },
    {
        "action": "Developer closes the notification permission of the app, then steps 1 and 3",
        "expected_result": 'A notice reads "系统通知权限已关闭" and the switch cannot be clicked',
    },
]


def build_sample_batch(client, workspace, project, module, user):
    """REC-SW-001's filled sample as an API record: one case, step 4 ✘,
    step 7 never executed, concluded FAIL."""
    case = create_case(
        client,
        workspace,
        project,
        module,
        title="通知区总开关关闭时子开关不可操作，打开后恢复",
        steps=SAMPLE_STEPS,
    )
    run = create_run(client, workspace, project, user)
    run_case = add_case(client, workspace, project, run["id"], case["id"])

    for sequence_id in (1, 2, 3, 5, 6):
        mark_step(
            client,
            workspace,
            project,
            run["id"],
            run_case["id"],
            sequence_id,
            conclusion="pass",
        )
    mark_step(
        client,
        workspace,
        project,
        run["id"],
        run_case["id"],
        4,
        conclusion="fail",
        actual_value="开关已变为开启态；该行下方无任何说明文字",
    )
    # Step 7 is the 需人工 step: both boxes blank, the reason in the
    # measured value (§6.1 rule 1). It is written but not concluded.
    mark_step(
        client,
        workspace,
        project,
        run["id"],
        run_case["id"],
        7,
        actual_value="未准备系统通知权限，本步未执行",
    )
    conclude(
        client,
        workspace,
        project,
        run["id"],
        run_case["id"],
        conclusion="FAIL",
        evidence="docs/evidence/2026-11-09/TC-SET-001/step4-after-toggle.png",
        remarks="步骤 4 勾了 ✘；步骤 7 需开发准备环境，按 需人工 记。",
        actual_result_summary="步骤 4：点「每日摘要」后未出现说明文字；步骤 7 未执行",
    )
    return run, run_case


@pytest.mark.contract
@pytest.mark.django_db
class TestSummary:
    def test_the_sample_batch_summarises_like_the_sample(
        self, session_client, workspace, project, module, create_user
    ):
        """REC-SW-001 prints PASS 0 | FAIL 1 | N/A 0 | 需人工 1 | 总数 1
        for this record. The API answers the same numbers, and the
        deviation line is the tester's own one-line phenomenon."""
        run, _ = build_sample_batch(
            session_client, workspace, project, module, create_user
        )
        assert finalize(session_client, workspace, project, run["id"]).status_code == (
            status.HTTP_200_OK
        )

        response = summary(session_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_200_OK, response.content
        body = response.json()
        assert body["total"] == 1
        assert body["pass"] == 0
        assert body["fail"] == 1
        assert body["na"] == 0
        assert body["manual"] == 1
        assert body["deviations"] == [
            {
                "case_number": "TC-CLI-001",
                "summary": "步骤 4：点「每日摘要」后未出现说明文字；步骤 7 未执行",
            }
        ]

    def test_the_summary_carries_exactly_the_documented_keys(
        self, session_client, workspace, project, create_user
    ):
        run = create_run(session_client, workspace, project, create_user)
        body = summary(session_client, workspace, project, run["id"]).json()
        assert set(body) == {"total", "pass", "fail", "na", "manual", "deviations"}

    def test_the_counts_of_a_mixed_batch(
        self, session_client, workspace, project, module, create_user
    ):
        passing = create_case(session_client, workspace, project, module)
        failing = create_case(session_client, workspace, project, module)
        not_applicable = create_case(session_client, workspace, project, module)
        manual = create_case(session_client, workspace, project, module)

        run = create_run(session_client, workspace, project, create_user)
        pass_case = add_case(
            session_client, workspace, project, run["id"], passing["id"]
        )
        fail_case = add_case(
            session_client, workspace, project, run["id"], failing["id"]
        )
        na_case = add_case(
            session_client, workspace, project, run["id"], not_applicable["id"]
        )
        manual_case = add_case(
            session_client, workspace, project, run["id"], manual["id"]
        )

        mark_step(
            session_client,
            workspace,
            project,
            run["id"],
            pass_case["id"],
            1,
            conclusion="pass",
            actual_value="the settings window opened at 300 ms",
        )
        conclude(
            session_client,
            workspace,
            project,
            run["id"],
            pass_case["id"],
            conclusion="PASS",
            evidence="docs/evidence/pass.png",
        )

        mark_step(
            session_client,
            workspace,
            project,
            run["id"],
            fail_case["id"],
            1,
            conclusion="fail",
            actual_value="the login button stayed grey",
        )
        conclude(
            session_client,
            workspace,
            project,
            run["id"],
            fail_case["id"],
            conclusion="FAIL",
            evidence="docs/evidence/fail.png",
        )

        conclude(
            session_client,
            workspace,
            project,
            run["id"],
            na_case["id"],
            conclusion="N/A",
            na_reason="the notification pane does not exist on this build",
            na_criteria_source="requirement REQ-7 section 2",
            evidence="not applicable",
        )

        conclude(
            session_client,
            workspace,
            project,
            run["id"],
            manual_case["id"],
            conclusion="MANUAL",
            manual_executor="Li Si",
            manual_condition="after the developer prepares the environment",
            evidence="pending",
        )

        assert finalize(session_client, workspace, project, run["id"]).status_code == (
            status.HTTP_200_OK
        )

        body = summary(session_client, workspace, project, run["id"]).json()
        assert body["total"] == 4
        assert body["pass"] == 1
        assert body["fail"] == 1
        assert body["na"] == 1
        assert body["manual"] == 1
        # Only the failed case is a deviation; its one-line phenomenon is
        # the measured value of its ✘ step when no case-level summary was
        # written.
        assert body["deviations"] == [
            {"case_number": "TC-CLI-002", "summary": "the login button stayed grey"}
        ]

    def test_an_unexecuted_step_of_a_failing_case_counts_as_manual(
        self, session_client, workspace, project, module, create_user
    ):
        """§6.1 rule 1: two blank boxes mean the step was not executed,
        and the case is counted 需人工 next to FAIL — the sample's own
        numbers add up to more than the total, which is deliberate."""
        run, _ = build_sample_batch(
            session_client, workspace, project, module, create_user
        )
        body = summary(session_client, workspace, project, run["id"]).json()
        assert body["fail"] == 1
        assert body["manual"] == 1
        assert body["total"] == 1

    def test_a_client_cannot_supply_a_count(
        self, session_client, workspace, project, module, create_user
    ):
        run, _ = build_sample_batch(
            session_client, workspace, project, module, create_user
        )
        response = session_client.get(
            summary_url(workspace.slug, project.id, run["id"])
            + "?total=99&pass=99&fail=0&na=0&manual=0"
        )
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        # The numbers are computed by the server; a query parameter is not
        # an input of the summary.
        assert body["total"] == 1
        assert body["fail"] == 1
        assert body["manual"] == 1

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_the_endpoint_is_read_only(
        self, session_client, workspace, project, create_user, method
    ):
        run = create_run(session_client, workspace, project, create_user)
        response = getattr(session_client, method)(
            summary_url(workspace.slug, project.id, run["id"]), {}, format="json"
        )
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED, response.content

    def test_an_empty_batch_summarises_to_zero(
        self, session_client, workspace, project, create_user
    ):
        run = create_run(session_client, workspace, project, create_user)
        body = summary(session_client, workspace, project, run["id"]).json()
        assert body == {
            "total": 0,
            "pass": 0,
            "fail": 0,
            "na": 0,
            "manual": 0,
            "deviations": [],
        }

    def test_a_batch_of_another_project_is_not_visible(
        self,
        session_client,
        workspace,
        project,
        other_project,
        create_user,
    ):
        foreign = create_run(session_client, workspace, other_project, create_user)
        response = summary(session_client, workspace, project, foreign["id"])
        assert response.status_code == status.HTTP_404_NOT_FOUND, response.content

    def test_an_unknown_batch_is_a_404(
        self, session_client, workspace, project
    ):
        response = summary(session_client, workspace, project, uuid4())
        assert response.status_code == status.HTTP_404_NOT_FOUND, response.content

    def test_the_summary_of_a_draft_batch_works_too(
        self, session_client, workspace, project, module, create_user
    ):
        """The summary is a report, not a gate: it answers while the batch
        is still being written."""
        run, _ = build_sample_batch(
            session_client, workspace, project, module, create_user
        )
        assert TestRun.objects.get(pk=run["id"]).status != "completed"
        response = summary(session_client, workspace, project, run["id"])
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["fail"] == 1


# --------------------------------------------------------------------------- #
# ⑤ The body shape of the writes this mission touches                        #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestTheBodyShape:
    """A body that is not a JSON object stays a refused payload on every
    write of the feature — including the two the re-test link reaches."""

    MALFORMED_BODIES = ([1, 2], "abc", 7, True)

    def test_a_non_object_body_is_a_400(
        self, session_client, workspace, project, module, create_user
    ):
        case = create_case(session_client, workspace, project, module)
        run = create_run(session_client, workspace, project, create_user)
        endpoints = [
            ("post", cases_url(workspace.slug, project.id)),
            ("patch", f"{cases_url(workspace.slug, project.id)}{case['id']}/"),
            ("post", runs_url(workspace.slug, project.id)),
            ("patch", f"{runs_url(workspace.slug, project.id)}{run['id']}/"),
        ]
        for method, url in endpoints:
            for body in self.MALFORMED_BODIES:
                response = getattr(session_client, method)(url, body, format="json")
                assert response.status_code == status.HTTP_400_BAD_REQUEST, (
                    method,
                    url,
                    body,
                    response.content,
                )
                assert response.json()["code"] == "invalid_payload", (method, url, body)

        # The batch and the case were not written to.
        assert TestRun.objects.count() == 1
        assert TestRunCase.objects.count() == 0
        assert TestRunStepRecord.objects.count() == 0
