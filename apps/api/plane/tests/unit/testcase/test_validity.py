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
    ACTUAL_VALUE_IS_IMAGE_ONLY,
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
    contains_image,
    is_evaluation_phrase,
    is_filled_markdown,
    is_placeholder,
    markdown_text,
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
    attachment_count=0,
):
    return StepRecordData(
        sequence_id=sequence_id,
        action=action,
        expected_result=expected_result,
        conclusion=conclusion,
        actual_value=actual_value,
        attachment_count=attachment_count,
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
        "evidence_attachment_count": 0,
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


#: The shapes an image can be written in. Every one of them is a picture
#: and no text: the plain inline image, one whose file name carries a
#: parenthesis, one with balanced parentheses at any depth, the
#: reference style with and without its definition, and the one whose
#: syntax was never closed.
IMAGE_SHAPES = [
    "![](https://files.test/shot.png)",
    "![](Screenshot (1).png)",
    "![](a(b).png)",
    "![](a(b(c)).png)",
    "![](a(b(c(d)).png))",
    "![alt][ref]",
    "![shot][img]\n\n[img]: https://files.test/shot.png",
    "![](https://files.test/shot.png",
]

#: The shapes the reviewer tabulated, with what the rules must say about
#: them: the text the value carries, whether that is enough to be a
#: measured value, whether the value holds an image, and whether a
#: failed step with that value is told to attach the file.
#:
#: The list is the parity list: the rules read every shape the same way
#: the front end renders it, and the two shapes CommonMark does not
#: recognise but the tower ruled to be images are the only deviation.
PARITY_CASES = [
    # value, text, filled, image, image hint
    ("![](https://files.test/shot.png)", "", False, True, True),
    ("![](Screenshot (1).png)", "", False, True, True),
    ("![](a(b).png)", "", False, True, True),
    ("![alt][ref]", "", False, True, True),
    ("![](https://files.test/shot.png", "", False, True, True),
    ("exit 1 ![](Screenshot (1).png)", "exit 1", True, True, False),
    ("line one\nline two ![alt](url", "line one\nline two", True, True, False),
    ("![](a(b(c)).png)", "", False, True, True),
    ("![](a(b(c(d)).png))", "", False, True, True),
    ("![shot][img]\n\n[img]: https://files.test/shot.png", "", False, True, True),
    ("[log]: http://x.test", "", False, False, False),
    ("`![](x.png)`", "![](x.png)", True, False, False),
    ("![](a.png error 42", "error 42", True, True, False),
    # the image is taken out of the text and leaves a blank behind it
    ("the marker ![x](y after the marker", "the marker   after the marker", True, True, False),
]


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

    def test_an_evaluation_word_written_in_markdown_is_detected(self):
        """The markup is not what the value says: ``**不一致**`` says
        exactly what ``不一致`` says."""
        assert is_evaluation_phrase("**不一致**") is True
        assert is_evaluation_phrase("<b>正常</b>") is True
        assert is_evaluation_phrase("`不一致`") is True

    def test_a_yes_or_no_answer_is_an_observation(self):
        """The list of evaluation words carries judgements only.

        An answer to a yes/no question reports what was seen, so "no" is
        a legitimate measured value ("did the device answer? — no"). The
        judgement words stay in the list.
        """
        assert is_evaluation_phrase("no") is False
        assert is_evaluation_phrase("yes") is False
        assert is_evaluation_phrase("ok") is True
        assert is_evaluation_phrase("pass") is True
        assert is_evaluation_phrase("fail") is True
        assert is_evaluation_phrase("n/a") is True


class TestMarkdownMeasuredValue:
    """The measured value is Markdown source: the markup is read, not stored."""

    def test_a_plain_text_is_a_measured_value(self):
        reasons = validate_run_case(
            _case(
                steps=(_step(conclusion=STEP_VERDICT_FAIL, actual_value="error 42"),),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert reasons == []

    def test_the_markup_does_not_hide_the_text(self):
        reasons = validate_run_case(
            _case(
                steps=(_step(conclusion=STEP_VERDICT_FAIL, actual_value="**error 42**"),),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert reasons == []

    def test_an_evaluation_word_written_in_markdown_is_still_rejected(self):
        reasons = validate_run_case(
            _case(
                steps=(_step(conclusion=STEP_VERDICT_FAIL, actual_value="**不一致**"),),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert "step_1:" + ACTUAL_VALUE_IS_EVALUATION in reasons

    def test_an_evaluation_word_written_in_html_is_still_rejected(self):
        reasons = validate_run_case(
            _case(
                steps=(_step(conclusion=STEP_VERDICT_FAIL, actual_value="<b>正常</b>"),),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert "step_1:" + ACTUAL_VALUE_IS_EVALUATION in reasons

    @pytest.mark.parametrize("value", IMAGE_SHAPES)
    def test_a_pure_image_is_not_a_measured_value(self, value):
        """An image is a picture, not a sentence — in every shape."""
        reasons = validate_run_case(
            _case(
                steps=(_step(conclusion=STEP_VERDICT_FAIL, actual_value=value),),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert "step_1:" + FAIL_WITHOUT_ACTUAL_VALUE in reasons

    @pytest.mark.parametrize("value", IMAGE_SHAPES)
    def test_a_pure_image_names_the_missing_file(self, value):
        reasons = validate_run_case(
            _case(
                steps=(_step(conclusion=STEP_VERDICT_FAIL, actual_value=value),),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert "step_1:" + ACTUAL_VALUE_IS_IMAGE_ONLY in reasons

    def test_the_alt_text_of_an_image_is_no_text(self):
        """An image contributes no text of its own — not even its alt
        text. The picture counts when the file is attached to the step."""
        reasons = validate_run_case(
            _case(
                steps=(
                    _step(
                        conclusion=STEP_VERDICT_FAIL,
                        actual_value="![the login screen](https://files.test/shot.png)",
                    ),
                ),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert "step_1:" + FAIL_WITHOUT_ACTUAL_VALUE in reasons
        assert markdown_text("![the login screen](https://files.test/shot.png)").strip() == ""

    def test_an_image_next_to_a_text_is_a_measured_value(self):
        reasons = validate_run_case(
            _case(
                steps=(
                    _step(
                        conclusion=STEP_VERDICT_FAIL,
                        actual_value="error 42 ![](https://files.test/shot.png)",
                    ),
                ),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert reasons == []

    def test_a_placeholder_is_not_a_measured_value(self):
        reasons = validate_run_case(
            _case(
                steps=(_step(conclusion=STEP_VERDICT_FAIL, actual_value="待补充"),),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert "step_1:" + FAIL_WITHOUT_ACTUAL_VALUE in reasons

    def test_a_blank_value_is_not_a_measured_value(self):
        reasons = validate_run_case(
            _case(
                steps=(_step(conclusion=STEP_VERDICT_FAIL, actual_value="   "),),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert "step_1:" + FAIL_WITHOUT_ACTUAL_VALUE in reasons


class TestMarkdownHelpers:
    @pytest.mark.parametrize(
        "value",
        ["", "   ", "![](a.png)", "![](a.png) ![](b.png)", "-", "待补充", "**-**", "<b></b>"],
    )
    def test_a_value_without_text_is_not_filled(self, value):
        assert is_filled_markdown(value) is False

    @pytest.mark.parametrize(
        "value",
        [
            "error 42",
            "**error 42**",
            "<b>error 42</b>",
            "![shot](a.png) error 42",
            "[exit code 1](http://x.test)",
        ],
    )
    def test_a_value_with_text_is_filled(self, value):
        assert is_filled_markdown(value) is True

    def test_the_text_of_a_link_stays(self):
        assert markdown_text("[exit code 1](http://x.test)") == "exit code 1"

    def test_the_text_of_a_reference_link_stays(self):
        """A reference link needs its definition — without one it is
        literal text, the way CommonMark reads it."""
        assert markdown_text("[exit code 1][log]\n\n[log]: http://x.test").strip() == "exit code 1"
        assert markdown_text("[exit code 1][log]") == "[exit code 1][log]"

    def test_an_image_carries_no_text(self):
        assert markdown_text("![](a.png)").strip() == ""

    @pytest.mark.parametrize("value", IMAGE_SHAPES)
    def test_every_shape_of_an_image_carries_no_text(self, value):
        assert markdown_text(value).strip() == ""
        assert is_filled_markdown(value) is False

    @pytest.mark.parametrize("value", IMAGE_SHAPES)
    def test_every_shape_of_an_image_is_an_image(self, value):
        assert contains_image(value) is True

    def test_a_bare_reference_definition_is_no_text(self):
        """``[log]: http://x.test`` is markup, not a sentence."""
        assert markdown_text("[log]: http://x.test").strip() == ""
        assert is_filled_markdown("[log]: http://x.test") is False
        assert contains_image("[log]: http://x.test") is False

    def test_a_bare_reference_definition_is_not_a_measured_value(self):
        reasons = validate_run_case(
            _case(
                steps=(_step(conclusion=STEP_VERDICT_FAIL, actual_value="[log]: http://x.test"),),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert "step_1:" + FAIL_WITHOUT_ACTUAL_VALUE in reasons
        assert "step_1:" + ACTUAL_VALUE_IS_IMAGE_ONLY not in reasons


class TestParity:
    """The rules read every shape the way the front end renders it.

    The list is the one the reviewer tabulated: the text the value
    carries, whether that is a measured value, whether the value holds an
    image, and whether a failed step is told to attach the file.
    """

    @pytest.mark.parametrize(
        "value,text,filled,image,hint",
        PARITY_CASES,
        ids=[case[0] for case in PARITY_CASES],
    )
    def test_the_shape_is_read_as_it_is_listed(self, value, text, filled, image, hint):
        assert markdown_text(value).strip() == text
        assert is_filled_markdown(value) is filled
        assert contains_image(value) is image

        reasons = validate_run_case(
            _case(
                steps=(_step(conclusion=STEP_VERDICT_FAIL, actual_value=value),),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert ("step_1:" + ACTUAL_VALUE_IS_IMAGE_ONLY in reasons) is hint
        assert ("step_1:" + FAIL_WITHOUT_ACTUAL_VALUE in reasons) is (not filled)


class TestCodeSpans:
    """The content of a code span is literal text.

    No markup inside it is markup — an image inside backticks is a
    quotation, not a picture.
    """

    def test_an_image_inside_a_code_span_is_text(self):
        assert markdown_text("`![](x.png)`") == "![](x.png)"
        assert is_filled_markdown("`![](x.png)`") is True
        assert contains_image("`![](x.png)`") is False

    def test_an_image_inside_a_code_span_raises_no_image_hint(self):
        reasons = validate_run_case(
            _case(
                steps=(_step(conclusion=STEP_VERDICT_FAIL, actual_value="`![](x.png)`"),),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert reasons == []

    def test_an_evaluation_word_in_a_code_span_is_still_rejected(self):
        """`` `不一致` `` is the word itself — the backticks are
        formatting, the tester wrote the word."""
        assert is_evaluation_phrase("`不一致`") is True
        assert is_evaluation_phrase("`ok`") is True

        reasons = validate_run_case(
            _case(
                steps=(_step(conclusion=STEP_VERDICT_FAIL, actual_value="`不一致`"),),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert "step_1:" + ACTUAL_VALUE_IS_EVALUATION in reasons

    def test_a_quotation_of_the_raw_source_is_accepted(self):
        """`` `**不一致**` `` quotes the source; the content is not an
        evaluation word."""
        assert markdown_text("`**不一致**`") == "**不一致**"
        assert is_evaluation_phrase("`**不一致**`") is False

        reasons = validate_run_case(
            _case(
                steps=(_step(conclusion=STEP_VERDICT_FAIL, actual_value="`**不一致**`"),),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert "step_1:" + ACTUAL_VALUE_IS_EVALUATION not in reasons


class TestLooseFallback:
    """A malformed image takes only itself.

    The fallback takes the image out of the text — the words behind a
    malformed image are the tester's words and stay text.
    """

    def test_the_words_after_a_malformed_image_stay_text(self):
        assert markdown_text("![](a.png error 42").strip() == "error 42"
        assert is_filled_markdown("![](a.png error 42") is True

    def test_the_words_after_a_malformed_image_stay_text_in_a_sentence(self):
        # the image is taken out of the text and leaves a blank behind it
        assert markdown_text("the marker ![x](y after the marker") == "the marker   after the marker"
        assert is_filled_markdown("the marker ![x](y after the marker") is True

    def test_a_failed_step_with_words_after_a_malformed_image_is_accepted(self):
        """The tester wrote "error 42" — the rule may not call that
        "not measured"."""
        reasons = validate_run_case(
            _case(
                steps=(_step(conclusion=STEP_VERDICT_FAIL, actual_value="![](a.png error 42"),),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert reasons == []

    def test_a_pure_malformed_image_is_still_an_image(self):
        """The words are kept — the image is still taken out."""
        assert markdown_text("![](https://files.test/shot.png").strip() == ""
        assert contains_image("![](https://files.test/shot.png") is True


class TestHtmlEntities:
    """The entities of a text node are decoded — after the parse."""

    def test_an_evaluation_written_as_entities_is_rejected(self):
        assert markdown_text("&#x4E0D;&#x4E00;&#x81F4;") == "不一致"
        assert is_evaluation_phrase("&#x4E0D;&#x4E00;&#x81F4;") is True

        reasons = validate_run_case(
            _case(
                steps=(_step(conclusion=STEP_VERDICT_FAIL, actual_value="&#x4E0D;&#x4E00;&#x81F4;"),),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert "step_1:" + ACTUAL_VALUE_IS_EVALUATION in reasons

    def test_an_entity_is_read_as_the_character(self):
        assert markdown_text("&amp;") == "&"

    def test_a_decoded_entity_is_still_text(self):
        """The entities are decoded after the parse, never before: a
        decoded ``&lt;img&gt;`` is text, not markup."""
        assert markdown_text("&lt;img&gt;") == "<img>"
        assert contains_image("&lt;img&gt;") is False

    def test_the_content_of_a_code_span_keeps_its_entities(self):
        """A code span is literal — its entities are literal too."""
        assert markdown_text("`&amp;`") == "&amp;"


class TestEmphasis:
    """The emphasis markers are delimiters, not characters.

    A lone ``*`` is a multiplication sign or a stray character. Stripping
    it would invent a word that the tester never wrote — and an invented
    word can be an evaluation word ("ok*" would become "ok").
    """

    def test_a_stray_asterisk_stays(self):
        assert markdown_text("ok*") == "ok*"
        assert is_evaluation_phrase("ok*") is False

    def test_a_multiplication_stays(self):
        assert markdown_text("5*3=15") == "5*3=15"
        assert markdown_text("value*2") == "value*2"

    def test_the_double_asterisk_is_stripped(self):
        assert markdown_text("**不一致**") == "不一致"
        assert is_evaluation_phrase("**不一致**") is True

    def test_the_single_underscore_is_stripped(self):
        assert markdown_text("_italic_") == "italic"

    def test_a_dunder_is_read_as_bold(self):
        """The parser reads ``__init__`` as bold — the front end renders
        it the same way. The text it carries is still a text, and still
        no evaluation word."""
        assert markdown_text("__init__") == "init"
        assert is_evaluation_phrase("__init__") is False

    def test_a_word_with_an_underscore_stays(self):
        assert markdown_text("snake_case_name") == "snake_case_name"

    def test_a_paired_asterisk_is_stripped(self):
        assert markdown_text("*italic*") == "italic"

    def test_a_strike_is_stripped(self):
        assert markdown_text("~~gone~~") == "gone"

    def test_the_code_markers_are_stripped(self):
        assert markdown_text("`exit code 1`") == "exit code 1"

    @pytest.mark.parametrize("value", ["-", "--", "...", "待补充", "TBD", "todo"])
    def test_a_placeholder_is_recognised(self, value):
        assert is_placeholder(value) is True

    @pytest.mark.parametrize("value", ["— (the device stayed silent)", "error 42", "no", "-1"])
    def test_an_observation_is_not_a_placeholder(self, value):
        assert is_placeholder(value) is False


class TestAttachments:
    """A file that is attached to a step is a measured value."""

    def test_a_text_with_an_attachment_is_accepted(self):
        reasons = validate_run_case(
            _case(
                steps=(
                    _step(
                        conclusion=STEP_VERDICT_FAIL,
                        actual_value="error 42",
                        attachment_count=1,
                    ),
                ),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert reasons == []

    def test_an_attachment_alone_is_a_measured_value(self):
        """The screenshot of the screen is what the tester saw."""
        reasons = validate_run_case(
            _case(
                steps=(
                    _step(
                        conclusion=STEP_VERDICT_FAIL,
                        actual_value="",
                        attachment_count=1,
                    ),
                ),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert reasons == []
        assert "step_1:" + FAIL_WITHOUT_ACTUAL_VALUE not in reasons

    def test_an_attachment_alone_does_not_hide_an_evaluation_word(self):
        reasons = validate_run_case(
            _case(
                steps=(
                    _step(
                        conclusion=STEP_VERDICT_FAIL,
                        actual_value="不一致",
                        attachment_count=1,
                    ),
                ),
                conclusion=RUN_CASE_CONCLUSION_FAIL,
            )
        )
        assert "step_1:" + ACTUAL_VALUE_IS_EVALUATION in reasons

    def test_an_attachment_without_a_verdict_is_rejected(self):
        reasons = validate_run_case(
            _case(
                steps=(_step(conclusion=STEP_VERDICT_UNSET, attachment_count=1),),
                conclusion=RUN_CASE_CONCLUSION_NA,
                na_reason="the module is not built for Windows",
                na_criteria_source="STD-SW-003 §5",
            )
        )
        assert "step_1:" + ACTUAL_VALUE_WITHOUT_CONCLUSION in reasons

    def test_a_measurement_without_a_verdict_is_rejected(self):
        reasons = validate_run_case(
            _case(
                steps=(_step(conclusion=STEP_VERDICT_UNSET, actual_value="error 42"),),
                conclusion=RUN_CASE_CONCLUSION_NA,
                na_reason="the module is not built for Windows",
                na_criteria_source="STD-SW-003 §5",
            )
        )
        assert "step_1:" + ACTUAL_VALUE_WITHOUT_CONCLUSION in reasons


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

    @pytest.mark.parametrize("value", IMAGE_SHAPES)
    def test_a_pure_image_is_not_evidence(self, value):
        """An image is a picture, not a file the batch can show."""
        assert EVIDENCE_MISSING in validate_run_case(_case(evidence=value))

    def test_a_placeholder_is_not_evidence(self):
        assert EVIDENCE_MISSING in validate_run_case(_case(evidence="待补充"))

    def test_an_attachment_alone_is_evidence(self):
        """The screenshot of the report is the evidence."""
        reasons = validate_run_case(_case(evidence="", evidence_attachment_count=1))
        assert EVIDENCE_MISSING not in reasons

    def test_a_markdown_evidence_is_evidence(self):
        reasons = validate_run_case(_case(evidence="**the report** of the run, see [the log](http://x.test)"))
        assert EVIDENCE_MISSING not in reasons

    def test_the_evidence_is_not_judged_for_evaluation_words(self):
        """Only the measured value of a step is judged for evaluation
        words. The evidence is not — the docstring of the model says so,
        and this test pins it."""
        reasons = validate_run_case(_case(evidence="不一致"))
        assert not [reason for reason in reasons if ACTUAL_VALUE_IS_EVALUATION in reason]


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

    def test_the_executor_may_be_named_by_the_snapshot_only(self):
        """The batch keeps a text snapshot of the executor next to the
        foreign key: a deleted user leaves the foreign key empty, but the
        snapshot keeps the name of the person who executed the batch."""
        reasons = validate_run(_run(executed_by="", executed_by_name="Li Wei"))
        assert RUN_EXECUTED_BY_MISSING not in reasons

    def test_a_batch_without_any_executor_name_is_rejected(self):
        reasons = validate_run(_run(executed_by="", executed_by_name=""))
        assert RUN_EXECUTED_BY_MISSING in reasons

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
            steps=(
                _step(conclusion="nonsense", actual_value="不一致"),
                _step(
                    sequence_id=2,
                    conclusion=STEP_VERDICT_FAIL,
                    actual_value="![](https://files.test/shot.png)",
                ),
            ),
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
