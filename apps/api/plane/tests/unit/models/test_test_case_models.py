# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Unit tests for the test-case models of M63.

Covers the module-code registry (STD-SW-003 §4.2), the numbering of cases
and batches, the snapshot of a case inside a batch, and the freezing of a
completed batch.
"""

from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError

from plane.db.models import (
    Project,
    ProjectMember,
    TestCase,
    TestCaseIssue,
    TestCaseModule,
    TestCaseSequence,
    TestCaseStep,
    TestRun,
    TestRunCase,
    TestRunStepRecord,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.db.testcase_validity import (
    CONCLUSION_IS_FINAL,
    DUPLICATE_CASE_NUMBER,
    RUN_EXECUTED_BY_MISSING,
    RunData,
    validate_run,
)


pytestmark = pytest.mark.unit


@pytest.fixture
def user(db):
    user = User.objects.create(
        email="tester-m63@plane.so",
        username="tester_m63",
        first_name="Test",
        last_name="Tester",
    )
    user.set_password("human@123")
    user.save()
    return user


@pytest.fixture
def workspace(user):
    ws = Workspace.objects.create(
        name="M63 Workspace",
        slug="m63-workspace",
        owner=user,
    )
    WorkspaceMember.objects.create(workspace=ws, member=user, role=20, is_active=True)
    return ws


@pytest.fixture
def project(workspace, user):
    project = Project.objects.create(
        name="M63 Project",
        identifier="M63",
        workspace=workspace,
        network=2,
    )
    ProjectMember.objects.create(project=project, member=user, role=20, is_active=True)
    return project


@pytest.fixture
def other_project(workspace, user):
    project = Project.objects.create(
        name="M63 Other Project",
        identifier="M64",
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


def _case(module, project, **overrides):
    base = {
        "project": project,
        "module": module,
        "title": "Log in with a valid account",
        "purpose": "Make sure the login path works",
        "object_overview": "The login form of pbot",
        "priority": "P1",
        "preconditions": "The account exists",
        "environment": "Windows 10, pbot 1.2.3",
    }
    base.update(overrides)
    return TestCase.objects.create(**base)


def _step(test_case, project, sequence_id=1, action="Do it", expected_result="It works"):
    return TestCaseStep.objects.create(
        project=project,
        test_case=test_case,
        sequence_id=sequence_id,
        action=action,
        expected_result=expected_result,
    )


@pytest.mark.django_db
class TestModuleRegistry:
    def test_the_code_is_stored_in_upper_case(self, project):
        module = TestCaseModule.objects.create(
            project=project,
            code="cli",
            description="The command line interface of pbot",
        )
        assert module.code == "CLI"

    @pytest.mark.parametrize("code", ["C", "TOOLONGX", "CL-I", "CL 1"])
    def test_a_malformed_code_is_rejected(self, project, code):
        with pytest.raises(ValidationError):
            TestCaseModule.objects.create(
                project=project,
                code=code,
                description="A module",
            )

    def test_an_empty_description_is_rejected(self, project):
        with pytest.raises(ValidationError):
            TestCaseModule.objects.create(project=project, code="CLI", description="   ")

    def test_the_same_code_may_not_be_used_twice_in_a_project(self, project, module):
        with pytest.raises(ValidationError):
            TestCaseModule.objects.create(
                project=project,
                code="CLI",
                description="Another group of functions",
            )

    def test_the_same_code_may_be_used_in_another_project(self, project, other_project):
        other = TestCaseModule.objects.create(
            project=other_project,
            code="CLI",
            description="The command line interface of pbot",
        )
        assert other.code == "CLI"

    def test_the_code_of_a_module_that_carries_cases_cannot_be_changed(self, project, module):
        _case(module, project)
        module.code = "FW"
        with pytest.raises(ValidationError):
            module.save()

    def test_the_code_of_an_empty_module_may_be_changed(self, project, module):
        module.code = "FW"
        module.save()
        module.refresh_from_db()
        assert module.code == "FW"

    def test_a_module_that_carries_cases_cannot_be_deleted(self, project, module):
        _case(module, project)
        with pytest.raises(ValidationError):
            module.delete()

    def test_a_module_without_cases_can_be_deleted(self, project, module):
        module.delete()
        assert TestCaseModule.objects.filter(pk=module.pk).count() == 0

    def test_a_module_whose_case_was_voided_cannot_be_deleted(self, project, module):
        case = _case(module, project)
        case.status = "void"
        case.save()
        with pytest.raises(ValidationError):
            module.delete()

    def test_a_module_with_a_case_that_has_no_counter_row_cannot_be_deleted(
        self, project, module
    ):
        """The "carries cases" guard on its own.

        A case created with an explicitly supplied ``sequence_id`` never
        goes through the allocator, so the module has **no** counter row:
        the "has already issued a number" guard has nothing to find and
        only the "carries cases" guard can refuse the deletion. Without
        this test the first guard would be dead code that no test
        protects (the reviewer's P2-3).
        """
        case = _case(module, project, sequence_id=42)
        assert case.number == "TC-CLI-042"
        assert not TestCaseSequence.all_objects.filter(module=module).exists(), (
            "the premise of this test: the module must not have a counter row"
        )

        with pytest.raises(ValidationError):
            module.delete()

    def test_the_code_is_normalised_by_clean_as_well(self, project):
        module = TestCaseModule(
            project=project,
            workspace=project.workspace,
            code="cli",
            description="The command line interface of pbot",
        )
        module.clean()
        assert module.code == "CLI"

    def test_full_clean_accepts_a_lowercase_code(self, project, user):
        """``clean()`` and ``save()`` have to agree.

        ``save()`` upper-cases the code before it validates it, so
        ``full_clean()`` — what a serializer calls — must not reject a
        lowercase code that ``save()`` stores happily.

        The audit columns are set by hand because ``UserAuditModel``
        declares them ``null=True`` without ``blank=True``, so
        ``full_clean()`` rejects an unset one (a repo-wide quirk of the
        audit mixin, not of this model).
        """
        module = TestCaseModule(
            project=project,
            workspace=project.workspace,
            code="cli",
            description="The command line interface of pbot",
            created_by=user,
            updated_by=user,
        )
        module.full_clean()
        assert module.code == "CLI"


@pytest.mark.django_db
class TestCaseNumbering:
    def test_the_first_case_of_a_module_gets_001(self, project, module):
        case = _case(module, project)
        assert case.number == "TC-CLI-001"
        assert case.sequence_id == 1

    def test_the_number_increases_inside_the_module(self, project, module):
        assert _case(module, project).number == "TC-CLI-001"
        assert _case(module, project).number == "TC-CLI-002"
        assert _case(module, project).number == "TC-CLI-003"

    def test_another_module_counts_on_its_own(self, project, module):
        _case(module, project)
        other = TestCaseModule.objects.create(
            project=project,
            code="FIRM",
            description="The firmware update path",
        )
        assert _case(other, project).number == "TC-FIRM-001"

    def test_another_project_counts_on_its_own(self, project, other_project, module):
        _case(module, project)
        other_module = TestCaseModule.objects.create(
            project=other_project,
            code="CLI",
            description="The command line interface of pbot",
        )
        assert _case(other_module, other_project).number == "TC-CLI-001"

    def test_a_voided_case_does_not_give_its_number_away(self, project, module):
        first = _case(module, project)
        first.status = "void"
        first.save()
        assert _case(module, project).number == "TC-CLI-002"

    def test_a_case_may_not_use_the_module_of_another_project(self, project, other_project, module):
        with pytest.raises(ValidationError):
            _case(module, other_project)

    def test_the_module_of_a_case_cannot_be_changed(self, project, module):
        case = _case(module, project)
        other = TestCaseModule.objects.create(
            project=project,
            code="FIRM",
            description="The firmware update path",
        )
        case.module = other
        with pytest.raises(ValidationError):
            case.save()

    def test_the_number_of_a_case_cannot_be_changed(self, project, module):
        case = _case(module, project)
        case.number = "TC-CLI-999"
        with pytest.raises(ValidationError):
            case.save()


@pytest.mark.django_db
class TestSteps:
    def test_a_step_belongs_to_a_case_of_the_same_project(self, project, other_project, module):
        case = _case(module, project)
        with pytest.raises(ValidationError):
            TestCaseStep.objects.create(
                project=other_project,
                test_case=case,
                sequence_id=1,
                action="Do it",
                expected_result="It works",
            )


@pytest.mark.django_db
class TestRunNumbering:
    def test_the_first_batch_of_a_day_gets_01(self, project):
        run = TestRun.objects.create(
            project=project,
            run_date=date(2026, 11, 9),
            version_number="1.2.3",
            build_number="b4021",
            commit_hash="9f1c0de",
            environment="Windows 10 on the test bench",
        )
        assert run.number == "RUN-20261109-01"
        assert run.sequence_id == 1

    def test_the_number_increases_inside_the_day(self, project):
        for expected in ("RUN-20261109-01", "RUN-20261109-02", "RUN-20261109-03"):
            run = TestRun.objects.create(
                project=project,
                run_date=date(2026, 11, 9),
                version_number="1.2.3",
                build_number="b4021",
                commit_hash="9f1c0de",
                environment="Windows 10 on the test bench",
            )
            assert run.number == expected

    def test_another_day_counts_on_its_own(self, project):
        TestRun.objects.create(
            project=project,
            run_date=date(2026, 11, 9),
            version_number="1.2.3",
            build_number="b4021",
            commit_hash="9f1c0de",
            environment="Windows 10 on the test bench",
        )
        run = TestRun.objects.create(
            project=project,
            run_date=date(2026, 11, 10),
            version_number="1.2.3",
            build_number="b4021",
            commit_hash="9f1c0de",
            environment="Windows 10 on the test bench",
        )
        assert run.number == "RUN-20261110-01"

    def test_another_project_counts_on_its_own(self, project, other_project):
        TestRun.objects.create(
            project=project,
            run_date=date(2026, 11, 9),
            version_number="1.2.3",
            build_number="b4021",
            commit_hash="9f1c0de",
            environment="Windows 10 on the test bench",
        )
        run = TestRun.objects.create(
            project=other_project,
            run_date=date(2026, 11, 9),
            version_number="1.2.3",
            build_number="b4021",
            commit_hash="9f1c0de",
            environment="Windows 10 on the test bench",
        )
        assert run.number == "RUN-20261109-01"


@pytest.mark.django_db
class TestBatchVersion:
    def _run(self, project, **overrides):
        base = {
            "project": project,
            "run_date": date(2026, 11, 9),
            "version_number": "1.2.3",
            "build_number": "b4021",
            "commit_hash": "9f1c0de",
            "environment": "Windows 10 on the test bench",
        }
        base.update(overrides)
        return TestRun.objects.create(**base)

    def test_the_tested_version_may_be_filled_in_while_the_batch_is_empty(self, project):
        run = self._run(project, version_number="", build_number="", commit_hash="")
        run.version_number = "1.2.3"
        run.save()
        run.refresh_from_db()
        assert run.version_number == "1.2.3"

    def test_the_tested_version_cannot_be_changed_afterwards(self, project):
        run = self._run(project)
        run.version_number = "1.2.4"
        with pytest.raises(ValidationError):
            run.save()

    def test_the_build_number_cannot_be_changed_afterwards(self, project):
        run = self._run(project)
        run.build_number = "b4022"
        with pytest.raises(ValidationError):
            run.save()

    def test_the_commit_hash_cannot_be_changed_afterwards(self, project):
        run = self._run(project)
        run.commit_hash = "0000000"
        with pytest.raises(ValidationError):
            run.save()

    def test_the_environment_may_be_corrected_while_the_batch_runs(self, project):
        run = self._run(project)
        run.environment = "Windows 11 on the test bench"
        run.save()
        run.refresh_from_db()
        assert run.environment == "Windows 11 on the test bench"


@pytest.mark.django_db
class TestSnapshot:
    def _run(self, project):
        return TestRun.objects.create(
            project=project,
            run_date=date(2026, 11, 9),
            version_number="1.2.3",
            build_number="b4021",
            commit_hash="9f1c0de",
            environment="Windows 10 on the test bench",
        )

    def test_adding_a_case_copies_the_header(self, project, module):
        case = _case(module, project)
        run = self._run(project)
        run_case = TestRunCase.create_for_test_case(case, run)

        assert run_case.case_number == "TC-CLI-001"
        assert run_case.case_module_code == "CLI"
        assert run_case.case_title == case.title
        assert run_case.case_purpose == case.purpose
        assert run_case.case_priority == "P1"
        assert run_case.case_environment == case.environment

    def test_adding_a_case_copies_the_steps(self, project, module):
        case = _case(module, project)
        _step(case, project, 1, "Open the login page", "The page is shown")
        _step(case, project, 2, "Type the password", "The password is accepted")
        run = self._run(project)

        run_case = TestRunCase.create_for_test_case(case, run)

        records = list(run_case.step_records.order_by("sequence_id"))
        assert [record.sequence_id for record in records] == [1, 2]
        assert records[0].action_snapshot == "Open the login page"
        assert records[0].expected_result_snapshot == "The page is shown"
        assert records[1].action_snapshot == "Type the password"

    def test_editing_the_case_later_does_not_change_the_snapshot(self, project, module):
        case = _case(module, project)
        _step(case, project, 1, "Open the login page", "The page is shown")
        run = self._run(project)
        run_case = TestRunCase.create_for_test_case(case, run)

        case.title = "A completely different title"
        case.purpose = "A completely different purpose"
        case.save()
        _step(case, project, 2, "A step that was added later", "It works")

        run_case.refresh_from_db()
        assert run_case.case_title == "Log in with a valid account"
        assert run_case.case_purpose == "Make sure the login path works"
        assert run_case.step_records.count() == 1

    def test_a_case_of_another_project_may_not_be_added(self, project, other_project, module):
        case = _case(module, project)
        run = self._run(other_project)
        with pytest.raises(ValidationError):
            TestRunCase.create_for_test_case(case, run)


@pytest.mark.django_db
class TestCompletedBatchIsFrozen:
    def _run(self, project, status="completed"):
        run = TestRun.objects.create(
            project=project,
            run_date=date(2026, 11, 9),
            version_number="1.2.3",
            build_number="b4021",
            commit_hash="9f1c0de",
            environment="Windows 10 on the test bench",
        )
        run.status = status
        run.save()
        return run

    def test_a_case_may_not_be_added_to_a_completed_batch(self, project, module):
        case = _case(module, project)
        run = self._run(project)
        with pytest.raises(ValidationError):
            TestRunCase.create_for_test_case(case, run)

    def test_the_header_of_a_completed_batch_may_not_be_changed(self, project):
        run = self._run(project)
        run.version_number = "1.2.4"
        with pytest.raises(ValidationError):
            run.save()

    def test_a_completed_batch_may_not_be_reopened(self, project):
        run = self._run(project)
        run.status = "in_progress"
        with pytest.raises(ValidationError):
            run.save()

    def test_a_step_record_of_a_completed_batch_may_not_be_written(self, project, module):
        case = _case(module, project)
        _step(case, project)
        run = self._run(project, status="in_progress")
        run_case = TestRunCase.create_for_test_case(case, run)

        run.status = "completed"
        run.save()

        record = run_case.step_records.first()
        record.conclusion = "pass"
        with pytest.raises(ValidationError):
            record.save()


@pytest.mark.django_db
class TestConclusionIsFinal:
    """A conclusion that is written into a batch is never changed.

    「结论一经写进批次不再改，重跑开新批次」 (STD-SW-003 §6). The batch
    stays writable while it is open — the evidence, the remarks and the
    measured value may still be filled in — but the conclusion and the
    step verdicts are final once they are written.
    """

    def _run_case(self, project, module, status="in_progress"):
        run = TestRun.objects.create(
            project=project,
            run_date=date(2026, 11, 9),
            version_number="1.2.3",
            build_number="b4021",
            commit_hash="9f1c0de",
            environment="Windows 10 on the test bench",
        )
        run.status = status
        run.save()

        case = _case(module, project)
        _step(case, project)
        run_case = TestRunCase.create_for_test_case(case, run)
        return run, run_case

    def test_a_written_conclusion_cannot_be_changed(self, project, module):
        _, run_case = self._run_case(project, module)

        run_case.conclusion = "PASS"
        run_case.save()

        run_case.conclusion = "FAIL"
        with pytest.raises(ValidationError) as error:
            run_case.save()
        assert CONCLUSION_IS_FINAL in str(error.value)

        run_case.refresh_from_db()
        assert run_case.conclusion == "PASS", "the stored conclusion must not change"

    def test_a_written_conclusion_cannot_be_cleared(self, project, module):
        _, run_case = self._run_case(project, module)

        run_case.conclusion = "PASS"
        run_case.save()

        run_case.conclusion = ""
        with pytest.raises(ValidationError) as error:
            run_case.save()
        assert CONCLUSION_IS_FINAL in str(error.value)

        run_case.refresh_from_db()
        assert run_case.conclusion == "PASS", "the stored conclusion must not be cleared"

    def test_a_conclusion_may_be_set_once(self, project, module):
        _, run_case = self._run_case(project, module)
        assert run_case.conclusion == ""

        run_case.conclusion = "PASS"
        run_case.save()

        run_case.refresh_from_db()
        assert run_case.conclusion == "PASS"

    def test_the_evidence_may_still_be_written_after_the_conclusion(self, project, module):
        _, run_case = self._run_case(project, module)

        run_case.conclusion = "PASS"
        run_case.save()

        run_case.evidence = "screenshot 2026-11-09.png"
        run_case.save()

        run_case.refresh_from_db()
        assert run_case.evidence == "screenshot 2026-11-09.png"

    def test_a_marked_step_verdict_cannot_be_changed(self, project, module):
        _, run_case = self._run_case(project, module)
        record = run_case.step_records.first()

        record.conclusion = "fail"
        record.save()

        record.conclusion = "pass"
        with pytest.raises(ValidationError) as error:
            record.save()
        assert CONCLUSION_IS_FINAL in str(error.value)

        record.refresh_from_db()
        assert record.conclusion == "fail", "the stored verdict must not change"

    def test_a_marked_step_verdict_cannot_be_uncrossed(self, project, module):
        _, run_case = self._run_case(project, module)
        record = run_case.step_records.first()

        record.conclusion = "pass"
        record.save()

        record.conclusion = "unset"
        with pytest.raises(ValidationError) as error:
            record.save()
        assert CONCLUSION_IS_FINAL in str(error.value)

        record.refresh_from_db()
        assert record.conclusion == "pass", "the stored verdict must not be cleared"

    def test_the_measured_value_may_still_be_written_after_a_verdict(self, project, module):
        _, run_case = self._run_case(project, module)
        record = run_case.step_records.first()

        record.conclusion = "fail"
        record.save()

        record.actual_value = "error 42"
        record.save()

        record.refresh_from_db()
        assert record.actual_value == "error 42"

    def test_an_unset_step_verdict_may_be_set(self, project, module):
        _, run_case = self._run_case(project, module)
        record = run_case.step_records.first()
        assert record.conclusion == "unset"

        record.conclusion = "pass"
        record.save()

        record.refresh_from_db()
        assert record.conclusion == "pass"


@pytest.mark.django_db
class TestSnapshotIsAtomic:
    """The snapshot of a case is written in one transaction.

    A failure while the step records are copied must not leave a case in
    the batch without its snapshot steps — the snapshot is what makes the
    batch historical (the mission's hard requirement).
    """

    def _run(self, project):
        return TestRun.objects.create(
            project=project,
            run_date=date(2026, 11, 9),
            version_number="1.2.3",
            build_number="b4021",
            commit_hash="9f1c0de",
            environment="Windows 10 on the test bench",
        )

    def test_a_failure_while_copying_the_steps_leaves_nothing_behind(
        self, project, module, monkeypatch
    ):
        case = _case(module, project)
        _step(case, project, 1, "First step", "It works")
        _step(case, project, 2, "Second step", "It works too")
        run = self._run(project)

        original_create = TestRunStepRecord.objects.create
        calls = {"count": 0}

        def failing_create(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("the second step record cannot be written")
            return original_create(*args, **kwargs)

        monkeypatch.setattr(TestRunStepRecord.objects, "create", failing_create)

        with pytest.raises(RuntimeError):
            TestRunCase.create_for_test_case(case, run)

        assert TestRunCase.all_objects.count() == 0, (
            "the half-written case record must have been rolled back"
        )
        assert TestRunStepRecord.all_objects.count() == 0, (
            "no step record may survive a failed snapshot"
        )

    def test_the_happy_path_still_copies_every_step(self, project, module):
        case = _case(module, project)
        _step(case, project, 1, "First step", "It works")
        _step(case, project, 2, "Second step", "It works too")
        run = self._run(project)

        run_case = TestRunCase.create_for_test_case(case, run)

        assert run_case.step_records.count() == 2


@pytest.mark.django_db
class TestExecutorSnapshot:
    """The batch keeps the name of its executor next to the foreign key.

    The foreign key is ``SET_NULL``; a deleted user would leave the batch
    without an executor and a completed batch retroactively invalid. The
    text snapshot keeps the name.
    """

    def _run(self, project, executed_by):
        return TestRun.objects.create(
            project=project,
            run_date=date(2026, 11, 9),
            version_number="1.2.3",
            build_number="b4021",
            commit_hash="9f1c0de",
            environment="Windows 10 on the test bench",
            executed_by=executed_by,
        )

    def _executor(self, suffix=""):
        executor = User.objects.create(
            email=f"executor-m63{suffix}@plane.so",
            username=f"executor_m63{suffix}",
            first_name="Exe",
            last_name="Cutor",
        )
        executor.set_password("human@123")
        executor.save()
        return executor

    def test_the_name_of_the_executor_is_snapshotted(self, project):
        executor = self._executor()
        run = self._run(project, executor)

        run.refresh_from_db()
        assert run.executed_by_name == executor.display_name
        assert run.executed_by_name != ""

    def test_the_name_survives_the_deletion_of_the_user_row(self, project):
        executor = self._executor()
        run = self._run(project, executor)
        run.refresh_from_db()
        snapshot = run.executed_by_name

        # A hard delete — the repository has no user deletion flow, but
        # the foreign key is SET_NULL and the snapshot has to survive it.
        # ``User`` is not a soft-delete model, so ``delete()`` is a hard
        # delete already.
        executor.delete()

        run.refresh_from_db()
        assert run.executed_by_id is None
        assert run.executed_by_name == snapshot

    def test_a_completed_batch_stays_valid_after_the_deletion(self, project):
        executor = self._executor()
        run = self._run(project, executor)
        run.status = "completed"
        run.save()

        executor.delete()
        run.refresh_from_db()

        reasons = validate_run(
            RunData(
                number=run.number,
                run_date=run.run_date,
                version_number=run.version_number,
                build_number=run.build_number,
                commit_hash=run.commit_hash,
                environment=run.environment,
                executed_by="",
                executed_by_name=run.executed_by_name,
                status="completed",
            )
        )
        assert RUN_EXECUTED_BY_MISSING not in reasons

    def test_the_snapshot_follows_a_changed_executor(self, project):
        executor = self._executor()
        run = self._run(project, executor)
        first_snapshot = run.executed_by_name

        other = self._executor("-second")
        run.executed_by = other
        run.save()
        run.refresh_from_db()

        assert run.executed_by_name != first_snapshot
        assert run.executed_by_name == other.display_name

    def test_an_executor_assigned_later_is_snapshotted(self, project):
        """A batch may be opened without an executor; the executor is
        assigned when the run starts. The snapshot has to be filled then
        as well."""
        run = self._run(project, None)
        run.refresh_from_db()
        assert run.executed_by_id is None
        assert run.executed_by_name == ""

        executor = self._executor()
        run.executed_by = executor
        run.save()

        run.refresh_from_db()
        assert run.executed_by_id == executor.id
        assert run.executed_by_name == executor.display_name

    def test_a_new_executor_replaces_the_name_of_a_deleted_one(self, project):
        """The foreign key of a deleted executor is NULL again. A new
        executor must not inherit the name of the deleted one."""
        executor = self._executor()
        run = self._run(project, executor)
        run.refresh_from_db()
        deleted_name = run.executed_by_name

        executor.delete()
        run.refresh_from_db()
        assert run.executed_by_id is None
        assert run.executed_by_name == deleted_name

        replacement = self._executor("-replacement")
        run.executed_by = replacement
        run.save()

        run.refresh_from_db()
        assert run.executed_by_id == replacement.id
        assert run.executed_by_name == replacement.display_name
        assert run.executed_by_name != deleted_name

    def test_a_partial_update_of_the_executor_keeps_the_snapshot_in_step(self, project):
        """Django only writes the fields listed in ``update_fields``; a
        partial update that moves the executor has to carry the snapshot
        with it, otherwise the old name stays next to the new person."""
        executor = self._executor()
        run = self._run(project, executor)
        run.refresh_from_db()
        first_snapshot = run.executed_by_name

        replacement = self._executor("-replacement")
        run.executed_by = replacement
        run.save(update_fields=["executed_by"])

        run.refresh_from_db()
        assert run.executed_by_id == replacement.id
        assert run.executed_by_name == replacement.display_name
        assert run.executed_by_name != first_snapshot


@pytest.mark.django_db
class TestDuplicateCaseInBatch:
    """The same test case may not sit in one batch twice."""

    def _run(self, project):
        return TestRun.objects.create(
            project=project,
            run_date=date(2026, 11, 9),
            version_number="1.2.3",
            build_number="b4021",
            commit_hash="9f1c0de",
            environment="Windows 10 on the test bench",
        )

    def test_the_same_case_may_not_be_added_twice(self, project, module):
        case = _case(module, project)
        run = self._run(project)

        TestRunCase.create_for_test_case(case, run)

        with pytest.raises(ValidationError) as error:
            TestRunCase.create_for_test_case(case, run)

        assert DUPLICATE_CASE_NUMBER in str(error.value)
        assert not isinstance(error.value, IntegrityError)
        assert TestRunCase.all_objects.count() == 1

    def test_a_soft_deleted_record_does_not_block_re_adding(self, project, module):
        case = _case(module, project)
        run = self._run(project)

        first = TestRunCase.create_for_test_case(case, run)
        first.delete(soft=True)

        second = TestRunCase.create_for_test_case(case, run)

        assert second.pk != first.pk
        assert TestRunCase.objects.filter(test_run=run, test_case=case).count() == 1

    def test_the_same_case_may_be_added_to_another_batch(self, project, module):
        case = _case(module, project)
        first_run = self._run(project)
        second_run = self._run(project)

        TestRunCase.create_for_test_case(case, first_run)
        TestRunCase.create_for_test_case(case, second_run)

        assert TestRunCase.all_objects.count() == 2


@pytest.mark.django_db
class TestCaseIssueLink:
    def test_a_case_may_be_linked_to_a_work_item(self, project, module):
        from plane.db.models import Issue

        issue = Issue.objects.create(
            project=project,
            name="The work item behind the case",
        )
        link = TestCaseIssue.objects.create(
            project=project,
            test_case=_case(module, project),
            issue=issue,
        )
        assert link.test_case.issue_links.count() == 1
        assert issue.test_case_links.count() == 1

    def test_a_link_whose_case_and_work_item_disagree_is_rejected(
        self, project, other_project, module
    ):
        """The case and the work item are in different projects — the
        first branch of the guard fires."""
        from plane.db.models import Issue

        issue = Issue.objects.create(
            project=other_project,
            name="The work item of the other project",
        )
        with pytest.raises(ValidationError) as error:
            TestCaseIssue.objects.create(
                project=project,
                test_case=_case(module, project),
                issue=issue,
            )
        assert "test_case" in error.value.error_dict

    def test_a_link_that_claims_another_project_is_rejected(
        self, project, other_project, module
    ):
        """Only the second branch of the guard can satisfy this.

        The case and the work item are in the **same** project — a
        legitimate pair — but the link row itself claims another project.
        The first branch has nothing to find here, so only the ``issue``
        branch can refuse the row.
        """
        from plane.db.models import Issue

        issue = Issue.objects.create(
            project=project,
            name="The work item of the same project",
        )
        with pytest.raises(ValidationError) as error:
            TestCaseIssue.objects.create(
                project=other_project,
                test_case=_case(module, project),
                issue=issue,
            )
        assert "issue" in error.value.error_dict
