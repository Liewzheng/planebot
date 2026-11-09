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

from plane.db.models import (
    Project,
    ProjectMember,
    TestCase,
    TestCaseIssue,
    TestCaseModule,
    TestCaseStep,
    TestRun,
    TestRunCase,
    User,
    Workspace,
    WorkspaceMember,
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
