# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The batch summary (STD-SW-003 §6.2 rule 3, §6.3).

「批次汇总四项：用例总数、`PASS` 数、`FAIL` 数、`N/A` 与 `需人工` 数；另附不符点
清单，逐条列用例编号与一句话现象。」

:func:`plane.db.testcase_summary.summarize_run` is tested against the
standard's own filled record, ``REC-SW-001-filled-sample.md``: one batch
``RUN-20261109-02`` holding one case that ended ``FAIL`` on step 4, with
step 7 never executed. The sample's summary table prints ``PASS 0 | FAIL 1 |
N/A 0 | 需人工 1 | 用例总数 1`` — the four numbers add up to two, on purpose,
and the counts here reproduce it.

The deviation line of the sample (「`TC-SET-001` 步骤 4：点「每日摘要」后未出现
说明文字；步骤 7 未执行」) is the tester's own one-sentence summary; the
record itself carries the step-4 measured value. The function returns the
sentence when it is filled (§4.1 field 13) and the measured value of the
first ✘ otherwise (§6.1 rule 2) — both are tested.
"""

from datetime import date
from dataclasses import replace

import pytest

from plane.db.testcase_summary import SUMMARY_KEYS, summarize_run
from plane.db.testcase_validity import (
    RUN_CASE_CONCLUSION_FAIL,
    RUN_CASE_CONCLUSION_MANUAL,
    RUN_CASE_CONCLUSION_NA,
    RUN_CASE_CONCLUSION_PASS,
    STEP_VERDICT_FAIL,
    STEP_VERDICT_PASS,
    STEP_VERDICT_UNSET,
    RunCaseData,
    RunData,
    StepRecordData,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# REC-SW-001-filled-sample.md, batch RUN-20261109-02
# --------------------------------------------------------------------------- #

SAMPLE_STEPS = (
    StepRecordData(
        sequence_id=1,
        action="看右栏第二行「允许通知」，其右侧的开关。",
        expected_result=(
            "关闭态：圆点在左端、底色为灰。为开启态或第三种样式 → 勾 ✘，"
            "把看到的样式与截图路径写进实测值。"
        ),
        conclusion=STEP_VERDICT_PASS,
    ),
    StepRecordData(
        sequence_id=2,
        action="看下一行「每日摘要」，其右侧的开关。",
        expected_result="不可操作：颜色明显比步骤 1 的开关浅，点击无反应。同样深、可点击 → 勾 ✘。",
        conclusion=STEP_VERDICT_PASS,
    ),
    StepRecordData(
        sequence_id=3,
        action="点「允许通知」右侧的开关一次。",
        expected_result=(
            "变为开启态：圆点移到右端，底色不再是灰色；「每日摘要」的开关同时变深。"
            "任一未变 → 勾 ✘。"
        ),
        conclusion=STEP_VERDICT_PASS,
    ),
    StepRecordData(
        sequence_id=4,
        action="点「每日摘要」右侧的开关一次。",
        expected_result=(
            "变为开启态；该行下方出现说明文字「每天 09:00 汇总一次」，逐字一致。"
            "文字不出现或与预期不同 → 勾 ✘，把实际文字（或「无文字」）抄进实测值。"
        ),
        conclusion=STEP_VERDICT_FAIL,
        actual_value="开关已变为开启态；该行下方无任何说明文字",
    ),
    StepRecordData(
        sequence_id=5,
        action=(
            "点设置窗口左上角红色关闭按钮，再按前置条件第 3 条重新打开并点「通知」。"
        ),
        expected_result=(
            "两个开关仍为开启态。任一被重置 → 勾 ✘。说明文字由步骤 4 单独判，"
            "本步不重复判。"
        ),
        conclusion=STEP_VERDICT_PASS,
    ),
    StepRecordData(
        sequence_id=6,
        action=(
            "菜单 应用 →「退出应用」（Command 加 Q）退出，重新启动后按前置条件"
            "第 3 条进入「通知」。"
        ),
        expected_result="两个开关仍为开启态。任一被重置 → 勾 ✘。",
        conclusion=STEP_VERDICT_PASS,
    ),
    StepRecordData(
        sequence_id=7,
        action=(
            "（需人工，由开发准备环境）开发在系统「系统设置 → 通知」里关闭本应用的"
            "通知权限后，执行人重做步骤 1、3。"
        ),
        expected_result=(
            "右栏顶部出现提示「系统通知权限已关闭」；「允许通知」开关不可操作。"
            "提示不出现或仍可点 → 勾 ✘。未准备环境时两个框都不勾，在实测值写"
            "「未准备环境」。"
        ),
        conclusion=STEP_VERDICT_UNSET,
        actual_value="未准备系统通知权限，本步未执行",
    ),
)

SAMPLE_CASE = RunCaseData(
    case_number="TC-SET-001",
    module_code="SET",
    title="通知区总开关关闭时子开关不可操作，打开后恢复",
    purpose=(
        "验证「通知」区的总开关与「每日摘要」子开关的联动：总开关关闭时子开关应为"
        "不可操作，打开总开关后子开关恢复可用，且重启应用后两个状态都被记住。"
    ),
    object_overview=(
        "被测应用是一个桌面笔记工具。主窗口分三块：左侧笔记列表、中间正文区、"
        "底部状态栏。设置是独立窗口，左栏四个入口自上而下为：账号、通知、隐私、"
        "关于；点左栏某项，右栏显示对应内容。本条只看「通知」对应的右栏内容。"
    ),
    priority="P1",
    preconditions=(
        "1. 应用已安装且能启动，版本 2.3.1（构建号 2310）。\n"
        "2. 首次启动状态：未登录任何账号，设置项均为出厂默认。"
    ),
    environment="macOS 15.2 / MacBook Pro 14 英寸（M4）；应用 2.3.1，构建号 2310，提交 `8d5cf7e`",
    steps=SAMPLE_STEPS,
    conclusion=RUN_CASE_CONCLUSION_FAIL,
    evidence=(
        "`docs/evidence/2026-11-09/TC-SET-001/`：`notify-on.png`、"
        "`step4-after-toggle.png`、`notify-restart.png`、`console.txt`"
    ),
    remarks=(
        "步骤 4 勾了 ✘：开关已开启但说明文字未出现。需求条目「通知联动规则 v2」写明"
        "该说明文字应存在，按 `STD-SW-003` §8.2 判为产品缺陷。步骤 7 需开发准备环境，"
        "两个框都未勾，按 `需人工` 记。"
    ),
)

SAMPLE_RUN = RunData(
    number="RUN-20261109-02",
    run_date=date(2026, 11, 9),
    version_number="2.3.1",
    build_number="2310",
    commit_hash="8d5cf7e",
    environment="macOS 15.2 / MacBook Pro 14 英寸（M4）；全新安装，未登录",
    executed_by="张三",
    status="completed",
    cases=(SAMPLE_CASE,),
)


class TestTheSampleRecord:
    def test_the_counts_are_the_four_items_of_the_sample_table(self):
        """REC-SW-001 §五: ``PASS 0 | FAIL 1 | N/A 0 | 需人工 1 | 用例总数 1``."""
        summary = summarize_run(SAMPLE_RUN)
        assert summary["total"] == 1
        assert summary["pass"] == 0
        assert summary["fail"] == 1
        assert summary["na"] == 0
        assert summary["manual"] == 1

    def test_the_counts_are_not_a_partition_of_the_total(self):
        """The sample's four numbers add up to two, and that is what the
        standard prints: one case, failed on step 4, step 7 not executed."""
        summary = summarize_run(SAMPLE_RUN)
        assert summary["pass"] + summary["fail"] + summary["na"] + summary["manual"] == 2
        assert summary["total"] == 1

    def test_the_sample_manual_count_comes_from_its_unexecuted_step(self):
        """No case in the sample is concluded 需人工; the case is FAIL and
        its step 7 is the 需人工 one (§6.1 rule 1)."""
        assert SAMPLE_CASE.conclusion == RUN_CASE_CONCLUSION_FAIL
        assert any(step.conclusion == STEP_VERDICT_UNSET for step in SAMPLE_CASE.steps)

    def test_the_deviation_is_the_step_4_measured_value(self):
        """§6.1 rule 2 wants the observed original, and that is what the
        record carries; the sample's summary cell paraphrases it."""
        summary = summarize_run(SAMPLE_RUN)
        assert summary["deviations"] == [
            {
                "case_number": "TC-SET-001",
                "summary": "开关已变为开启态；该行下方无任何说明文字",
            }
        ]

    def test_the_filled_summary_wins_over_the_measured_value(self):
        """§4.1 field 13: with the one-sentence summary filled, that is the
        sentence the summary cell of the sample prints."""
        sentence = "步骤 4：点「每日摘要」后未出现说明文字；步骤 7 未执行"
        run = replace(SAMPLE_RUN, cases=(replace(SAMPLE_CASE, actual_result_summary=sentence),))
        assert summarize_run(run)["deviations"] == [
            {"case_number": "TC-SET-001", "summary": sentence}
        ]


