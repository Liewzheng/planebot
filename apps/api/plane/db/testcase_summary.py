# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The summary of one execution batch (STD-SW-003 §6.2 rule 3, §6.3).

Pure functions over the dataclasses of
:mod:`plane.db.testcase_validity` — no Django import, no database — so the
summary can be tested on the shape of a real record and reused by every
surface that shows a batch.

What §6.2 rule 3 asks for
-------------------------
「批次汇总四项：用例总数、`PASS` 数、`FAIL` 数、`N/A` 与 `需人工` 数；另附
不符点清单，逐条列用例编号与一句话现象。」

:func:`summarize_run` returns exactly that: the four counts as
``total`` / ``pass`` / ``fail`` / ``na`` + ``manual``, and the list of
deviations, one entry per case with ``case_number`` and a one-line
phenomenon.

The counts are **not** a partition of the total
------------------------------------------------
The standard's own filled sample (``REC-SW-001``) prints, for one case
that ended ``FAIL`` with one step left unexecuted (「需人工」), the table
``PASS 0 | FAIL 1 | N/A 0 | 需人工 1 | 用例总数 1``. The four numbers add up
to two, and that is deliberate: §6.1 rule 1 says a step whose two boxes
are both blank was not executed and is recorded as 「需人工」, and the
sample's 判定 line says 「步骤 7 两框未勾、实测值写明未执行的，按 `需人工` 记，
不影响 `FAIL` 判定」. So:

* ``fail`` counts the cases concluded ``FAIL`` — the case's own verdict;
* ``manual`` counts the cases recorded as 「需人工」, either at case level
  (``conclusion == MANUAL``) or because a step of a case that was
  concluded something else was never executed (its verdict is still
  blank). A case can be both: it failed on one step and never ran another.
  A case concluded ``N/A`` is not counted twice that way — an N/A case
  does not run its steps, and its blank boxes are the conclusion.

A caller that wants a partition asks for the case conclusions alone (they
are in :class:`RunCaseData`); the summary answers the question the
standard asks, and that question allows the overlap.

What counts as a deviation (§6.2 rule 4)
----------------------------------------
A deviation is 「实测与预期不一致」 — a fact, not yet a defect. A case
contributes one when its conclusion is ``FAIL`` or when one of its steps
is marked ✘. The one-line phenomenon is the case's own
``actual_result_summary`` when the tester wrote one (§4.1 field 13), and
otherwise the measured value of the first ✘ step — which is what §6.1
rule 2 requires a failed step to carry. The step number and the expected
value stay in the step row; the summary is the sentence a person reads.
"""

# Module imports
from plane.db.testcase_validity import (
    RUN_CASE_CONCLUSION_FAIL,
    RUN_CASE_CONCLUSION_MANUAL,
    RUN_CASE_CONCLUSION_NA,
    RUN_CASE_CONCLUSION_PASS,
    STEP_VERDICT_FAIL,
    STEP_VERDICT_UNSET,
    RunCaseData,
    RunData,
    markdown_text,
)

#: The keys of the dict :func:`summarize_run` returns, in the order the
#: table of §6.2 rule 3 prints them (the four counts, then the list).
SUMMARY_KEYS = ("total", "pass", "fail", "na", "manual", "deviations")


def _is_unexecuted(case: RunCaseData) -> bool:
    """True when a step of the case was never executed.

    §6.1 rule 1: 「两个框都没打勾等于没执行，按 `需人工` 重记」. A case record
    that has been snapshotted into a batch carries its steps; a step whose
    verdict is still blank has no box ticked.
    """
    return any(step.conclusion == STEP_VERDICT_UNSET for step in case.steps)


def _is_deviation(case: RunCaseData) -> bool:
    """True when the case reports a mismatch between observed and expected."""
    if case.conclusion == RUN_CASE_CONCLUSION_FAIL:
        return True
    return any(step.conclusion == STEP_VERDICT_FAIL for step in case.steps)


def _deviation_phenomenon(case: RunCaseData) -> str:
    """The one-line phenomenon of a deviating case.

    The tester's own summary when it is filled; otherwise the measured
    value of the first ✘ step. Both are Markdown sources, so the text is
    read with :func:`markdown_text` — a summary is the sentence, not its
    markup.
    """
    summary = markdown_text(case.actual_result_summary).strip()
    if summary:
        return summary

    for step in case.steps:
        if step.conclusion == STEP_VERDICT_FAIL:
            return markdown_text(step.actual_value).strip()

    return ""


def summarize_run(run: RunData) -> dict:
    """Summarise ``run`` the way §6.2 rule 3 asks.

    Returns ``{"total", "pass", "fail", "na", "manual", "deviations"}``
    where ``deviations`` is a list of ``{"case_number", "summary"}`` in
    the order the cases sit in the batch. See the module docstring for why
    the four counts may add up to more than ``total``.
    """
    summary = {"total": 0, "pass": 0, "fail": 0, "na": 0, "manual": 0, "deviations": []}

    conclusion_keys = {
        RUN_CASE_CONCLUSION_PASS: "pass",
        RUN_CASE_CONCLUSION_FAIL: "fail",
        RUN_CASE_CONCLUSION_NA: "na",
        RUN_CASE_CONCLUSION_MANUAL: "manual",
    }

    for case in run.cases:
        summary["total"] += 1

        key = conclusion_keys.get(case.conclusion)
        if key is not None:
            summary[key] += 1

        # A case that was not concluded 需人工 may still hold a step that
        # was never executed (§6.1 rule 1, and the REC-SW-001 sample). A
        # case concluded N/A did not run its steps at all — its blank
        # boxes are the conclusion, not a missing execution.
        if key not in ("manual", "na") and _is_unexecuted(case):
            summary["manual"] += 1

        if _is_deviation(case):
            summary["deviations"].append(
                {
                    "case_number": case.case_number.strip(),
                    "summary": _deviation_phenomenon(case),
                }
            )

    return summary
