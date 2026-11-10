# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""App-side adapter of the record-validity rules (STD-SW-003 §6).

The rules themselves live in :mod:`plane.db.testcase_validity` — pure
functions over plain dataclasses. This module is the thin layer that
turns a request/response into those dataclasses and turns the reason
codes they return into the wire format the front end consumes:

* :func:`build_run_case_data` / :func:`build_run_data` fill the dataclasses
  from the model rows (including the attachment counts, which come from
  the model properties);
* :func:`describe_reasons` maps a reason list to one entry per problem,
  each with a **field path**, the stable **reason code** and a Chinese
  sentence;
* :func:`check_authoring` runs the *authoring* subset of the rules — the
  mandatory header items, the number format and the step table — for the
  create/update surface, where no verdict and no measured value exist
  yet.

The rules are never re-implemented here: every verdict comes from
:func:`plane.db.testcase_validity.validate_run_case` /
:func:`plane.db.testcase_validity.validate_run`.
"""

# Python imports
from typing import Optional

# Module imports
from plane.db.testcase_numbering import format_case_number, is_valid_module_code
from plane.db.testcase_validity import (
    ACTUAL_VALUE_IS_EVALUATION,
    ACTUAL_VALUE_IS_IMAGE_ONLY,
    ACTUAL_VALUE_WITHOUT_CONCLUSION,
    CASE_NUMBER_FORMAT,
    CASE_NUMBER_MODULE_MISMATCH,
    CONCLUSION_FAIL_WITHOUT_FAILED_STEP,
    CONCLUSION_FAIL_WITH_UNSET_STEP,
    CONCLUSION_INVALID,
    CONCLUSION_IS_FINAL,
    CONCLUSION_MANUAL_WITH_FAILED_STEP,
    CONCLUSION_MANUAL_WITHOUT_CONDITION,
    CONCLUSION_MANUAL_WITHOUT_EXECUTOR,
    CONCLUSION_NA_WITHOUT_CRITERIA_SOURCE,
    CONCLUSION_NA_WITHOUT_REASON,
    CONCLUSION_NA_WITH_FAILED_STEP,
    CONCLUSION_PASS_WITH_FAILED_STEP,
    CONCLUSION_PASS_WITH_UNSET_STEP,
    DUPLICATE_CASE_NUMBER,
    ENVIRONMENT_MISSING,
    EVIDENCE_MISSING,
    EXPECTED_EMPTY,
    ACTION_EMPTY,
    FAIL_WITHOUT_ACTUAL_VALUE,
    MODULE_CODE_FORMAT,
    OBJECT_OVERVIEW_MISSING,
    PRECONDITIONS_MISSING,
    PRIORITY_INVALID,
    PURPOSE_MISSING,
    RUN_BUILD_NUMBER_MISSING,
    RUN_COMMIT_HASH_MISSING,
    RUN_COMPLETED,
    RUN_DATE_MISSING,
    RUN_ENVIRONMENT_MISSING,
    RUN_EXECUTED_BY_MISSING,
    RUN_NUMBER_FORMAT,
    RUN_VERSION_NUMBER_MISSING,
    STEP_CONCLUSION_INVALID,
    STEP_CONCLUSION_UNSET,
    STEP_COUNT_ZERO,
    TITLE_MISSING,
    RunCaseData,
    RunData,
    StepRecordData,
    validate_run_case,
)


#: Chinese sentence per bare reason code. The reason code stays the
#: machine-readable identity of a problem; this sentence is what the
#: front end shows next to the offending field.
REASON_MESSAGES_ZH = {
    CASE_NUMBER_FORMAT: "用例编号必须形如 TC-<模块码>-<三位序号>。",
    MODULE_CODE_FORMAT: "模块码必须是 2 至 6 位大写字母。",
    CASE_NUMBER_MODULE_MISMATCH: "用例编号中的模块码与用例所属模块不一致。",
    TITLE_MISSING: "缺少必填项：用例标题。",
    PURPOSE_MISSING: "缺少必填项：测试目的。",
    OBJECT_OVERVIEW_MISSING: "缺少必填项：测试对象总览。",
    PRECONDITIONS_MISSING: "缺少必填项：前置条件。",
    ENVIRONMENT_MISSING: "缺少必填项：环境与版本。",
    PRIORITY_INVALID: "优先级必须是 P0、P1、P2、P3 之一。",
    STEP_COUNT_ZERO: "步骤表至少要有一行步骤。",
    ACTION_EMPTY: "步骤的操作不能为空。",
    EXPECTED_EMPTY: "步骤的预期结果不能为空。",
    STEP_CONCLUSION_INVALID: "步骤结论只能是 pass、fail 或未填写。",
    STEP_CONCLUSION_UNSET: "步骤结论必须勾选，不能留空。",
    FAIL_WITHOUT_ACTUAL_VALUE: "勾选 ✘ 的步骤必须填写实测值（文字或附件）。",
    ACTUAL_VALUE_IS_EVALUATION: "实测值必须是观察到的原文，不能只写评价词。",
    ACTUAL_VALUE_IS_IMAGE_ONLY: "实测值只有一个内嵌图片，请把文件作为附件挂到该步骤。",
    ACTUAL_VALUE_WITHOUT_CONCLUSION: "填写了实测值却没有勾选步骤结论。",
    CONCLUSION_INVALID: "结论必须是 PASS、FAIL、N/A 或需人工。",
    CONCLUSION_PASS_WITH_FAILED_STEP: "结论为 PASS 时不允许存在 ✘ 的步骤。",
    CONCLUSION_PASS_WITH_UNSET_STEP: "结论为 PASS 时不允许存在未勾选的步骤。",
    CONCLUSION_FAIL_WITHOUT_FAILED_STEP: "结论为 FAIL 时至少要有一个 ✘ 的步骤。",
    CONCLUSION_FAIL_WITH_UNSET_STEP: "结论为 FAIL 时不允许存在未勾选的步骤。",
    CONCLUSION_NA_WITHOUT_REASON: "结论为 N/A 时必须写明理由。",
    CONCLUSION_NA_WITHOUT_CRITERIA_SOURCE: "结论为 N/A 时必须写明判据来源。",
    CONCLUSION_NA_WITH_FAILED_STEP: "结论为 N/A 时不允许存在 ✘ 的步骤。",
    CONCLUSION_MANUAL_WITHOUT_EXECUTOR: "结论为需人工时必须写明由谁执行。",
    CONCLUSION_MANUAL_WITHOUT_CONDITION: "结论为需人工时必须写明执行条件。",
    CONCLUSION_MANUAL_WITH_FAILED_STEP: "结论为需人工时不允许存在 ✘ 的步骤。",
    EVIDENCE_MISSING: "缺少必填项：证据。",
    RUN_NUMBER_FORMAT: "批次编号必须形如 RUN-<YYYYMMDD>-<两位序号>。",
    RUN_DATE_MISSING: "缺少必填项：执行日期。",
    RUN_VERSION_NUMBER_MISSING: "缺少必填项：被测版本号。",
    RUN_BUILD_NUMBER_MISSING: "缺少必填项：被测构建号。",
    RUN_COMMIT_HASH_MISSING: "缺少必填项：被测提交号。",
    RUN_ENVIRONMENT_MISSING: "缺少必填项：运行环境。",
    RUN_EXECUTED_BY_MISSING: "缺少必填项：执行人。",
    DUPLICATE_CASE_NUMBER: "同一个用例在批次中重复出现。",
    RUN_COMPLETED: "批次已完成，结论不再回写。",
    CONCLUSION_IS_FINAL: "结论一经写入不再修改，请作废该记录后在新批次中重测。",
}


#: The field of the record a reason code points at. The step-scoped codes
#: get the step number appended by :func:`describe_reasons`.
REASON_FIELD_PATHS = {
    CASE_NUMBER_FORMAT: "number",
    MODULE_CODE_FORMAT: "module_code",
    CASE_NUMBER_MODULE_MISMATCH: "number",
    TITLE_MISSING: "title",
    PURPOSE_MISSING: "purpose",
    OBJECT_OVERVIEW_MISSING: "object_overview",
    PRECONDITIONS_MISSING: "preconditions",
    ENVIRONMENT_MISSING: "environment",
    PRIORITY_INVALID: "priority",
    STEP_COUNT_ZERO: "steps",
    ACTION_EMPTY: "action",
    EXPECTED_EMPTY: "expected_result",
    STEP_CONCLUSION_INVALID: "conclusion",
    STEP_CONCLUSION_UNSET: "conclusion",
    FAIL_WITHOUT_ACTUAL_VALUE: "actual_value",
    ACTUAL_VALUE_IS_EVALUATION: "actual_value",
    ACTUAL_VALUE_IS_IMAGE_ONLY: "actual_value",
    ACTUAL_VALUE_WITHOUT_CONCLUSION: "conclusion",
    CONCLUSION_INVALID: "conclusion",
    CONCLUSION_PASS_WITH_FAILED_STEP: "conclusion",
    CONCLUSION_PASS_WITH_UNSET_STEP: "conclusion",
    CONCLUSION_FAIL_WITHOUT_FAILED_STEP: "conclusion",
    CONCLUSION_FAIL_WITH_UNSET_STEP: "conclusion",
    CONCLUSION_NA_WITHOUT_REASON: "na_reason",
    CONCLUSION_NA_WITHOUT_CRITERIA_SOURCE: "na_criteria_source",
    CONCLUSION_NA_WITH_FAILED_STEP: "conclusion",
    CONCLUSION_MANUAL_WITHOUT_EXECUTOR: "manual_executor",
    CONCLUSION_MANUAL_WITHOUT_CONDITION: "manual_condition",
    CONCLUSION_MANUAL_WITH_FAILED_STEP: "conclusion",
    EVIDENCE_MISSING: "evidence",
    RUN_NUMBER_FORMAT: "number",
    RUN_DATE_MISSING: "run_date",
    RUN_VERSION_NUMBER_MISSING: "version_number",
    RUN_BUILD_NUMBER_MISSING: "build_number",
    RUN_COMMIT_HASH_MISSING: "commit_hash",
    RUN_ENVIRONMENT_MISSING: "environment",
    RUN_EXECUTED_BY_MISSING: "executed_by",
    DUPLICATE_CASE_NUMBER: "test_case",
    RUN_COMPLETED: "status",
    CONCLUSION_IS_FINAL: "conclusion",
}


#: The reasons that belong to the *execution* surface: the step verdict,
#: the measured value, the case conclusion and the evidence. The
#: authoring surface (create / update a case) has no verdict and no
#: measured value yet, so these reasons are dropped there — the rules
#: themselves still run, only the codes that cannot apply are ignored.
EXECUTION_ONLY_REASONS = frozenset(
    {
        CONCLUSION_INVALID,
        CONCLUSION_PASS_WITH_FAILED_STEP,
        CONCLUSION_PASS_WITH_UNSET_STEP,
        CONCLUSION_FAIL_WITHOUT_FAILED_STEP,
        CONCLUSION_FAIL_WITH_UNSET_STEP,
        CONCLUSION_NA_WITHOUT_REASON,
        CONCLUSION_NA_WITHOUT_CRITERIA_SOURCE,
        CONCLUSION_NA_WITH_FAILED_STEP,
        CONCLUSION_MANUAL_WITHOUT_EXECUTOR,
        CONCLUSION_MANUAL_WITHOUT_CONDITION,
        CONCLUSION_MANUAL_WITH_FAILED_STEP,
        EVIDENCE_MISSING,
        STEP_CONCLUSION_INVALID,
        STEP_CONCLUSION_UNSET,
        FAIL_WITHOUT_ACTUAL_VALUE,
        ACTUAL_VALUE_IS_EVALUATION,
        ACTUAL_VALUE_IS_IMAGE_ONLY,
        ACTUAL_VALUE_WITHOUT_CONCLUSION,
    }
)


def split_reason(reason: str) -> tuple[Optional[str], Optional[int], str]:
    """Split a reason string into ``(case_number, step_sequence, code)``.

    The reason codes carry their location in two decorations
    (``step_<n>:<code>`` and ``<case number>|<reason>``); the codes
    themselves never contain ``:`` or ``|``, so the split is
    unambiguous.
    """
    case_number: Optional[str] = None
    step_sequence: Optional[int] = None
    rest = reason

    if "|" in rest:
        case_number, rest = rest.split("|", 1)
        if case_number == "?":
            case_number = None

    if rest.startswith("step_") and ":" in rest:
        head, rest = rest.split(":", 1)
        try:
            step_sequence = int(head[len("step_") :])
        except ValueError:
            step_sequence = None

    return case_number, step_sequence, rest


def describe_reasons(reasons: list[str]) -> list[dict]:
    """Turn a reason list into one entry per problem.

    Each entry carries the field path of the problem (so the front end
    can put the message next to the offending input), the stable reason
    code and the Chinese sentence.
    """
    described = []
    for reason in reasons:
        case_number, step_sequence, code = split_reason(reason)
        field = REASON_FIELD_PATHS.get(code, "")
        parts = []
        if case_number:
            parts.append(f"cases[{case_number}]")
        if step_sequence is not None:
            parts.append(f"steps[{step_sequence}]")
        if field:
            parts.append(field)
        described.append(
            {
                "code": code,
                "message": REASON_MESSAGES_ZH.get(code, reason),
                "path": ".".join(parts),
                "field": field,
                "case_number": case_number,
                "step_sequence": step_sequence,
            }
        )
    return described


def attachment_count(record) -> int:
    """The number of live files attached to a record.

    The model property is the source of truth, but it is one query per
    record. When the caller prefetched the ``assets`` relation the
    prefetched rows are counted instead, so a list endpoint does not run
    one query per row.
    """
    cache = getattr(record, "_prefetched_objects_cache", None)
    if cache is not None and "assets" in cache:
        return sum(1 for asset in cache["assets"] if not asset.is_deleted)
    return record.attachment_count


def evidence_attachment_count(record) -> int:
    """The number of live files attached to a case record (its evidence)."""
    cache = getattr(record, "_prefetched_objects_cache", None)
    if cache is not None and "assets" in cache:
        return sum(1 for asset in cache["assets"] if not asset.is_deleted)
    return record.evidence_attachment_count


def build_step_record_data(record) -> StepRecordData:
    """Fill the dataclass of one step record of a batch case."""
    return StepRecordData(
        sequence_id=record.sequence_id,
        action=record.action_snapshot,
        expected_result=record.expected_result_snapshot,
        conclusion=record.conclusion,
        actual_value=record.actual_value,
        attachment_count=attachment_count(record),
    )


def build_run_case_data(run_case) -> RunCaseData:
    """Fill the dataclass of one case inside a batch.

    The header is the snapshot the batch carries, not the current state
    of the case — that is the whole point of the snapshot.
    """
    cache = getattr(run_case, "_prefetched_objects_cache", None)
    if cache is not None and "step_records" in cache:
        records = cache["step_records"]
    else:
        records = run_case.step_records.all()
    return RunCaseData(
        case_number=run_case.case_number,
        module_code=run_case.case_module_code,
        title=run_case.case_title,
        purpose=run_case.case_purpose,
        object_overview=run_case.case_object_overview,
        priority=run_case.case_priority,
        preconditions=run_case.case_preconditions,
        environment=run_case.case_environment,
        steps=tuple(build_step_record_data(record) for record in records),
        conclusion=run_case.conclusion,
        evidence=run_case.evidence,
        na_reason=run_case.na_reason,
        na_criteria_source=run_case.na_criteria_source,
        manual_executor=run_case.manual_executor,
        manual_condition=run_case.manual_condition,
        remarks=run_case.remarks,
        actual_result_summary=run_case.actual_result_summary,
        evidence_attachment_count=evidence_attachment_count(run_case),
    )


def executor_name(run) -> str:
    """The name of the executor as the live user row spells it."""
    executor = run.executed_by
    if executor is None:
        return ""
    return (executor.display_name or executor.full_name or executor.email or "").strip()


def build_run_data(run, run_cases) -> RunData:
    """Fill the dataclass of one batch and the cases it holds."""
    return RunData(
        number=run.number,
        run_date=run.run_date,
        version_number=run.version_number,
        build_number=run.build_number,
        commit_hash=run.commit_hash,
        environment=run.environment,
        executed_by=executor_name(run),
        status=run.status,
        cases=tuple(build_run_case_data(run_case) for run_case in run_cases),
        executed_by_name=run.executed_by_name,
    )


def build_case_data(
    module_code: str,
    header: dict,
    steps: list[dict],
    number: Optional[str] = None,
) -> RunCaseData:
    """Fill the dataclass of a case that is being authored.

    ``number`` is the number the case carries. When the case is not
    written yet the number is assigned by the server on save, so the
    canonical form of the module code is used for the format check —
    the number format is a property of the module code.
    """
    if number is None:
        number = format_case_number(module_code, 1) if is_valid_module_code(module_code) else ""

    return RunCaseData(
        case_number=number,
        module_code=module_code,
        title=header.get("title") or "",
        purpose=header.get("purpose") or "",
        object_overview=header.get("object_overview") or "",
        priority=header.get("priority") or "",
        preconditions=header.get("preconditions") or "",
        environment=header.get("environment") or "",
        steps=tuple(
            StepRecordData(
                sequence_id=index,
                # Only a mapping carries a step; anything else is read as
                # an empty action, so a malformed step lands on
                # ``action_empty`` instead of raising.
                action=(step.get("action") or "") if isinstance(step, dict) else "",
                expected_result=(
                    (step.get("expected_result") or "") if isinstance(step, dict) else ""
                ),
            )
            for index, step in enumerate(steps, start=1)
        ),
    )


def check_authoring(
    module_code: str,
    header: dict,
    steps: list[dict],
    number: Optional[str] = None,
) -> list[str]:
    """The reasons why a case may not be written (created or updated).

    Only the authoring subset of the rules applies here: the number
    format, the module code, the mandatory header items and the step
    table. The verdict, the measured value and the evidence belong to
    the execution surface and are not judged while the case is written.
    """
    reasons = validate_run_case(build_case_data(module_code, header, steps, number=number))
    return [reason for reason in reasons if split_reason(reason)[2] not in EXECUTION_ONLY_REASONS]


#: Every reason code the rules can produce. A model validation error
#: carries its code as the prefix of the message (``"<code>: <text>"``);
#: the codes below are what makes such a message machine-readable.
KNOWN_REASON_CODES = frozenset(REASON_MESSAGES_ZH)


def reason_code_of(message: str) -> Optional[str]:
    """The reason code a model validation message carries, if any.

    The model layer prefixes its messages with the stable code
    (``"conclusion_is_final: the conclusion of a case …"``). A message
    without such a prefix carries no code.
    """
    if not isinstance(message, str) or ":" not in message:
        return None
    prefix = message.split(":", 1)[0].strip()
    return prefix if prefix in KNOWN_REASON_CODES else None