# --------------------------------------------------------------------------- #
# The rules of the counts
# --------------------------------------------------------------------------- #


def _step(sequence_id=1, conclusion=STEP_VERDICT_PASS, actual_value=""):
    return StepRecordData(
        sequence_id=sequence_id,
        conclusion=conclusion,
        actual_value=actual_value,
        action="点一下开关",
        expected_result="圆点在左端",
    )


def _case(number="TC-SET-001", conclusion=RUN_CASE_CONCLUSION_PASS, **overrides):
    base = {
        "case_number": number,
        "module_code": "SET",
        "title": "总开关关闭时子开关不可操作",
        "purpose": "验证联动",
        "object_overview": "设置窗口「通知」区",
        "priority": "P1",
        "preconditions": "应用已启动",
        "environment": "macOS 15.2",
        "steps": (_step(),),
        "conclusion": conclusion,
        "evidence": "shot.png",
    }
    base.update(overrides)
    return RunCaseData(**base)


def _run(*cases):
    return RunData(
        number="RUN-20261110-01",
        run_date=date(2026, 11, 10),
        version_number="2.3.1",
        build_number="2310",
        commit_hash="8d5cf7e",
        environment="macOS 15.2",
        executed_by="张三",
        status="completed",
        cases=tuple(cases),
    )


