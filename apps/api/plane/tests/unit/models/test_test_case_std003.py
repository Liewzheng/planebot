# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The db-layer fields STD-SW-003 asks for besides the case header (M81).

Three pieces of the standard have no column until this module was added:

* ``TestCase.related_feature`` — §4.1 field 10 (推荐): the requirement item,
  change ticket, commit or design version the case stands for, plus where
  the value was read from (§5.7). Free text, next to the work-item links
  of :class:`TestCaseIssue` — the two coexist, neither replaces the other.
* ``TestCase.remarks`` — §4.1 field 11 (推荐): deviations, incidental
  findings, uncovered scope, unverified items and their source (§5.12).
* ``TestRun.retest_of`` — §6.2 rule 2: a re-test appends a new batch that
  names the original one. Nullable self-reference, ``SET_NULL``, same
  project, never itself, never a ring. Pointing at a **completed** batch is
  the normal case.

Every rule here is tested for both sides: the good case is accepted, the
bad case raises ``ValidationError`` with the stable code in the message.
"""

from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, models

from plane.db.testcase_validity import (
    RETEST_OF_CYCLE,
    RETEST_OF_PROJECT_MISMATCH,
    RETEST_OF_SELF,
)
from plane.db.models import (
    Issue,
    Project,
    ProjectMember,
    TestCase,
    TestCaseIssue,
    TestCaseModule,
    TestRun,
    User,
    Workspace,
    WorkspaceMember,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def user(db):
    user = User.objects.create(
        email="tester-m81@plane.so",
        username="tester_m81",
        first_name="Test",
        last_name="Tester",
    )
    user.set_password("human@123")
    user.save()
    return user


@pytest.fixture
def workspace(user):
    ws = Workspace.objects.create(name="M81 Workspace", slug="m81-workspace", owner=user)
    WorkspaceMember.objects.create(workspace=ws, member=user, role=20, is_active=True)
    return ws


@pytest.fixture
def project(workspace, user):
    project = Project.objects.create(
        name="M81 Project", identifier="M81", workspace=workspace, network=2
    )
    ProjectMember.objects.create(project=project, member=user, role=20, is_active=True)
    return project


@pytest.fixture
def other_project(workspace, user):
    project = Project.objects.create(
        name="M81 Other Project", identifier="M82", workspace=workspace, network=2
    )
    ProjectMember.objects.create(project=project, member=user, role=20, is_active=True)
    return project


@pytest.fixture
def module(project):
    return TestCaseModule.objects.create(
        project=project, code="SET", description="The settings window"
    )


def _case(module, project, **overrides):
    base = {
        "project": project,
        "module": module,
        "title": "总开关关闭时子开关不可操作",
        "purpose": "验证总开关与子开关的联动",
        "object_overview": "设置窗口「通知」区的右栏",
        "priority": "P1",
        "preconditions": "应用已安装并启动",
        "environment": "macOS 15.2, 应用 2.3.1",
    }
    base.update(overrides)
    return TestCase.objects.create(**base)


def _run(project, **overrides):
    base = {
        "project": project,
        "run_date": date(2026, 11, 9),
        "version_number": "2.3.1",
        "build_number": "2310",
        "commit_hash": "8d5cf7e",
        "environment": "macOS 15.2 / MacBook Pro 14 英寸",
    }
    base.update(overrides)
    return TestRun.objects.create(**base)


@pytest.mark.django_db
class TestRecommendedFields:
    """``related_feature`` and ``remarks`` (§4.1 fields 10 and 11)."""

    def test_the_defaults_are_empty(self, module, project):
        case = _case(module, project)
        assert case.related_feature == ""
        assert case.remarks == ""

    def test_the_column_defaults_are_empty_as_well(self):
        """A row written by something that skips the model (a fixture, a
        bulk insert) gets the empty string, not NULL."""
        for field_name in ("related_feature", "remarks"):
            field = TestCase._meta.get_field(field_name)
            assert field.default == ""
            assert field.blank is True
            assert field.null is False

    def test_a_case_may_be_written_without_them(self, module, project, user):
        """``full_clean()`` — what a serializer calls — accepts the two
        columns unfilled: they are 推荐 in §4.1, not 必备."""
        case = TestCase(
            project=project,
            workspace=project.workspace,
            module=module,
            title="总开关关闭时子开关不可操作",
            purpose="验证联动",
            object_overview="设置窗口",
            priority="P1",
            preconditions="应用已启动",
            environment="macOS 15.2",
            created_by=user,
            updated_by=user,
        )
        case.full_clean()
        case.save()
        assert case.related_feature == ""
        assert case.remarks == ""

    def test_they_are_stored_as_written(self, module, project):
        case = _case(
            module,
            project,
            related_feature=(
                "「通知」设置区（需求条目：通知联动规则 v2）；取值来源：帮助页"
                "《快速上手》第 2 节、2026-11-09 的界面截图"
            ),
            remarks=(
                "步骤 4 勾了 ✘：开关已开启但说明文字未出现；步骤 7 未执行，"
                "未核实项：系统通知权限关闭时的提示语，来源：需求条目 v2"
            ),
        )
        case.refresh_from_db()
        assert "通知联动规则 v2" in case.related_feature
        assert "未核实项" in case.remarks

    def test_the_related_feature_and_the_work_item_link_coexist(self, module, project):
        """The text is what the author wrote against, the link is a living
        row; a case may carry both (the standard asks for the text, the
        product keeps the link)."""
        issue = Issue.objects.create(project=project, name="The work item behind the case")
        case = _case(module, project, related_feature="需求条目「通知联动规则 v2」")
        TestCaseIssue.objects.create(project=project, test_case=case, issue=issue)

        case.refresh_from_db()
        assert case.related_feature == "需求条目「通知联动规则 v2」"
        assert case.issue_links.count() == 1
        assert case.issue_links.get().issue_id == issue.pk


@pytest.mark.django_db
class TestRetestOfField:
    """The shape of the ``retest_of`` column (§6.2 rule 2)."""

    def test_the_field_is_a_nullable_self_reference_with_set_null(self):
        field = TestRun._meta.get_field("retest_of")
        assert field.null is True
        assert field.blank is True
        assert field.remote_field.model is TestRun
        assert field.remote_field.on_delete is models.SET_NULL
        assert field.remote_field.related_name == "retests"

    def test_a_batch_needs_no_retest_link(self, project):
        run = _run(project)
        assert run.retest_of_id is None
        assert run.retest_of is None

    def test_the_reverse_relation_lists_the_retests(self, project):
        original = _run(project)
        retest = _run(project, retest_of=original)
        assert list(original.retests.all()) == [retest]


@pytest.mark.django_db
class TestRetestOfChecks:
    """Same project, never itself, never a ring (§6.2 rule 2)."""

    def test_a_retest_may_point_at_a_completed_batch(self, project):
        """The ordinary case: the original batch is closed, the re-test
        follows it."""
        original = _run(project, status="completed")
        retest = _run(project, retest_of=original)
        assert retest.retest_of_id == original.pk
        assert original.status == "completed"

    def test_a_retest_may_point_at_an_open_batch(self, project):
        """The re-test may be opened while the first batch is still being
        written."""
        original = _run(project, status="in_progress")
        retest = _run(project, retest_of=original)
        assert retest.retest_of_id == original.pk

    def test_a_retest_may_point_at_a_retest(self, project):
        """A chain of three: the second re-test names the first one."""
        original = _run(project, status="completed")
        first_retest = _run(project, retest_of=original)
        second_retest = _run(project, retest_of=first_retest)
        assert second_retest.retest_of_id == first_retest.pk
        assert first_retest.retest_of_id == original.pk

    def test_a_link_across_projects_is_rejected(self, project, other_project):
        foreign = _run(other_project)
        with pytest.raises(ValidationError) as error:
            _run(project, retest_of=foreign)
        assert "retest_of" in error.value.error_dict
        assert RETEST_OF_PROJECT_MISMATCH in error.value.error_dict["retest_of"][0].message

    def test_a_link_to_itself_is_rejected(self, project):
        run = _run(project)
        run.retest_of = run
        with pytest.raises(ValidationError) as error:
            run.save()
        assert RETEST_OF_SELF in error.value.error_dict["retest_of"][0].message

    def test_a_link_that_closes_a_ring_is_rejected(self, project):
        """A → B, then B → A: the walk meets the batch being written."""
        first = _run(project)
        second = _run(project, retest_of=first)

        first.retest_of = second
        with pytest.raises(ValidationError) as error:
            first.save()
        assert RETEST_OF_CYCLE in error.value.error_dict["retest_of"][0].message

    def test_a_ring_between_other_rows_is_rejected(self, project):
        """A ring that does not contain the batch being written stops the
        walk instead of looping for ever. It can only be built by writing
        around ``save()``, which is exactly the shape the guard is for."""
        first = _run(project)
        second = _run(project, retest_of=first)
        # Bypass the model guard to plant a broken chain in the table.
        TestRun.all_objects.filter(pk=first.pk).update(retest_of=second)

        with pytest.raises(ValidationError) as error:
            _run(project, retest_of=first)
        assert RETEST_OF_CYCLE in error.value.error_dict["retest_of"][0].message

    def test_the_check_runs_in_clean_as_well(self, project, other_project):
        """A serializer calls ``full_clean()``; the guard has to fire
        there too."""
        foreign = _run(other_project)
        run = TestRun(
            project=project,
            run_date=date(2026, 11, 9),
            version_number="2.3.1",
            build_number="2310",
            commit_hash="8d5cf7e",
            environment="macOS 15.2",
            retest_of=foreign,
        )
        with pytest.raises(ValidationError) as error:
            run.full_clean()
        assert RETEST_OF_PROJECT_MISMATCH in error.value.error_dict["retest_of"][0].message

    def test_an_ordinary_update_keeps_the_link(self, project):
        original = _run(project, status="completed")
        retest = _run(project, retest_of=original)
        retest.environment = "Windows 11 on the test bench"
        retest.save()
        retest.refresh_from_db()
        assert retest.retest_of_id == original.pk
        assert retest.environment == "Windows 11 on the test bench"

    def test_a_completed_batch_may_not_gain_the_link(self, project):
        """The header of a completed batch is frozen; the link is part of
        the header."""
        other = _run(project)
        completed = _run(project, status="completed")
        completed.retest_of = other
        with pytest.raises(ValidationError) as error:
            completed.save()
        assert "retest_of" in str(error.value)

    def test_deleting_the_original_batch_leaves_the_retest_without_a_link(self, project):
        """``SET_NULL``: the re-test is a record of its own and survives the
        deletion of the batch it names."""
        original = _run(project)
        retest = _run(project, retest_of=original)

        # ``all_objects`` is the plain manager: its queryset delete removes
        # the row for real, which is what fires ``SET_NULL``.
        TestRun.all_objects.filter(pk=original.pk).delete()

        retest.refresh_from_db()
        assert retest.retest_of_id is None


@pytest.mark.django_db
class TestMigration:
    """The migration carries the three columns to the database."""

    @staticmethod
    def _columns(table):
        with connection.cursor() as cursor:
            description = connection.introspection.get_table_description(cursor, table)
        return {column.name for column in description}

    def test_the_two_case_columns_exist(self):
        columns = self._columns(TestCase._meta.db_table)
        assert {"related_feature", "remarks"} <= columns

    def test_the_retest_link_column_exists(self):
        columns = self._columns(TestRun._meta.db_table)
        assert "retest_of_id" in columns

    def test_the_retest_link_column_is_nullable(self):
        with connection.cursor() as cursor:
            columns = {
                column.name: column
                for column in connection.introspection.get_table_description(
                    cursor, TestRun._meta.db_table
                )
            }
        assert columns["retest_of_id"].null_ok is True
