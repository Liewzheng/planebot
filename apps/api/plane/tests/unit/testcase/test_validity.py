# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Unit tests for the record-validity rules (STD-SW-003 §6).

The rules are pure functions, so these tests need no database. Every rule
of the standard gets its own test, and each test names the reason code it
expects — so removing a rule from
:mod:`plane.db.testcase_validity` turns the matching test red instead of
silently letting an invalid record through.
"""

from datetime import date

import pytest

from plane.db.testcase_validity import (
    ACTUAL_VALUE_IS_EVALUATION,
    ACTUAL_VALUE_WITHOUT_CONCLUSION,
    ACTION_EMPTY,
    CASE_NUMBER_FORMAT,
    CASE_NUMBER_MODULE_MISMATCH,
    CONCLUSION_FAIL_WITHOUT_FAILED_STEP,
    CONCLUSION_FAIL_WITH_UNSET_STEP,
    CONCLUSION_INVALID,
    CONCLUSION_IS_FINAL,
    CONCLUSION_MANUAL_WITHOUT_CONDITION,
    CONCLUSION_MANUAL_WITHOUT_EXECUTOR,
    CONCLUSION_NA_WITHOUT_CRITERIA_SOURCE,
    CONCLUSION_NA_WITHOUT_REASON,
    CONCLUSION_PASS_WITH_FAILED_STEP,
    CONCLUSION_PASS_WITH_UNSET_STEP,
    DUPLICATE_CASE_NUMBER,
    ENVIRONMENT_MISSING,
    EVIDENCE_MISSING,
    EXPECTED_EMPTY,
    FAIL_WITHOUT_ACTUAL_VALUE,
    MODULE_CODE_FORMAT,
    OBJECT_OVERVIEW_MISSING,
    PRECONDITIONS_MISSING,
    PRIORITY_INVALID,
    PURPOSE_MISSING,
    REASON_MESSAGES,
    RUN_BUILD_NUMBER_MISSING,
    RUN_COMMIT_HASH_MISSING,
    RUN_COMPLETED,
    RUN_DATE_MISSING,
    RUN_ENVIRONMENT_MISSING,
    RUN_EXECUTED_BY_MISSING,
    RUN_NUMBER_FORMAT,
    RUN_VERSION_NUMBER_MISSING,
    STEP_CONCLUSION_INVALID,
    STEP_COUNT_ZERO,
    STEP_CONCLUSION_UNSET,
    TITLE_MISSING,
    RunCaseData,
    RunData,
    StepRecordData,
    is_evaluation_phrase,
    validate_run,
    validate_run_case,
    validate_run_write,
)
from plane.db.testcase_validity import (
    RUN_CASE_CONCLUSION_FAIL,
    RUN_CASE_CONCLUSION_MANUAL,
    RUN_CASE_CONCLUSION_NA,
    RUN_CASE_CONCLUSION_PASS,
    STEP_VERDICT_FAIL,
    STEP_VERDICT_PASS,
    STEP_VERDICT_UNSET,
)


pytestmark = pytest.mark.unit


def _step(
    sequence_id=1,
    conclusion=STEP_VERDICT_PASS,
    actual_value="",
    action="Open the login page",
    expected_result="The login page is shown",
):
    return StepRecordData(
        sequence_id=sequence_id,
        action=action,
        expected_result=expected_result,
        conclusion=conclusion,
        actual_value=actual_value,
    )


def _case(**overrides):
    """A case record that satisfies every rule."""
    base = {
        "case_number": "TC-CLI-001",
        "module_code": "CLI",
        "title": "Log in with a valid account",
        "purpose": "Make sure the login path works",
        "object_overview": "The login form of pbot",
        "priority": "P1",
        "preconditions": "The account exists",
        "environment": "Windows 10, pbot 1.2.3",
        "steps": (_step(),),
        "conclusion": RUN_CASE_CONCLUSION_PASS,
        "evidence": "screenshot 2026-11-09.png",
    }
    base.update(overrides)
    return RunCaseData(**base)


def _run(**overrides):
    """A batch that satisfies every rule."""
    base = {
        "number": "RUN-20261109-01",
        "run_date": date(2026, 11, 9),
        "version_number": "1.2.3",
        "build_number": "b4021",
        "commit_hash": "9f1c0de",
        "environment": "Windows 10 on the test bench",
        "executed_by": "Li Wei",
        "status": "in_progress",
        "cases": (_case(),),
    }
    base.update(overrides)
    return RunData(**base)


class TestValidRecords:
    def test_a_complete_case_is_valid(self):
        assert validate_run_case(_case()) == []

    def test_a_complete_batch_is_valid(self):
        assert validate_run(_run()) == []

    def test_a_batch_in_progress_may_still_be_written(self):
        assert validate_run_write(_run(status="in_progress")) == []

    def test_a_draft_batch_may_still_be_written(self):
        assert validate_run_write(_run(status="draft")) == []


class TestNumberAndModuleCode:
    def test_a_malformed_case_number_is_rejected(self):
        assert CASE_NUMBER_FORMAT in validate_run_case(_case(case_number="TC-CLI-1"))

    def test_a_lowercase_module_code_is_rejected(self):
        assert MODULE_CODE_FORMAT in validate_run_case(_case(module_code="cli"))

    def test_a_one_letter_module_code_is_rejected(self):
        assert MODULE_CODE_FORMAT in validate_run_case(_case(module_code="C"))

    def test_the_number_must_carry_the_module_code_of_the_case(self):
        reasons = validate_run_case(_case(case_number="TC-FW-001", module_code="CLI"))
        assert CASE_NUMBER_MODULE_MISMATCH in reasons


class TestMandatoryHeader:
    @pytest.mark.parametrize(
        "field,code",
        [
            ("title", TITLE_MISSING),
            ("purpose", PURPOSE_MISSING),
            ("object_overview", OBJECT_OVERVIEW_MISSING),
            ("preconditions", PRECONDITIONS_MISSING),
            ("environment", ENVIRONMENT_MISSING),
        ],
    )
    def test_a_missing_header_item_is_rejected(self, field, code):
        assert code in validate_run_case(_case(**{field: "  "}))

    def test_a_missing_priority_is_rejected(self):
        assert PRIORITY_INVALID in validate_run_case(_case(priority=""))

    def test_an_unknown_priority_is_rejected(self):
        assert PRIORITY_INVALID in validate_run_case(_case(priority="P9"))

    @pytest.mark.parametrize("priority", ["P0", "P1", "P2", "P3"])
    def test_every_documented_priority_is_accepted(self, priority):
        assert validate_run_case(_case(priority=priority)) == []


class TestStepTable:
    def test_a_case_without_a_step_is_rejected(self):
        assert STEP_COUNT_ZERO in validate_run_case(_case(steps=()))

    def test_a_step_without_an_action_is_rejected(self):
        reasons = validate_run_case(_case(steps=(_step(action=""),)))
        assert "step_1:" + ACTION_EMPTY in reasons

    def test_a_step_without_an_expected_result_is_rejected(self):
        reasons = validate_run_case(_case(steps=(_step(expected_result=""),)))
        assert "step_1:" + EXPECTED_EMPTY in reasons

    def test_an_unknown_step_verdict_is_rejected(self):
        reasons = validate_run_case(
            _case(steps=(_step(conclusion="maybe"),), conclusion=RUN_CASE_CONCLUSION_PASS)
        )
        assert "step_1:" + STEP_CONCLUSION_INVALID in reasons

    def test_a_blank_step_verdict_is_rejected_under_pass(self):
        reasons = validate_run_case(
            _case(steps=(_step(conclusion=STEP_VERDICT_UNSET),))
        )
        assert "step_1:" + STEP_CONCLUSION_UNSET in reasons
        assert CONCLUSION_PASS_WITH_UNSET_STEP in reasons

    def test_a_blank_step_verdict_is_rejected_under_fail(self):
        reasons = validate_run_case(
            _case(
                steps=(
                    _step(1, conclusion=STEP_VERDICT_FAIL, actual_value="error 42"),
                    _step(2, conclusion=STEP_VERDICT_UNSET),
                ),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert "step_2:" + STEP_CONCLUSION_UNSET in reasons
        assert CONCLUSION_FAIL_WITH_UNSET_STEP in reasons

    def test_a_failed_step_without_a_measured_value_is_rejected(self):
        reasons = validate_run_case(
            _case(
                steps=(_step(conclusion=STEP_VERDICT_FAIL, actual_value=""),),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert "step_1:" + FAIL_WITHOUT_ACTUAL_VALUE in reasons

    def test_a_failed_step_with_the_observed_value_is_accepted(self):
        reasons = validate_run_case(
            _case(
                steps=(_step(conclusion=STEP_VERDICT_FAIL, actual_value="error 42"),),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert reasons == []

    def test_an_evaluation_word_is_not_a_measured_value(self):
        reasons = validate_run_case(
            _case(
                steps=(_step(conclusion=STEP_VERDICT_FAIL, actual_value="不一致"),),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert "step_1:" + ACTUAL_VALUE_IS_EVALUATION in reasons

    def test_an_evaluation_word_is_rejected_under_pass_as_well(self):
        reasons = validate_run_case(
            _case(steps=(_step(conclusion=STEP_VERDICT_PASS, actual_value="正常"),))
        )
        assert "step_1:" + ACTUAL_VALUE_IS_EVALUATION in reasons

    def test_the_observed_text_is_not_mistaken_for_an_evaluation_word(self):
        reasons = validate_run_case(
            _case(steps=(_step(conclusion=STEP_VERDICT_PASS, actual_value="显示 error 42"),))
        )
        assert reasons == []

    def test_a_measured_value_without_a_verdict_is_rejected(self):
        reasons = validate_run_case(
            _case(
                steps=(_step(conclusion=STEP_VERDICT_UNSET, actual_value="error 42"),),
                conclusion=RUN_CASE_CONCLUSION_NA,
                na_reason="not applicable",
                na_criteria_source="STD-SW-003 §5",
            )
        )
        assert "step_1:" + ACTUAL_VALUE_WITHOUT_CONCLUSION in reasons


class TestEvaluationWords:
    @pytest.mark.parametrize(
        "phrase",
        ["不一致", "正常", "异常", "正确", "错误", "符合预期", "通过", "不通过", "OK", "Pass", "失败"],
    )
    def test_a_bare_evaluation_word_is_detected(self, phrase):
        assert is_evaluation_phrase(phrase) is True

    @pytest.mark.parametrize(
        "phrase",
        ["显示 error 42", "命令失败，退出码 1", "0.12s", "不一致之处在于行号 42", ""],
    )
    def test_an_observation_is_not_an_evaluation_word(self, phrase):
        assert is_evaluation_phrase(phrase) is False


class TestConclusion:
    def test_an_unknown_conclusion_is_rejected(self):
        assert CONCLUSION_INVALID in validate_run_case(_case(conclusion="DONE"))

    def test_a_missing_conclusion_is_rejected(self):
        assert CONCLUSION_INVALID in validate_run_case(_case(conclusion=""))

    def test_pass_with_a_failed_step_is_rejected(self):
        reasons = validate_run_case(
            _case(
                steps=(_step(conclusion=STEP_VERDICT_FAIL, actual_value="error 42"),),
                conclusion=RUN_CASE_CONCLUSION_PASS,
            )
        )
        assert CONCLUSION_PASS_WITH_FAILED_STEP in reasons

    def test_fail_without_a_failed_step_is_rejected(self):
        reasons = validate_run_case(
            _case(steps=(_step(conclusion=STEP_VERDICT_PASS),), conclusion=RUN_CASE_CONCLUSION_FAIL)
        )
        assert CONCLUSION_FAIL_WITHOUT_FAILED_STEP in reasons

    def test_na_without_a_reason_is_rejected(self):
        reasons = validate_run_case(
            _case(
                conclusion=RUN_CASE_CONCLUSION_NA,
                na_reason="",
                na_criteria_source="STD-SW-003 §5",
            )
        )
        assert CONCLUSION_NA_WITHOUT_REASON in reasons

    def test_na_without_the_source_of_the_criteria_is_rejected(self):
        reasons = validate_run_case(
            _case(
                conclusion=RUN_CASE_CONCLUSION_NA,
                na_reason="the module is not built for Windows",
                na_criteria_source="",
            )
        )
        assert CONCLUSION_NA_WITHOUT_CRITERIA_SOURCE in reasons

    def test_na_with_reason_and_source_is_accepted(self):
        reasons = validate_run_case(
            _case(
                conclusion=RUN_CASE_CONCLUSION_NA,
                na_reason="the module is not built for Windows",
                na_criteria_source="STD-SW-003 §5",
            )
        )
        assert reasons == []

    def test_manual_without_an_executor_is_rejected(self):
        reasons = validate_run_case(
            _case(
                conclusion=RUN_CASE_CONCLUSION_MANUAL,
                manual_executor="",
                manual_condition="on the hardware bench",
            )
        )
        assert CONCLUSION_MANUAL_WITHOUT_EXECUTOR in reasons

    def test_manual_without_a_condition_is_rejected(self):
        reasons = validate_run_case(
            _case(
                conclusion=RUN_CASE_CONCLUSION_MANUAL,
                manual_executor="Zhang San",
                manual_condition="",
            )
        )
        assert CONCLUSION_MANUAL_WITHOUT_CONDITION in reasons

    def test_manual_with_executor_and_condition_is_accepted(self):
        reasons = validate_run_case(
            _case(
                conclusion=RUN_CASE_CONCLUSION_MANUAL,
                manual_executor="Zhang San",
                manual_condition="on the hardware bench",
            )
        )
        assert reasons == []


class TestEvidence:
    def test_a_missing_evidence_is_rejected(self):
        assert EVIDENCE_MISSING in validate_run_case(_case(evidence=""))

    def test_a_blank_evidence_is_rejected(self):
        assert EVIDENCE_MISSING in validate_run_case(_case(evidence="   "))


class TestBatchHeader:
    def test_a_malformed_batch_number_is_rejected(self):
        assert RUN_NUMBER_FORMAT in validate_run(_run(number="RUN-20261109-1"))

    def test_a_missing_execution_date_is_rejected(self):
        assert RUN_DATE_MISSING in validate_run(_run(run_date=None))

    def test_a_missing_version_number_is_rejected(self):
        assert RUN_VERSION_NUMBER_MISSING in validate_run(_run(version_number=""))

    def test_a_missing_build_number_is_rejected(self):
        assert RUN_BUILD_NUMBER_MISSING in validate_run(_run(build_number=""))

    def test_a_missing_commit_hash_is_rejected(self):
        assert RUN_COMMIT_HASH_MISSING in validate_run(_run(commit_hash=""))

    def test_a_missing_environment_is_rejected(self):
        assert RUN_ENVIRONMENT_MISSING in validate_run(_run(environment=""))

    def test_a_missing_executor_is_rejected(self):
        assert RUN_EXECUTED_BY_MISSING in validate_run(_run(executed_by=""))

    def test_all_four_environment_items_missing_are_all_reported(self):
        reasons = validate_run(
            _run(
                run_date=None,
                version_number="",
                build_number="",
                commit_hash="",
                environment="",
                executed_by="",
            )
        )
        for code in (
            RUN_DATE_MISSING,
            RUN_VERSION_NUMBER_MISSING,
            RUN_BUILD_NUMBER_MISSING,
            RUN_COMMIT_HASH_MISSING,
            RUN_ENVIRONMENT_MISSING,
            RUN_EXECUTED_BY_MISSING,
        ):
            assert code in reasons


class TestBatchContent:
    def test_the_same_case_twice_in_one_batch_is_rejected(self):
        reasons = validate_run(_run(cases=(_case(), _case())))
        assert "TC-CLI-001|" + DUPLICATE_CASE_NUMBER in reasons

    def test_two_different_cases_in_one_batch_are_accepted(self):
        second = _case(case_number="TC-CLI-002")
        assert validate_run(_run(cases=(_case(), second))) == []

    def test_the_reason_of_a_case_carries_the_case_number(self):
        reasons = validate_run(_run(cases=(_case(evidence=""),)))
        assert "TC-CLI-001|" + EVIDENCE_MISSING in reasons


class TestCompletedBatch:
    def test_a_completed_batch_may_not_be_written(self):
        assert validate_run_write(_run(status="completed")) == [RUN_COMPLETED]

    def test_a_completed_batch_still_validates_its_content(self):
        # Freezing the batch is a write rule, not a content rule: a
        # completed batch that was valid stays valid.
        assert validate_run(_run(status="completed")) == []


class TestReasonMessages:
    def test_every_reason_carries_a_message(self):
        """A reason code without a message would reach the API as a bare
        string; the table is what the API and the log lines use."""
        emitted = set()

        broken_case = _case(
            case_number="nonsense",
            module_code="x",
            title="",
            purpose="",
            object_overview="",
            priority="P9",
            preconditions="",
            environment="",
            steps=(_step(conclusion="nonsense", actual_value="不一致"),),
            conclusion="nonsense",
            evidence="",
        )
        emitted.update(validate_run_case(broken_case))

        broken_run = _run(
            number="nonsense",
            run_date=None,
            version_number="",
            build_number="",
            commit_hash="",
            environment="",
            executed_by="",
            cases=(broken_case, broken_case),
        )
        emitted.update(validate_run(broken_run))

        bare_codes = {reason.split(":")[-1].split("|")[-1] for reason in emitted}
        missing = bare_codes - set(REASON_MESSAGES)
        assert not missing, f"reason codes without a message: {sorted(missing)}"

    def test_every_message_names_its_code(self):
        for code, message in REASON_MESSAGES.items():
            assert isinstance(code, str) and code
            assert isinstance(message, str) and message

    def test_the_final_conclusion_code_is_part_of_the_enumeration(self):
        """``conclusion_is_final`` is raised by the model layer, not by
        the validators, so the completeness walk above never sees it."""
        assert CONCLUSION_IS_FINAL in REASON_MESSAGES
        assert REASON_MESSAGES[CONCLUSION_IS_FINAL]