class TestCounts:
    def test_an_empty_batch_counts_nothing(self):
        summary = summarize_run(_run())
        assert summary == {
            "total": 0,
            "pass": 0,
            "fail": 0,
            "na": 0,
            "manual": 0,
            "deviations": [],
        }

    def test_the_summary_carries_exactly_the_documented_keys(self):
        assert set(summarize_run(SAMPLE_RUN)) == set(SUMMARY_KEYS)

    def test_every_conclusion_is_counted_under_its_own_key(self):
        summary = summarize_run(
            _run(
                _case("TC-SET-001", RUN_CASE_CONCLUSION_PASS),
                _case(
                    "TC-SET-002",
                    RUN_CASE_CONCLUSION_FAIL,
                    steps=(_step(conclusion=STEP_VERDICT_FAIL, actual_value="底色为蓝"),),
                ),
                _case(
                    "TC-SET-003",
                    RUN_CASE_CONCLUSION_NA,
                    steps=(_step(conclusion=STEP_VERDICT_UNSET),),
                    na_reason="本版本未启用该特性",
                    na_criteria_source="版本说明 2.3.1 §4",
                ),
                _case(
                    "TC-SET-004",
                    RUN_CASE_CONCLUSION_MANUAL,
                    steps=(_step(conclusion=STEP_VERDICT_UNSET),),
                    manual_executor="开发",
                    manual_condition="系统通知权限关闭后",
                ),
            )
        )
        assert summary["total"] == 4
        assert summary["pass"] == 1
        assert summary["fail"] == 1
        assert summary["na"] == 1
        # TC-SET-004 is concluded 需人工; TC-SET-003 is N/A and its blank
        # boxes are the conclusion, not a missing execution.
        assert summary["manual"] == 1
        assert [entry["case_number"] for entry in summary["deviations"]] == ["TC-SET-002"]

    def test_a_batch_still_being_written_counts_its_unexecuted_cases(self):
        """§6.1 rule 1: two blank boxes are "not executed", i.e. 需人工."""
        summary = summarize_run(
            _run(
                _case("TC-SET-001", "", steps=(_step(conclusion=STEP_VERDICT_UNSET),)),
                _case("TC-SET-002", "", steps=(_step(conclusion=STEP_VERDICT_UNSET),)),
            )
        )
        assert summary["total"] == 2
        assert summary["manual"] == 2
        assert summary["pass"] + summary["fail"] + summary["na"] == 0


