# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The shared HTTP vocabulary of the test-case feature (STD-SW-003).

The feature has two HTTP surfaces — the app API under ``/api/`` and the
public v1 API under ``/api/v1/`` — and both answer their failures in the
same shape: the ``{"error", "code", "errors": [{code, message, path,
field}]}`` envelope of a 400/409, and the DRF ``{"detail"}`` of a 403.
The helpers that build those bodies therefore live here, in a module
neither surface owns, instead of in one surface's view module.

That placement is what keeps the two surfaces independent at import
time: the v1 views import this module and the app *serializers*, never
an app *view* module. A refactor of the app view layer that moves or
renames one of these helpers would otherwise raise ``ImportError`` while
Django imports the URLconf of the v1 surface — and the api container
would not start at all, not just these endpoints.

Nothing in this module may import ``plane.app.permissions``, the app's
authentication/session layer or a view module of either surface. The
rule vocabulary (:mod:`plane.app.testcase_validity`) is imported: it is
a pure module over the dataclasses of :mod:`plane.db.testcase_validity`
and carries the Chinese sentences the entries of ``errors`` quote.
"""

# Python imports
import re

# Django imports
from django.utils.dateparse import parse_datetime

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.testcase_validity import (
    REASON_MESSAGES_ZH,
    check_authoring,
    reason_code_of,
    split_reason,
)
from plane.db.models import (
    FileAsset,
    Project,
    TestRunCase,
    TestRunStepRecord,
)
from plane.db.testcase_validity import (
    RUN_CASE_CONCLUSION_FAIL,
    RUN_CASE_CONCLUSION_MANUAL,
    RUN_CASE_CONCLUSION_NA,
    STEP_VERDICT_UNSET,
    RunData,
    is_filled_markdown,
)


#: Reason codes the app layer adds on top of the vocabulary of
#: :mod:`plane.db.testcase_validity`. They are endpoint-level problems
#: the rules do not know about.
APP_REASON_MESSAGES_ZH = {
    "conflict": "这条记录在你编辑期间被改过了，请重新载入后再保存。",
    "module_code_taken": "该模块码已在本项目登记。",
    "module_code_format": "模块码必须是 2 至 6 位大写字母。",
    "module_description_missing": "模块说明不能为空。",
    "module_not_in_project": "该模块不属于本项目。",
    "issue_not_in_project": "该工作项不属于本项目。",
    "issue_link_exists": "该工作项已经关联到这个用例。",
    "case_not_in_project": "该用例不属于本项目。",
    "invalid_updated_at": "updated_at 不是合法的时间戳。",
    "duplicate_test_case": "同一个用例不能重复加入批次。",
    "test_case_ids_missing": "请至少选择一个用例。",
    "asset_not_found": "附件不存在。",
    "asset_already_bound": "该附件已经绑定到其它记录。",
    "asset_not_bindable": "该附件是其它用途的文件（封面、标识等），不能绑定到测试记录上。",
    "asset_project_mismatch": "该附件属于其它项目，不能绑定到本项目的记录上。",
    "run_version_fixed": "一个批次只对应一个被测版本，补测请新建批次。",
    "run_date_fixed": "批次的执行日期在创建后不能修改。",
    # --- the gates of a batch (M111) ------------------------------------
    "run_step_conclusion_unset": (
        "该步骤既没有勾选结论，也没有写明未执行的原因；批次不能结束。"
        "请勾选通过/不通过，或按需人工在实测值里写明为什么没执行（§6.1）。"
    ),
    "run_manual_without_basis": (
        "结论为需人工时必须写明依据（na_reason 或 na_criteria_source）："
        "为什么这条用例不能由机器判定。执行人和执行条件回答的是谁做、何时做，"
        "不能代替依据。"
    ),
    "run_evidence_copied": (
        "这段证据与另一个批次里同一条用例的证据一字不差；"
        "重测请走重测通道（retest_of），证据要重新记录。"
    ),
    "run_foreign_batch_number": (
        "证据里写了别的批次号；批次号由服务端给出，请删掉手写的那一处。"
    ),
}


#: The reason code of a step that was never touched: no verdict and
#: nothing written about it. The standard's own 需人工 shape — a step that
#: was not executed but whose reason is written in the measured value
#: (§6.1 rule 1) — is a different thing and stays allowed.
RUN_STEP_CONCLUSION_UNSET = "run_step_conclusion_unset"

#: The reason code of a 需人工 conclusion that carries no 依据.
RUN_MANUAL_WITHOUT_BASIS = "run_manual_without_basis"

#: The reason code of an evidence that is a verbatim copy of the same
#: case's evidence in another batch — the twin a re-test is supposed to
#: replace.
RUN_EVIDENCE_COPIED = "run_evidence_copied"

#: The reason code of an evidence that names a batch number which is not
#: this batch's own.
RUN_FOREIGN_BATCH_NUMBER = "run_foreign_batch_number"


#: The field of the record each gate reason points at. The step-scoped
#: code gets the step number appended, exactly like the rules of the
#: db layer do it.
GATE_REASON_FIELD_PATHS = {
    RUN_STEP_CONCLUSION_UNSET: "conclusion",
    RUN_MANUAL_WITHOUT_BASIS: "na_reason",
}


def is_uuid(value) -> bool:
    """True when the value looks like a UUID."""
    from uuid import UUID

    try:
        UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def same_date(value, other) -> bool:
    """True when the client sent the day the batch carries.

    The client may send the day as ``2026-11-09`` or as a full
    timestamp; the batch stores the day.
    """
    from datetime import date

    try:
        sent = date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return False
    return sent == other


def object_body(request):
    """The body of the request as a mapping — or the refusal response.

    The writes of this feature read the body directly
    (``request.data.get(...)``). A body that is not a JSON object — a
    list, a string, a number — has no ``get``; the read would raise an
    ``AttributeError`` and the endpoint would answer 500 where the
    contract promises a 400. Such a body is a refused payload, exactly
    as it is on the endpoints that go through a serializer.

    Returns the body (a ``dict``) or a :class:`Response` carrying the
    refusal; the caller answers the response when it gets one.
    """
    data = request.data
    if isinstance(data, dict):
        return data
    return Response(
        {
            "error": "The payload is not valid.",
            "code": "invalid_payload",
            "errors": [
                error_entry(
                    "invalid_payload",
                    "",
                    "The body of the request must be a JSON object.",
                )
            ],
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


def error_entry(code: str, path: str = "", message: str = "") -> dict:
    """One entry of the ``errors`` list of a 400 body.

    The sentence comes from the vocabulary of the rules
    (:data:`REASON_MESSAGES_ZH`) or, for the codes the app layer adds on
    top of it, from :data:`APP_REASON_MESSAGES_ZH`. Both are consulted:
    an entry without a sentence would leave the front end with nothing
    to show next to the offending field.
    """
    return {
        "code": code,
        "message": message
        or REASON_MESSAGES_ZH.get(code)
        or APP_REASON_MESSAGES_ZH.get(code, ""),
        "path": path,
        "field": path,
    }


def serializer_error_response(serializer, error: str = "The payload is not valid.") -> Response:
    """Wrap the refusal of a serializer in the envelope of this feature.

    A field the serializer refuses (an unknown priority, a date that is
    not a date) is answered in the same shape as every other error of
    the feature, so the front end and the CLI never have to parse a
    second shape. The message of such an entry is the **detail of the
    serializer** — it is not a localised sentence; the code and the
    field are what a client keys on.
    """
    errors = []
    raw_errors = serializer.errors
    if isinstance(raw_errors, dict):
        items = raw_errors.items()
    else:
        items = [("", raw_errors)]

    for field, messages in items:
        if not isinstance(messages, (list, tuple)):
            messages = [messages]
        for message in messages:
            errors.append(
                {
                    "code": "invalid_payload",
                    "message": str(message),
                    "path": field,
                    "field": field,
                }
            )

    return Response(
        {"error": error, "code": "invalid_payload", "errors": errors},
        status=status.HTTP_400_BAD_REQUEST,
    )


def validation_error_response(exc, error: str = "The payload is not valid.") -> Response:
    """Map a Django ``ValidationError`` onto the 400 body of this feature.

    The model layer prefixes its messages with the stable reason code
    (``"conclusion_is_final: …"``); the code is what the front end keys
    on, the Chinese sentence is what it shows.
    """
    errors = []
    message_dict = getattr(exc, "message_dict", None)
    if message_dict:
        for field, messages in message_dict.items():
            for message in messages:
                code = reason_code_of(message) or ""
                errors.append(
                    {
                        "code": code,
                        "message": REASON_MESSAGES_ZH.get(code, message),
                        "detail": message,
                        "path": field,
                        "field": field,
                    }
                )
    else:
        for message in getattr(exc, "messages", [str(exc)]):
            code = reason_code_of(message) or ""
            errors.append(
                {
                    "code": code,
                    "message": REASON_MESSAGES_ZH.get(code, message),
                    "detail": message,
                    "path": "",
                    "field": "",
                }
            )
    return Response(
        {"error": error, "code": "invalid_payload", "errors": errors},
        status=status.HTTP_400_BAD_REQUEST,
    )


def conflict_response(current: dict) -> Response:
    """The 409 body of an optimistic-concurrency conflict."""
    return Response(
        {
            "error": "The record was changed by somebody else. Reload it and try again.",
            "code": "conflict",
            "errors": [error_entry("conflict")],
            "current": current,
        },
        status=status.HTTP_409_CONFLICT,
    )


def stale_record_response(request, record, current: dict):
    """The 409 response when the client's ``updated_at`` no longer matches.

    ``None`` when the client sent no ``updated_at`` (the write is then a
    plain last-write-wins) or when the value still matches.
    """
    body = object_body(request)
    if isinstance(body, Response):
        return body

    client_value = body.get("updated_at")
    if not client_value:
        return None
    parsed = parse_datetime(str(client_value))
    if parsed is None:
        return Response(
            {
                "error": "updated_at is not a valid timestamp.",
                "code": "invalid_updated_at",
                "errors": [error_entry("invalid_updated_at", "updated_at")],
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    if parsed != record.updated_at:
        return conflict_response(current)
    return None


def project_or_404(slug, project_id):
    """The project of the URL, or ``None``."""
    return Project.objects.filter(pk=project_id, workspace__slug=slug).first()


def run_conflict_response(message: str, code: str = "run_completed") -> Response:
    """The 409 body of a write that a completed batch refuses."""
    return Response(
        {
            "error": message,
            "code": code,
            "errors": [error_entry(code)],
        },
        status=status.HTTP_409_CONFLICT,
    )


#: The foreign keys of ``FileAsset`` that name the entity a file hangs
#: on. A file that already carries one of them may not get a second
#: owner: the row would then belong to two entities at once, and the
#: unbind of one of them would take the file away from the other.
_ASSET_ENTITY_FIELDS = (
    "issue_id",
    "page_id",
    "comment_id",
    "draft_issue_id",
    "user_id",
    "test_run_case_id",
    "test_run_step_record_id",
)

#: The entity types a file may carry when it is bound to a record of a
#: batch. A file that belongs to a whole entity as such — the cover of a
#: project, the logo of a workspace, an avatar — is not a file of a
#: record: binding it would take it away from that entity, and the
#: unbind would flag the row deleted. Only a file without an entity (or
#: one that was already meant for a batch) may be attached to a record.
_BINDABLE_ENTITY_TYPES = frozenset(
    {
        "",
        None,
        FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD,
        FileAsset.EntityTypeContext.TEST_RUN_CASE_EVIDENCE,
    }
)


def bindable_asset(request, slug, record):
    """The asset the request wants to bind to ``record``, or the error.

    The asset has to belong to the **project of the record**: a file of
    another project may not be moved into this one — the bind would
    silently re-home it, and the unbind would then soft-delete a row of a
    project the caller has no rights in. A file that already hangs on
    another entity may not get a second owner either, and a file that
    belongs to a whole entity (the cover of a project, the logo of a
    workspace) may not be attached to a record at all.

    Returns the asset, or a :class:`Response` carrying the reason.
    """
    body = object_body(request)
    if isinstance(body, Response):
        return body

    asset_id = body.get("asset_id")
    if not asset_id or not is_uuid(asset_id):
        return Response(
            {
                "error": "asset_id is required.",
                "code": "asset_not_found",
                "errors": [error_entry("asset_not_found", "asset_id")],
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    asset = FileAsset.objects.filter(
        id=asset_id, workspace__slug=slug, is_deleted=False
    ).first()
    if asset is None:
        return Response(
            {
                "error": "The asset does not exist.",
                "code": "asset_not_found",
                "errors": [error_entry("asset_not_found", "asset_id")],
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    if asset.project_id and asset.project_id != record.project_id:
        return Response(
            {
                "error": "The asset belongs to another project.",
                "code": "asset_project_mismatch",
                "errors": [error_entry("asset_project_mismatch", "asset_id")],
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    if any(getattr(asset, field) for field in _ASSET_ENTITY_FIELDS):
        # The file hangs on another entity (a work item, a page, a
        # comment, a record of another batch). The check comes first: the
        # answer names the entity the file already belongs to, not the
        # kind of entity type it carries.
        return Response(
            {
                "error": "The asset is already bound to a record.",
                "code": "asset_already_bound",
                "errors": [error_entry("asset_already_bound", "asset_id")],
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    if asset.entity_type not in _BINDABLE_ENTITY_TYPES:
        # The file belongs to a whole entity — the cover of a project,
        # the logo of a workspace, an avatar. It carries no entity
        # foreign key, so the check above cannot see it.
        return Response(
            {
                "error": "The asset belongs to another entity.",
                "code": "asset_not_bindable",
                "errors": [error_entry("asset_not_bindable", "asset_id")],
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    return asset


# --------------------------------------------------------------------------- #
# The gates of a batch (M111)                                                  #
# --------------------------------------------------------------------------- #


def describe_gate_reasons(reasons: list[str]) -> list[dict]:
    """Turn the reason list of the gates into one entry per problem.

    The shape is the one of
    :func:`plane.app.testcase_validity.describe_reasons` — the field
    path, the stable reason code, the Chinese sentence and the case
    number / step sequence the problem belongs to — so the front end puts
    the message next to the input it belongs to, whichever rule found the
    problem.
    """
    described = []
    for reason in reasons:
        case_number, step_sequence, code = split_reason(reason)
        field = GATE_REASON_FIELD_PATHS.get(code, "")
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
                "message": APP_REASON_MESSAGES_ZH.get(code, reason),
                "path": ".".join(parts),
                "field": field,
                "case_number": case_number,
                "step_sequence": step_sequence,
            }
        )
    return described


def _step_records_something(step) -> bool:
    """True when the step carries an observation of its own.

    The measured value is the text it carries or the files attached to it
    — the same definition the rules of the db layer use.
    """
    return is_filled_markdown(step.actual_value) or step.attachment_count > 0


def finalize_gate_reasons(run: RunData) -> list[str]:
    """The reasons why a batch may not be closed yet.

    Two rules, and both of them are about a record that claims more than
    it carries:

    * **a step that was never touched** — no verdict and nothing written
      about it — is an unfinished execution, and the batch may not close
      over it. Two shapes stay allowed, because the standard asks for
      them: a step that was *not* executed but whose reason is written in
      the measured value (§6.1 rule 1 — REC-SW-001's own sample has one),
      and a case concluded ``N/A``: its blank boxes are the conclusion
      itself, not a missing execution.
    * **a 需人工 conclusion needs a 依据** — why the case cannot be
      judged by the machine. The executor and the condition answer *who*
      and *when*; the basis answers *why*, and without it 需人工 is a
      default that lets a batch close over cases nobody executed.

    The cases the rules of the db layer already judge are left alone, so
    that one problem is reported once: a ``PASS`` case with a blank step
    is ``conclusion_pass_with_unset_step``, and a case without a
    conclusion is ``conclusion_invalid``.
    """
    reasons: list[str] = []

    for case in run.cases:
        label = case.case_number.strip() or "?"

        if case.conclusion == RUN_CASE_CONCLUSION_NA:
            # The case declares that it does not apply to this build: its
            # blank boxes are the conclusion (§6.3).
            continue

        if case.conclusion == RUN_CASE_CONCLUSION_MANUAL:
            if not (
                (case.na_reason or "").strip() or (case.na_criteria_source or "").strip()
            ):
                reasons.append(f"{label}|{RUN_MANUAL_WITHOUT_BASIS}")

        if case.conclusion not in (RUN_CASE_CONCLUSION_FAIL, RUN_CASE_CONCLUSION_MANUAL):
            # A PASS case is judged by ``conclusion_pass_with_unset_step``
            # and a case without a conclusion by ``conclusion_invalid``.
            continue

        for step in case.steps:
            if step.conclusion != STEP_VERDICT_UNSET:
                continue
            if _step_records_something(step):
                # §6.1 rule 1: the step was not executed and the reason is
                # written in the measured value — the 需人工 shape of the
                # standard. It is a record, not a hole.
                continue
            reasons.append(f"{label}|step_{step.sequence_id}:{RUN_STEP_CONCLUSION_UNSET}")

    return reasons


# --------------------------------------------------------------------------- #
# The evidence of a case record                                                 #
# --------------------------------------------------------------------------- #


def evidence_header(run) -> str:
    """The canonical evidence header of a batch, rendered by the server.

    The header names the batch the record belongs to, the version under
    test, the day of the execution and the executor. It is **computed**
    and never stored, so it cannot name a batch that is not this one and
    cannot go stale when the header of the batch is filled in later — the
    two ways a hand-written header goes wrong.

    The front end shows this block above the evidence of the case; the
    tester writes the observation itself and never the batch number.
    """
    version_parts = []
    if run.version_number:
        version_parts.append(run.version_number)
    if run.build_number:
        version_parts.append(f"构建 {run.build_number}")
    if run.commit_hash:
        version_parts.append(f"提交 {run.commit_hash}")

    if version_parts:
        version_text = version_parts[0]
        if len(version_parts) > 1:
            version_text += "（" + "，".join(version_parts[1:]) + "）"
    else:
        version_text = "（未记录）"

    return "\n".join(
        [
            f"**批次**：{run.number}",
            f"**被测版本**：{version_text}",
            f"**执行日期**：{run.run_date.isoformat() if run.run_date else '（未记录）'}",
            f"**执行人**：{run.executed_by_name or '（未记录）'}",
        ]
    )


#: The shape of a batch number (``RUN-<YYYYMMDD>-<NN>``), as it is
#: written by hand in an evidence. The server hands the number out; a
#: number in the text of an evidence is always a hand-written one.
RUN_NUMBER_IN_TEXT = re.compile(r"RUN-\d{8}-\d{2}")


def _retest_chain_numbers(run) -> set[str]:
    """The numbers of the batches this one re-tests, and of their own.

    A re-test may name the batch it re-tests in its evidence: that is
    the relation the link already carries, written down for the reader.
    Any *other* batch number in the evidence is a hand-written one.
    """
    numbers: set[str] = set()
    seen: set[str] = set()
    current = run
    while current is not None and current.pk is not None:
        if current.pk in seen:
            break
        seen.add(current.pk)
        if current.number:
            numbers.add(current.number)
        current = current.retest_of
    return numbers


def foreign_batch_number_refusal(run_case, evidence) -> Response:
    """The 400 of an evidence that names a batch which is not this one.

    ``None`` when the evidence names no other batch. The number of a
    batch is written by the server (it hands the number out and renders
    the header of the record); a number inside the text of an evidence is
    always written by hand, and a hand-written number is exactly how the
    record of a batch ends up pointing at another one.
    """
    text = evidence or ""
    if not text.strip():
        return None

    run = run_case.test_run
    allowed = _retest_chain_numbers(run)
    allowed.add(run.number)

    for number in RUN_NUMBER_IN_TEXT.findall(text):
        if number in allowed:
            continue
        return Response(
            {
                "error": (
                    f"The evidence names another batch ({number}); the batch "
                    f"number is written by the server."
                ),
                "code": RUN_FOREIGN_BATCH_NUMBER,
                "errors": [
                    error_entry(RUN_FOREIGN_BATCH_NUMBER, "evidence", APP_REASON_MESSAGES_ZH[RUN_FOREIGN_BATCH_NUMBER])
                ],
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


def copied_evidence_refusal(run_case, evidence) -> Response:
    """The 409 of an evidence that is a verbatim copy of another batch's.

    ``None`` when the evidence is not a copy. The copy is the twin a
    re-test is supposed to replace: the same cases, the same evidence
    text, and no link between the two batches — a second execution that
    never happened. The re-test channel copies the *case set* and clears
    the outcome, so an evidence that is identical to the one of another
    batch is always written by hand.
    """
    text = (evidence or "").strip()
    if not text:
        return None

    twins = (
        TestRunCase.objects.filter(
            project_id=run_case.project_id,
            case_number=run_case.case_number,
        )
        .exclude(pk=run_case.pk)
        .select_related("test_run")
    )
    for twin in twins:
        if (twin.evidence or "").strip() != text:
            continue
        return Response(
            {
                "error": (
                    "The evidence is a copy of the evidence of the same case "
                    f"in batch {twin.test_run.number}."
                ),
                "code": RUN_EVIDENCE_COPIED,
                "errors": [
                    error_entry(RUN_EVIDENCE_COPIED, "evidence", APP_REASON_MESSAGES_ZH[RUN_EVIDENCE_COPIED])
                ],
            },
            status=status.HTTP_409_CONFLICT,
        )
    return None


def evidence_write_refusal(run_case, evidence) -> Response:
    """The refusal of an evidence that breaks one of the rules above.

    ``None`` when the write is fine. A write that does not change the
    evidence is never refused: the record was accepted once, and a
    refusal of an unrelated field would block the correction of the rest
    of the record.
    """
    if evidence is None:
        return None
    if (evidence or "").strip() == (run_case.evidence or "").strip():
        return None

    refusal = foreign_batch_number_refusal(run_case, evidence)
    if refusal is not None:
        return refusal
    return copied_evidence_refusal(run_case, evidence)


# --------------------------------------------------------------------------- #
# The re-test channel                                                          #
# --------------------------------------------------------------------------- #


def retest_header_defaults(source_run) -> dict:
    """The header fields a re-test inherits from the batch it re-tests.

    A re-test usually runs against another build, so the client may
    write its own version; what it does not write, the re-test takes
    over from the original batch. The executor is the person of the
    original execution until somebody else is named.
    """
    return {
        "version_number": source_run.version_number,
        "build_number": source_run.build_number,
        "commit_hash": source_run.commit_hash,
        "environment": source_run.environment,
        "executed_by": source_run.executed_by_id,
    }


def retest_authoring_reasons(source_run) -> list[str]:
    """The authoring reasons of the cases a re-test would copy.

    The same check the add path runs, over the snapshot the original
    batch holds: a case that is not written yet (an expected result that
    a reader cannot judge, §5.6) must not enter an execution batch — not
    through the add path and not through the re-test path either.
    """
    reasons: list[str] = []
    for run_case in source_run.run_cases.all().order_by("created_at"):
        header = {
            "title": run_case.case_title,
            "purpose": run_case.case_purpose,
            "object_overview": run_case.case_object_overview,
            "priority": run_case.case_priority,
            "preconditions": run_case.case_preconditions,
            "environment": run_case.case_environment,
        }
        steps = [
            {
                "action": record.action_snapshot,
                "expected_result": record.expected_result_snapshot,
            }
            for record in run_case.step_records.all().order_by("sequence_id")
        ]
        reasons.extend(
            f"{run_case.case_number}|{reason}"
            for reason in check_authoring(
                run_case.case_module_code,
                header,
                steps,
                number=run_case.case_number,
            )
        )
    return reasons


def copy_run_cases(source_run, new_run, actor_id=None) -> list[TestRunCase]:
    """Copy the case set of ``source_run`` into ``new_run``, cleared.

    The header of every case and every step is copied — the re-test runs
    *these* cases — but nothing of the execution is: no conclusion, no
    evidence, no measured value, no verdict and no file. What the
    original batch recorded stays there; the re-test writes its own
    record of its own execution.

    The rows are written with the attribution of the request, so a
    service principal owns the records it copied (the ownership step of
    the authorization chain matches the creator against the bot of the
    principal).
    """
    created: list[TestRunCase] = []

    for source_case in source_run.run_cases.all().order_by("created_at"):
        run_case = TestRunCase(
            test_run=new_run,
            test_case=source_case.test_case,
            project=new_run.project,
            workspace=new_run.workspace,
            case_number=source_case.case_number,
            case_module_code=source_case.case_module_code,
            case_title=source_case.case_title,
            case_purpose=source_case.case_purpose,
            case_object_overview=source_case.case_object_overview,
            case_priority=source_case.case_priority,
            case_preconditions=source_case.case_preconditions,
            case_environment=source_case.case_environment,
            # The outcome of the original execution is not copied: the
            # re-test writes its own conclusion, its own evidence and its
            # own reasons.
            conclusion="",
            evidence="",
            remarks="",
            actual_result_summary="",
            na_reason="",
            na_criteria_source="",
            manual_executor="",
            manual_condition="",
        )
        run_case.save(created_by_id=actor_id)

        for record in source_case.step_records.all().order_by("sequence_id"):
            step_record = TestRunStepRecord(
                test_run_case=run_case,
                project=new_run.project,
                sequence_id=record.sequence_id,
                action_snapshot=record.action_snapshot,
                expected_result_snapshot=record.expected_result_snapshot,
                # The verdict and the measured value of the original run
                # are not copied either.
                conclusion=STEP_VERDICT_UNSET,
                actual_value="",
            )
            step_record.save(created_by_id=actor_id)

        created.append(run_case)

    return created
