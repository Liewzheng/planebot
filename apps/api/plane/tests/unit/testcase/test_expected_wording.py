# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The unverifiable wording of an expected result (STD-SW-003 §5.6).

§5.6: 「预期结果里出现上表左栏任一写法，该用例按「未写完」处理，不得进入执行
批次。」 The ten wordings of the table are the list in
:data:`plane.db.testcase_validity.UNJUDGEABLE_EXPECTED_PHRASES`.

Two properties are tested here that the rule exists for:

* the check is **inside** the expected sentence — the counter-example of
  §5.6 is a whole sentence (「切换语言后界面显示正常」), not a bare word;
* the check is **not** the measured-value check. ``actual_value_is_evaluation``
  asks whether a measured value is nothing but a judgement;
  ``expected_is_unjudgeable`` asks whether a reader could judge the
  criterion at all. A record can trip one without the other.
"""

from datetime import date

import pytest

from plane.db.testcase_validity import (
    ACTUAL_VALUE_IS_EVALUATION,
    EXPECTED_EMPTY,
    EXPECTED_IS_UNJUDGEABLE,
    REASON_MESSAGES,
    UNJUDGEABLE_EXPECTED_PHRASES,
    UNJUDGEABLE_EXPECTED_WORDINGS,
    RunCaseData,
    RunData,
    StepRecordData,
    first_unjudgeable_phrase,
    is_unjudgeable_expected_result,
    validate_run,
    validate_run_case,
)
from plane.db.testcase_validity import (
    RUN_CASE_CONCLUSION_FAIL,
    RUN_CASE_CONCLUSION_PASS,
    STEP_VERDICT_FAIL,
    STEP_VERDICT_PASS,
)

pytestmark = pytest.mark.unit


def _step(
    sequence_id=1,
    conclusion=STEP_VERDICT_PASS,
    actual_value="",
    action="点「通知」设置区的第一行开关",
    expected_result="圆点在左端，底色为灰",
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
        "case_number": "TC-SET-001",
        "module_code": "SET",
        "title": "总开关关闭时子开关不可操作",
        "purpose": "验证总开关与子开关的联动",
        "object_overview": "设置窗口「通知」区的右栏",
        "priority": "P1",
        "preconditions": "应用已安装并启动",
        "environment": "macOS 15.2, 应用 2.3.1",
        "steps": (_step(),),
        "conclusion": RUN_CASE_CONCLUSION_PASS,
        "evidence": "docs/evidence/2026-11-10/TC-SET-001/notify-on.png",
    }
    base.update(overrides)
    return RunCaseData(**base)


def _run(**overrides):
    base = {
        "number": "RUN-20261110-01",
        "run_date": date(2026, 11, 10),
        "version_number": "2.3.1",
        "build_number": "2310",
        "commit_hash": "8d5cf7e",
        "environment": "macOS 15.2 / MacBook Pro 14",
        "executed_by": "张三",
        "status": "in_progress",
        "cases": (_case(),),
    }
    base.update(overrides)
    return RunData(**base)


#: The ten wordings of §5.6, each in the shape the table prints it.
SECTION_5_6_PHRASES = [
    "正常",
    "正确",
    "无异常",
    "友好",
    "流畅",
    "美观",
    "符合设计",
    "应该",
    "大概",
    "一般",
]

#: The same ten written in the other script the project writes in, keyed by
#: the §5.6 wording they stand for.
TRADITIONAL_VARIANTS = {
    "正確": "正确",
    "無異常": "无异常",
    "流暢": "流畅",
    "美觀": "美观",
    "符合設計": "符合设计",
    "應該": "应该",
}

#: The English renderings of the same words, keyed the same way.
ENGLISH_VARIANTS = {
    "normal": "正常",
    "correct": "正确",
    "no error": "无异常",
    "error-free": "无异常",
    "friendly": "友好",
    "user-friendly": "友好",
    "smooth": "流畅",
    "beautiful": "美观",
    "good-looking": "美观",
    "as designed": "符合设计",
    "matches the design": "符合设计",
    "per the design": "符合设计",
    "should": "应该",
    "roughly": "大概",
    "approximately": "大概",
    "generally": "一般",
    "in general": "一般",
}


class TestTheListItself:
    def test_the_list_is_the_ten_wordings_of_the_section(self):
        """The mission fixes the list at the ten words of §5.6 — no more,
        no fewer."""
        assert set(SECTION_5_6_PHRASES) == set(UNJUDGEABLE_EXPECTED_PHRASES)

    def test_every_spelling_maps_to_one_of_the_ten(self):
        for spelling, wording in UNJUDGEABLE_EXPECTED_WORDINGS.items():
            assert wording in SECTION_5_6_PHRASES, spelling

    def test_the_traditional_variants_are_covered(self):
        for spelling, wording in TRADITIONAL_VARIANTS.items():
            assert UNJUDGEABLE_EXPECTED_WORDINGS[spelling] == wording

    def test_the_english_variants_are_covered(self):
        for spelling, wording in ENGLISH_VARIANTS.items():
            assert UNJUDGEABLE_EXPECTED_WORDINGS[spelling] == wording

    def test_the_boolean_helper_agrees_with_the_lookup(self):
        assert is_unjudgeable_expected_result("界面显示正常") is True
        assert is_unjudgeable_expected_result("圆点在左端，底色为灰") is False
        assert is_unjudgeable_expected_result("") is False
        assert is_unjudgeable_expected_result(None) is False

    def test_the_code_carries_a_message_that_quotes_the_section(self):
        assert EXPECTED_IS_UNJUDGEABLE in REASON_MESSAGES
        assert "§5.6" in REASON_MESSAGES[EXPECTED_IS_UNJUDGEABLE]


class TestBareWordings:
    @pytest.mark.parametrize("wording", SECTION_5_6_PHRASES)
    def test_the_wording_alone_is_rejected(self, wording):
        """The expected result is nothing but the wording."""
        assert first_unjudgeable_phrase(wording) == wording
        reasons = validate_run_case(_case(steps=(_step(expected_result=wording),)))
        assert "step_1:" + EXPECTED_IS_UNJUDGEABLE in reasons

    @pytest.mark.parametrize("wording", SECTION_5_6_PHRASES)
    def test_the_wording_inside_a_sentence_is_rejected(self, wording):
        """§5.6 rejects the word, not the whole sentence."""
        expected = f"切换语言后，设置页按钮显示{wording}。"
        assert first_unjudgeable_phrase(expected) == wording
        reasons = validate_run_case(_case(steps=(_step(expected_result=expected),)))
        assert "step_1:" + EXPECTED_IS_UNJUDGEABLE in reasons


class TestCounterExampleOfTheSection:
    def test_the_counter_example_of_the_section_is_rejected(self):
        expected = "切换语言后界面显示正常"
        assert first_unjudgeable_phrase(expected) == "正常"
        reasons = validate_run_case(_case(steps=(_step(expected_result=expected),)))
        assert reasons == ["step_1:" + EXPECTED_IS_UNJUDGEABLE]

    def test_the_good_example_of_the_section_is_accepted(self):
        """§5.6 正例: three concrete strings, nothing to judge."""
        expected = (
            "切换语言后，设置页按钮文本依次为 `Settings`、`Language`、`Done`；"
            "退出应用重启后仍为英文"
        )
        assert first_unjudgeable_phrase(expected) is None
        assert validate_run_case(_case(steps=(_step(expected_result=expected),))) == []

    def test_an_enumerated_criterion_is_accepted(self):
        """§5.5: the four legal values, and what a fifth one means."""
        expected = (
            "标签文字必须是「可用」「缺失」「不可用」「未知」四者之一；"
            "出现其它文字，或根本没有这个标签 → ✘。"
        )
        assert first_unjudgeable_phrase(expected) is None


class TestOtherScriptsAndLanguages:
    @pytest.mark.parametrize("spelling,wording", sorted(TRADITIONAL_VARIANTS.items()))
    def test_a_traditional_spelling_is_rejected(self, spelling, wording):
        assert first_unjudgeable_phrase(f"切换后界面显示{spelling}") == wording
        reasons = validate_run_case(
            _case(steps=(_step(expected_result=f"切换后界面显示{spelling}"),))
        )
        assert "step_1:" + EXPECTED_IS_UNJUDGEABLE in reasons

    @pytest.mark.parametrize("spelling,wording", sorted(ENGLISH_VARIANTS.items()))
    def test_an_english_spelling_is_rejected(self, spelling, wording):
        assert first_unjudgeable_phrase(f"The dialog is {spelling}") == wording
        reasons = validate_run_case(
            _case(steps=(_step(expected_result=f"The dialog is {spelling}"),))
        )
        assert "step_1:" + EXPECTED_IS_UNJUDGEABLE in reasons

    @pytest.mark.parametrize("spelling", ["correctly", "normally", "smoothly"])
    def test_an_inflected_english_spelling_is_rejected(self, spelling):
        """An inflection is the same word: the ban does not end at the
        stem."""
        assert first_unjudgeable_phrase(f"The dialog opens {spelling}") is not None

    def test_a_full_width_english_spelling_is_rejected(self):
        assert first_unjudgeable_phrase("ＴＨＥ　ＤＩＡＬＯＧ　ＩＳ　ＮＯＲＭＡＬ") == "正常"

    def test_the_case_of_the_letters_does_not_matter(self):
        assert first_unjudgeable_phrase("The dialog is NORMAL") == "正常"


class TestMarkupAndSpacing:
    def test_the_markup_does_not_hide_the_wording(self):
        assert first_unjudgeable_phrase("**正常**") == "正常"
        assert first_unjudgeable_phrase("<b>正常</b>") == "正常"
        assert first_unjudgeable_phrase("界面**显示正常**") == "正常"

    def test_a_blank_between_the_characters_does_not_hide_the_wording(self):
        assert first_unjudgeable_phrase("界面显示正 常") == "正常"

    def test_a_line_break_does_not_hide_the_wording(self):
        assert first_unjudgeable_phrase("界面显示\n正常") == "正常"


class TestNotTheMeasuredValueRule:
    def test_this_code_is_not_the_measured_value_code(self):
        assert EXPECTED_IS_UNJUDGEABLE != ACTUAL_VALUE_IS_EVALUATION

    def test_a_judgeable_expectation_with_a_judged_value_only_trips_the_value_code(self):
        """The value is nothing but a judgement, the criterion is fine."""
        reasons = validate_run_case(
            _case(
                steps=(
                    _step(
                        conclusion=STEP_VERDICT_FAIL,
                        expected_result="圆点在左端，底色为灰",
                        actual_value="不一致",
                    ),
                ),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert "step_1:" + ACTUAL_VALUE_IS_EVALUATION in reasons
        assert "step_1:" + EXPECTED_IS_UNJUDGEABLE not in reasons

    def test_an_unjudgeable_expectation_with_a_reported_value_only_trips_the_expected_code(self):
        """The value reports what was seen, the criterion cannot be
        judged."""
        reasons = validate_run_case(
            _case(
                steps=(
                    _step(
                        conclusion=STEP_VERDICT_FAIL,
                        expected_result="界面友好",
                        actual_value="点完开关后「每日摘要」一行下方无任何说明文字",
                    ),
                ),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert "step_1:" + EXPECTED_IS_UNJUDGEABLE in reasons
        assert "step_1:" + ACTUAL_VALUE_IS_EVALUATION not in reasons

    def test_a_record_that_trips_both_keeps_both_codes(self):
        reasons = validate_run_case(
            _case(
                steps=(
                    _step(
                        conclusion=STEP_VERDICT_FAIL,
                        expected_result="界面显示正常",
                        actual_value="不一致",
                    ),
                ),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert "step_1:" + EXPECTED_IS_UNJUDGEABLE in reasons
        assert "step_1:" + ACTUAL_VALUE_IS_EVALUATION in reasons


class TestTheCaseDoesNotEnterTheBatch:
    def test_the_reason_is_routed_to_the_case_of_the_batch(self):
        run = _run(
            cases=(
                _case(steps=(_step(expected_result="切换语言后界面显示正常"),)),
                _case(case_number="TC-SET-002"),
            )
        )
        assert "TC-SET-001|step_1:" + EXPECTED_IS_UNJUDGEABLE in validate_run(run)

    def test_an_empty_expected_result_keeps_its_own_code(self):
        """An empty expected result is ``expected_empty``; the banned-word
        code is about a filled one."""
        reasons = validate_run_case(_case(steps=(_step(expected_result="   "),)))
        assert reasons == ["step_1:" + EXPECTED_EMPTY]

    def test_the_code_survives_the_authoring_filter(self):
        """The authoring surface of the app keeps the authoring codes; an
        unjudgeable expectation is an authoring problem, not an execution
        one."""
        from plane.app.testcase_validity import check_authoring

        reasons = check_authoring(
            "SET",
            {
                "title": "总开关关闭时子开关不可操作",
                "purpose": "验证总开关与子开关的联动",
                "object_overview": "设置窗口「通知」区的右栏",
                "priority": "P1",
                "preconditions": "应用已安装并启动",
                "environment": "macOS 15.2, 应用 2.3.1",
            },
            [
                {
                    "action": "点「通知」设置区的第一行开关",
                    "expected_result": "切换语言后界面显示正常",
                }
            ],
        )
        assert "step_1:" + EXPECTED_IS_UNJUDGEABLE in reasons