class TestDeviations:
    def test_a_failed_case_without_a_failed_step_still_deviates(self):
        """A wrong record: the collision is reported, not hidden."""
        summary = summarize_run(
            _run(
                _case(
                    "TC-SET-001",
                    RUN_CASE_CONCLUSION_FAIL,
                    actual_result_summary="步骤 3 的提示语与预期不同",
                )
            )
        )
        assert summary["deviations"] == [
            {"case_number": "TC-SET-001", "summary": "步骤 3 的提示语与预期不同"}
        ]

    def test_a_failed_step_deviates_even_when_the_case_has_no_conclusion(self):
        summary = summarize_run(
            _run(
                _case(
                    "TC-SET-001",
                    "",
                    steps=(_step(conclusion=STEP_VERDICT_FAIL, actual_value="提示语为「已保存」"),),
                )
            )
        )
        assert summary["deviations"] == [
            {"case_number": "TC-SET-001", "summary": "提示语为「已保存」"}
        ]

    def test_the_first_failed_step_names_the_deviation(self):
        summary = summarize_run(
            _run(
                _case(
                    "TC-SET-001",
                    RUN_CASE_CONCLUSION_FAIL,
                    steps=(
                        _step(sequence_id=1),
                        _step(sequence_id=2, conclusion=STEP_VERDICT_FAIL, actual_value="第一处原样输出"),
                        _step(sequence_id=3, conclusion=STEP_VERDICT_FAIL, actual_value="第二处原样输出"),
                    ),
                )
            )
        )
        assert summary["deviations"][0]["summary"] == "第一处原样输出"

    def test_the_markup_of_the_summary_is_stripped(self):
        summary = summarize_run(
            _run(
                _case(
                    "TC-SET-001",
                    RUN_CASE_CONCLUSION_FAIL,
                    actual_result_summary="**步骤 2** 的提示语缺失",
                )
            )
        )
        assert summary["deviations"][0]["summary"] == "步骤 2 的提示语缺失"

    def test_a_passing_batch_has_no_deviation(self):
        summary = summarize_run(_run(_case("TC-SET-001", RUN_CASE_CONCLUSION_PASS)))
        assert summary["deviations"] == []

    def test_a_blank_case_number_survives_as_a_blank(self):
        summary = summarize_run(
            _run(
                _case(
                    "  ",
                    RUN_CASE_CONCLUSION_FAIL,
                    actual_result_summary="提示语缺失",
                )
            )
        )
        assert summary["deviations"] == [{"case_number": "", "summary": "提示语缺失"}]


class TestPurity:
    def test_the_summary_module_imports_no_django(self):
        """The function runs on a plain record; a Django import would tie
        it to the ORM."""
        import ast
        import pathlib

        from plane.db import testcase_summary

        tree = ast.parse(pathlib.Path(testcase_summary.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        assert not [name for name in imported if name.split(".")[0] == "django"], imported
