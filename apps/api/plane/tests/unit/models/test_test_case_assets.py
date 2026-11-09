# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Unit tests for the attachments of a test run (M74).

The files of a step record and of a case record are bound to the record
by a foreign key on ``FileAsset`` — not by a link that is parsed out of
the Markdown source. The rules of :mod:`plane.db.testcase_validity` read
how many files are attached through ``TestRunStepRecord.attachment_count``
and ``TestRunCase.evidence_attachment_count``.
"""

from datetime import date
from uuid import uuid4

import pytest

from plane.db.models import (
    FileAsset,
    Project,
    ProjectMember,
    TestCase,
    TestCaseModule,
    TestCaseStep,
    TestRun,
    TestRunCase,
    TestRunStepRecord,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.db.testcase_validity import (
    FAIL_WITHOUT_ACTUAL_VALUE,
    STEP_VERDICT_FAIL,
    RUN_CASE_CONCLUSION_FAIL,
    RunCaseData,
    StepRecordData,
    validate_run_case,
)


pytestmark = pytest.mark.unit


@pytest.fixture
def user(db):
    user = User.objects.create(
        email="tester-m74@plane.so",
        username="tester_m74",
        first_name="Test",
        last_name="Tester",
    )
    user.set_password("human@123")
    user.save()
    return user


@pytest.fixture
def workspace(user):
    ws = Workspace.objects.create(
        name="M74 Workspace",
        slug="m74-workspace",
        owner=user,
    )
    WorkspaceMember.objects.create(workspace=ws, member=user, role=20, is_active=True)
    return ws


@pytest.fixture
def project(workspace, user):
    project = Project.objects.create(
        name="M74 Project",
        identifier="M74",
        workspace=workspace,
        network=2,
    )
    ProjectMember.objects.create(project=project, member=user, role=20, is_active=True)
    return project


@pytest.fixture
def module(project):
    return TestCaseModule.objects.create(
        project=project,
        code="CLI",
        description="The command line interface of pbot",
    )


@pytest.fixture
def run(project):
    return TestRun.objects.create(
        project=project,
        run_date=date(2026, 11, 9),
        version_number="1.2.3",
        build_number="b4021",
        commit_hash="9f1c0de",
        environment="Windows 10 on the test bench",
    )


@pytest.fixture
def run_case(project, module, run):
    case = TestCase.objects.create(
        project=project,
        module=module,
        title="Log in with a valid account",
        purpose="Make sure the login path works",
        object_overview="The login form of pbot",
        priority="P1",
        preconditions="The account exists",
        environment="Windows 10, pbot 1.2.3",
    )
    TestCaseStep.objects.create(
        project=project,
        test_case=case,
        sequence_id=1,
        action="Open the login page",
        expected_result="The page is shown",
    )
    return TestRunCase.create_for_test_case(case, run)


@pytest.fixture
def step_record(run_case, project):
    record = run_case.step_records.first()
    record.conclusion = STEP_VERDICT_FAIL
    record.save()
    return record


def _asset(workspace, **overrides):
    base = {
        "asset": f"{workspace.id}/{uuid4().hex}-shot.png",
        "workspace": workspace,
        "attributes": {"name": "shot.png", "type": "image/png", "size": 1024},
        "size": 1024,
        "is_uploaded": True,
    }
    base.update(overrides)
    return FileAsset.objects.create(**base)


def _run_case_data(run_case, step_record):
    """Map the model onto the dataclass the rules work on.

    This is the mapping the app layer has to do as well: the rules know
    nothing about Django.
    """
    return RunCaseData(
        case_number=run_case.case_number,
        module_code=run_case.case_module_code,
        title=run_case.case_title,
        purpose=run_case.case_purpose,
        object_overview=run_case.case_object_overview,
        priority=run_case.case_priority,
        preconditions=run_case.case_preconditions,
        environment=run_case.case_environment,
        steps=(
            StepRecordData(
                sequence_id=step_record.sequence_id,
                action=step_record.action_snapshot,
                expected_result=step_record.expected_result_snapshot,
                conclusion=step_record.conclusion,
                actual_value=step_record.actual_value,
                attachment_count=step_record.attachment_count,
            ),
        ),
        conclusion=run_case.conclusion or RUN_CASE_CONCLUSION_FAIL,
        evidence=run_case.evidence,
        evidence_attachment_count=run_case.evidence_attachment_count,
    )


@pytest.mark.django_db
class TestAttachmentBinding:
    def test_a_file_may_be_bound_to_a_step_record(self, workspace, step_record):
        asset = _asset(
            workspace,
            entity_type=FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD,
            test_run_step_record=step_record,
        )

        assert asset.test_run_step_record_id == step_record.pk
        assert step_record.attachment_count == 1
        assert list(step_record.assets.all()) == [asset]

    def test_a_file_may_be_bound_to_a_case_record(self, workspace, run_case):
        asset = _asset(
            workspace,
            entity_type=FileAsset.EntityTypeContext.TEST_RUN_CASE_EVIDENCE,
            test_run_case=run_case,
        )

        assert asset.test_run_case_id == run_case.pk
        assert run_case.evidence_attachment_count == 1
        assert list(run_case.assets.all()) == [asset]

    def test_a_file_without_a_binding_stays_unbound(self, workspace):
        asset = _asset(workspace)

        assert asset.test_run_step_record_id is None
        assert asset.test_run_case_id is None

    def test_the_two_entity_types_exist(self):
        assert FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD == "TEST_RUN_STEP_RECORD"
        assert FileAsset.EntityTypeContext.TEST_RUN_CASE_EVIDENCE == "TEST_RUN_CASE_EVIDENCE"

    def test_the_asset_of_a_test_run_is_served_by_the_workspace_endpoint(self, workspace, step_record):
        asset = _asset(
            workspace,
            entity_type=FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD,
            test_run_step_record=step_record,
        )

        assert asset.asset_url == f"/api/assets/v2/workspaces/{workspace.slug}/{asset.id}/"


@pytest.mark.django_db
class TestAttachmentCount:
    def test_a_deleted_file_is_not_counted(self, workspace, step_record):
        asset = _asset(
            workspace,
            entity_type=FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD,
            test_run_step_record=step_record,
        )
        assert step_record.attachment_count == 1

        # The flag alone is enough — the asset endpoints of the app layer
        # set it without touching the soft-delete column.
        asset.is_deleted = True
        asset.save(update_fields=["is_deleted"])

        assert step_record.attachment_count == 0

    def test_a_soft_deleted_file_is_not_counted(self, workspace, step_record):
        asset = _asset(
            workspace,
            entity_type=FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD,
            test_run_step_record=step_record,
        )
        assert step_record.attachment_count == 1

        asset.delete(soft=True)

        assert step_record.attachment_count == 0

    def test_two_files_are_counted_as_two(self, workspace, step_record):
        _asset(
            workspace,
            entity_type=FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD,
            test_run_step_record=step_record,
        )
        _asset(
            workspace,
            entity_type=FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD,
            test_run_step_record=step_record,
        )

        assert step_record.attachment_count == 2

    def test_a_file_of_another_step_is_not_counted(self, workspace, step_record, run_case):
        """Only the files of this step belong to this step."""
        other = TestRunStepRecord.objects.create(
            project=step_record.project,
            test_run_case=run_case,
            sequence_id=2,
        )
        _asset(
            workspace,
            entity_type=FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD,
            test_run_step_record=other,
        )

        assert step_record.attachment_count == 0
        assert other.attachment_count == 1


@pytest.mark.django_db
class TestAttachmentMakesTheStepFilled:
    """The attachment is the measured value — until it is gone."""

    def test_the_attachment_alone_fills_a_failed_step(self, workspace, run_case, step_record):
        _asset(
            workspace,
            entity_type=FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD,
            test_run_step_record=step_record,
        )
        run_case.evidence = "the report of the run"
        run_case.save()

        reasons = validate_run_case(_run_case_data(run_case, step_record))
        assert "step_1:" + FAIL_WITHOUT_ACTUAL_VALUE not in reasons

    def test_a_deleted_attachment_makes_the_step_unfilled_again(self, workspace, run_case, step_record):
        asset = _asset(
            workspace,
            entity_type=FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD,
            test_run_step_record=step_record,
        )
        run_case.evidence = "the report of the run"
        run_case.save()

        reasons = validate_run_case(_run_case_data(run_case, step_record))
        assert "step_1:" + FAIL_WITHOUT_ACTUAL_VALUE not in reasons

        asset.is_deleted = True
        asset.save(update_fields=["is_deleted"])

        reasons = validate_run_case(_run_case_data(run_case, step_record))
        assert "step_1:" + FAIL_WITHOUT_ACTUAL_VALUE in reasons

    def test_the_evidence_of_a_case_may_be_a_file(self, workspace, run_case, step_record):
        _asset(
            workspace,
            entity_type=FileAsset.EntityTypeContext.TEST_RUN_CASE_EVIDENCE,
            test_run_case=run_case,
        )

        reasons = validate_run_case(_run_case_data(run_case, step_record))
        assert "evidence_missing" not in reasons


@pytest.mark.django_db
class TestStorageContract:
    """The measured value and the evidence keep their Markdown source."""

    def test_the_measured_value_is_stored_as_it_was_typed(self, step_record):
        source = "**不一致**\n\n![shot](https://files.test/a.png)\n\nexit code 1"

        step_record.actual_value = source
        step_record.save()

        step_record.refresh_from_db()
        assert step_record.actual_value == source

    def test_the_evidence_is_stored_as_it_was_typed(self, run_case):
        source = "**the report** — [the log](https://files.test/log.txt)"

        run_case.evidence = source
        run_case.save()

        run_case.refresh_from_db()
        assert run_case.evidence == source
