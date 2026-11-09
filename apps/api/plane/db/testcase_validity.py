# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Record-validity rules for test-case execution records (STD-SW-003 §6).

Pure functions: they take plain dataclasses (no Django model, no database)
and return the list of reasons why a record is **not** valid. An empty list
means the record is valid.

The rules live here — not in a view — so that every surface (the app API
of M64, the v1 API of M65, the web forms of M66/M67) enforces the same
standard, and so the rules can be unit-tested without a database.

Reason string format
---------------------
A reason is a stable machine-readable code. Two optional decorations carry
the location of the problem:

``step_<n>:<code>``
    The problem is on step ``n`` of the case.
``<case number>|<reason>``
    The problem is on that case of a batch (used by :func:`validate_run`).

The codes themselves never contain ``:`` or ``|``, so the decorations can
be split off unambiguously. :data:`REASON_MESSAGES` maps the bare code to
an English sentence; the front end localises from the code.

Markdown and attachments
------------------------
The measured value of a step and the evidence of a case are stored as the
**Markdown source** that was typed — no HTML conversion, no stripping of
the markup. The rules read that source with a **parser** (mistune, the
same CommonMark the front end renders this field with), so what the
tester sees is what the rules judge:

* the markup is removed before the value is judged, so ``**不一致**`` and
  ``<b>正常</b>`` are judged as ``不一致`` and ``正常``;
* **an image contributes no text of its own — not even its alt text.**
  ``![alt](url)`` is a picture and not a sentence, in every shape it can
  be written (inline, in the reference style, with balanced parentheses
  in the destination, or with the syntax left open);
* the content of a code span is **literal text**: no markup inside it is
  markup. `` `ok` `` is the word itself and is rejected as an evaluation
  word, while `` `**不一致**` `` is a quotation of the raw source and is
  not;
* a value that carries no text of its own is not a measured value: an
  inline image is a picture, and a placeholder is filler;
* a file that is attached to the step or to the case **is** a measured
  value — the screenshot of the screen is what the tester saw. How many
  files are attached comes in through the dataclasses
  (``attachment_count`` and ``evidence_attachment_count``).
"""

from dataclasses import dataclass
from datetime import date
import re

# Third party imports
import mistune

# Module imports
from plane.db.testcase_numbering import (
    is_valid_module_code,
    parse_case_number,
    parse_run_number,
)

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

# Verdict of a single step inside a batch. The names carry ``VERDICT`` so
# they cannot be mixed up with the reason codes below (``STEP_CONCLUSION_*``),
# which describe what is wrong with a step.
STEP_VERDICT_PASS = "pass"
STEP_VERDICT_FAIL = "fail"
STEP_VERDICT_UNSET = "unset"
STEP_VERDICTS = frozenset({STEP_VERDICT_PASS, STEP_VERDICT_FAIL, STEP_VERDICT_UNSET})

# Verdict of a whole case inside a batch (the four states of the standard).
RUN_CASE_CONCLUSION_PASS = "PASS"
RUN_CASE_CONCLUSION_FAIL = "FAIL"
RUN_CASE_CONCLUSION_NA = "N/A"
RUN_CASE_CONCLUSION_MANUAL = "MANUAL"
RUN_CASE_CONCLUSIONS = (
    RUN_CASE_CONCLUSION_PASS,
    RUN_CASE_CONCLUSION_FAIL,
    RUN_CASE_CONCLUSION_NA,
    RUN_CASE_CONCLUSION_MANUAL,
)

# Priority of a test case (P0–P3).
PRIORITIES = ("P0", "P1", "P2", "P3")

# Lifecycle of a batch. A completed batch is frozen.
RUN_STATUS_DRAFT = "draft"
RUN_STATUS_IN_PROGRESS = "in_progress"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUSES = (RUN_STATUS_DRAFT, RUN_STATUS_IN_PROGRESS, RUN_STATUS_COMPLETED)

# --------------------------------------------------------------------------- #
# Reason codes
# --------------------------------------------------------------------------- #

CASE_NUMBER_FORMAT = "case_number_format"
MODULE_CODE_FORMAT = "module_code_format"
CASE_NUMBER_MODULE_MISMATCH = "case_number_module_mismatch"
TITLE_MISSING = "title_missing"
PURPOSE_MISSING = "purpose_missing"
OBJECT_OVERVIEW_MISSING = "object_overview_missing"
PRECONDITIONS_MISSING = "preconditions_missing"
ENVIRONMENT_MISSING = "environment_missing"
PRIORITY_INVALID = "priority_invalid"
STEP_COUNT_ZERO = "step_count_zero"
ACTION_EMPTY = "action_empty"
EXPECTED_EMPTY = "expected_empty"
STEP_CONCLUSION_INVALID = "step_conclusion_invalid"
STEP_CONCLUSION_UNSET = "step_conclusion_unset"
FAIL_WITHOUT_ACTUAL_VALUE = "fail_without_actual_value"
ACTUAL_VALUE_IS_EVALUATION = "actual_value_is_evaluation"
#: The measured value of a failed step carries nothing but an image.
#: The code never decides the validity on its own — it always comes with
#: :data:`FAIL_WITHOUT_ACTUAL_VALUE` — but it tells the front end what
#: to say: an inline image is not a measured value, the file behind it
#: has to be attached to the step.
ACTUAL_VALUE_IS_IMAGE_ONLY = "actual_value_is_image_only"
ACTUAL_VALUE_WITHOUT_CONCLUSION = "actual_value_without_conclusion"
CONCLUSION_INVALID = "conclusion_invalid"
CONCLUSION_PASS_WITH_FAILED_STEP = "conclusion_pass_with_failed_step"
CONCLUSION_PASS_WITH_UNSET_STEP = "conclusion_pass_with_unset_step"
CONCLUSION_FAIL_WITHOUT_FAILED_STEP = "conclusion_fail_without_failed_step"
CONCLUSION_FAIL_WITH_UNSET_STEP = "conclusion_fail_with_unset_step"
CONCLUSION_NA_WITHOUT_REASON = "conclusion_na_without_reason"
CONCLUSION_NA_WITHOUT_CRITERIA_SOURCE = "conclusion_na_without_criteria_source"
CONCLUSION_NA_WITH_FAILED_STEP = "conclusion_na_with_failed_step"
CONCLUSION_MANUAL_WITHOUT_EXECUTOR = "conclusion_manual_without_executor"
CONCLUSION_MANUAL_WITHOUT_CONDITION = "conclusion_manual_without_condition"
CONCLUSION_MANUAL_WITH_FAILED_STEP = "conclusion_manual_with_failed_step"
EVIDENCE_MISSING = "evidence_missing"
RUN_NUMBER_FORMAT = "run_number_format"
RUN_DATE_MISSING = "run_date_missing"
RUN_VERSION_NUMBER_MISSING = "run_version_number_missing"
RUN_BUILD_NUMBER_MISSING = "run_build_number_missing"
RUN_COMMIT_HASH_MISSING = "run_commit_hash_missing"
RUN_ENVIRONMENT_MISSING = "run_environment_missing"
RUN_EXECUTED_BY_MISSING = "run_executed_by_missing"
DUPLICATE_CASE_NUMBER = "duplicate_case_number"
RUN_COMPLETED = "run_completed"
#: A conclusion that is written into a batch is never changed afterwards
#: (STD-SW-003 §6). The model layer raises this code; the batch is
#: corrected by voiding the case record and running again in a new batch.
CONCLUSION_IS_FINAL = "conclusion_is_final"


#: English sentence per bare reason code. The front end localises from the
#: code; this table is for API responses and log lines.
REASON_MESSAGES = {
    CASE_NUMBER_FORMAT: "The test case number must look like TC-<MODULE>-<NNN>.",
    MODULE_CODE_FORMAT: "The module code must be 2 to 6 uppercase letters.",
    CASE_NUMBER_MODULE_MISMATCH: "The test case number does not carry the module code of the case.",
    TITLE_MISSING: "The case title is required.",
    PURPOSE_MISSING: "The test purpose is required.",
    OBJECT_OVERVIEW_MISSING: "The test object overview is required.",
    PRECONDITIONS_MISSING: "The preconditions are required.",
    ENVIRONMENT_MISSING: "The environment and version are required.",
    PRIORITY_INVALID: "The priority must be one of P0, P1, P2, P3.",
    STEP_COUNT_ZERO: "The case has no step.",
    ACTION_EMPTY: "The step action is required.",
    EXPECTED_EMPTY: "The step expected result is required.",
    STEP_CONCLUSION_INVALID: "The step verdict must be pass, fail or unset.",
    STEP_CONCLUSION_UNSET: "The step verdict must be marked, it may not be left blank.",
    FAIL_WITHOUT_ACTUAL_VALUE: "A failed step must carry the measured value — as text or as an attached file.",
    ACTUAL_VALUE_IS_EVALUATION: "The measured value must be the observed original, not an evaluation word.",
    ACTUAL_VALUE_IS_IMAGE_ONLY: "The measured value is only an image — attach the file to the step.",
    ACTUAL_VALUE_WITHOUT_CONCLUSION: "A measured value without a step verdict is not allowed.",
    CONCLUSION_INVALID: "The conclusion must be PASS, FAIL, N/A or MANUAL.",
    CONCLUSION_PASS_WITH_FAILED_STEP: "A PASS conclusion must not have a failed step.",
    CONCLUSION_PASS_WITH_UNSET_STEP: "A PASS conclusion must not have a step with a blank verdict.",
    CONCLUSION_FAIL_WITHOUT_FAILED_STEP: "A FAIL conclusion needs at least one failed step.",
    CONCLUSION_FAIL_WITH_UNSET_STEP: "A FAIL conclusion must not have a step with a blank verdict.",
    CONCLUSION_NA_WITHOUT_REASON: "An N/A conclusion needs a reason.",
    CONCLUSION_NA_WITHOUT_CRITERIA_SOURCE: "An N/A conclusion needs the source of its criteria.",
    CONCLUSION_NA_WITH_FAILED_STEP: "An N/A conclusion must not have a failed step.",
    CONCLUSION_MANUAL_WITHOUT_EXECUTOR: "A MANUAL conclusion needs to name who executes it.",
    CONCLUSION_MANUAL_WITHOUT_CONDITION: "A MANUAL conclusion needs the condition under which it is executed.",
    CONCLUSION_MANUAL_WITH_FAILED_STEP: "A MANUAL conclusion must not have a failed step, that is a FAIL.",
    EVIDENCE_MISSING: "The evidence is required.",
    RUN_NUMBER_FORMAT: "The batch number must look like RUN-<YYYYMMDD>-<NN>.",
    RUN_DATE_MISSING: "The execution date is required.",
    RUN_VERSION_NUMBER_MISSING: "The tested version number is required.",
    RUN_BUILD_NUMBER_MISSING: "The tested build number is required.",
    RUN_COMMIT_HASH_MISSING: "The tested commit hash is required.",
    RUN_ENVIRONMENT_MISSING: "The execution environment is required.",
    RUN_EXECUTED_BY_MISSING: "The executor is required.",
    DUPLICATE_CASE_NUMBER: "The same test case is in the batch twice.",
    RUN_COMPLETED: "The batch is completed, a conclusion is never written back into it.",
    CONCLUSION_IS_FINAL: "A conclusion that is written into a batch is never changed.",
}


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #

#: The parser. mistune reads the value with CommonMark — the standard
#: the front end renders this field with — so the rules and the
#: rendered form agree: what the tester sees is what the rules judge.
#:
#: The strikethrough plugin is on: a judgement word is a judgement word
#: however it is marked, and the source is stored as it was typed.
_MARKDOWN_PARSER = mistune.create_markdown(renderer="ast", plugins=["strikethrough"])

#: The shapes the tower ruled to be images although CommonMark does not
#: treat them as such:
#:
#: * ``![](Screenshot (1).png)`` — a destination with a space in it;
#: * ``![](a.png`` — a destination that was never closed;
#: * ``![alt][ref]`` — a reference-style image whose definition is
#:   missing.
#:
#: CommonMark reads all three as literal text, so the parser leaves them
#: in the text; they are taken out here. This is the only place where
#: the rules look at the source with a pattern instead of with the
#: parse — and it never looks inside a code span, whose content is a
#: quotation.
_LOOSE_IMAGE_RE = re.compile(
    # an inline image, closed — the destination may hold spaces and
    # balanced parentheses
    r"!\[[^\]\n]*\]\((?:[^()\n]|\([^()\n]*\))*\)"
    # an inline image that was never closed
    r"|!\[[^\]\n]*\]\([^)\n]*"
    # a reference-style image
    r"|!\[[^\]\n]*\]\[[^\]\n]*\]"
)


def _strip_loose_images(text: str) -> tuple[str, bool]:
    """Take the images CommonMark does not recognise out of the text.

    Returns the text without them and whether there was one.
    """
    if "!" not in text:
        return text, False

    has_image = False

    def replace(match):
        nonlocal has_image
        has_image = True
        return " "

    return _LOOSE_IMAGE_RE.sub(replace, text), has_image


def _collect_markdown(node: dict) -> tuple[str, bool]:
    """Read one node of the parse.

    Returns the text of the node and whether it holds an image.
    """
    node_type = node.get("type")

    if node_type == "text":
        return _strip_loose_images(node.get("raw", ""))

    if node_type in ("codespan", "block_code"):
        # The content of a code span is literal text: no markup inside
        # it is markup. A fenced code block is literal content too.
        return node.get("raw", ""), False

    if node_type == "image":
        # A picture is a file, not a sentence: it carries no text.
        return "", True

    if node_type in ("inline_html", "block_html", "thematic_break", "blank_line"):
        # Markup, not text.
        return "", False

    if node_type in ("linebreak", "softbreak"):
        return "\n", False

    children = node.get("children")
    if children:
        parts: list[str] = []
        has_image = False
        for child in children:
            child_text, child_image = _collect_markdown(child)
            parts.append(child_text)
            has_image = has_image or child_image
        return "".join(parts), has_image

    raw = node.get("raw")
    if isinstance(raw, str):
        return raw, False
    return "", False


def _read_markdown(value: object) -> tuple[str, bool]:
    """Read the value once: the text it carries and whether it has an image.

    One parse, two answers. Every rule of the standard reads the value
    through this function, so the text and the image flag can never
    disagree.
    """
    if not isinstance(value, str):
        return "", False

    parts: list[str] = []
    has_image = False
    blocks = _MARKDOWN_PARSER(value) or []
    for index, block in enumerate(blocks):
        if index:
            # Two blocks are two pieces of text, not one word glued
            # together.
            parts.append("\n")
        block_text, block_image = _collect_markdown(block)
        parts.append(block_text)
        has_image = has_image or block_image
    return "".join(parts), has_image


def markdown_text(value: object) -> str:
    """The text of a Markdown source, with the markup taken away.

    The result answers two questions: does the value carry text of its
    own, and is that text nothing but an evaluation word. The markup is
    not what the value says — ``**不一致**`` says exactly what
    ``不一致`` says — so it is removed before the comparison.

    **An image contributes no text of its own — not even its alt text.**
    ``![alt](url)`` is a picture, and a picture is a file, not a
    sentence. The text of a link stays, the destination of a link does
    not, and a link reference definition is not text either.
    """
    return _read_markdown(value)[0]


def contains_image(value: object) -> bool:
    """True when the source holds an image.

    The shapes that count: every image the parser recognises — inline,
    in the reference style, with balanced parentheses in the destination
    at any depth — and the shapes CommonMark does not recognise but the
    tower ruled to be images (see :data:`_LOOSE_IMAGE_RE`).

    A value inside a code span holds no image: `` `![](x.png)` `` is a
    quotation, not a picture.
    """
    return _read_markdown(value)[1]


# --------------------------------------------------------------------------- #
# Evaluation words (评价词)
# --------------------------------------------------------------------------- #

#: Words that judge the result instead of reporting it. A measured value
#: that is nothing but one of these is not the observed original — the
#: standard rejects "不一致" as a measured value.
#:
#: The list carries judgements only. An answer to a yes/no question is
#: an observation ("no" answers "did the device answer?"), so "yes" and
#: "no" are **not** in the list — a measured value of exactly "no" is a
#: legitimate result. "ok", "pass", "fail" and "n/a" stay: they judge
#: the outcome or the applicability, exactly like "正常" and "通过" do.
EVALUATION_PHRASES = frozenset(
    {
        "不一致",
        "一致",
        "正常",
        "异常",
        "正确",
        "错误",
        "符合预期",
        "符合",
        "不符合预期",
        "不符合",
        "同预期",
        "与预期一致",
        "与预期不符",
        "通过",
        "不通过",
        "合格",
        "不合格",
        "成功",
        "失败",
        "良好",
        "正常显示",
        "异常显示",
        "无异常",
        "有异常",
        "满足预期",
        "不满足预期",
        "无问题",
        "有问题",
        "生效",
        "未生效",
        "ok",
        "o.k.",
        "n/a",
        "na",
        "pass",
        "passed",
        "fail",
        "failed",
    }
)

#: Values that are only filler — the field was touched, but nothing was
#: measured. They do not count as a measured value. The list holds
#: neither an evaluation word nor an observation: "n/a" stays an
#: evaluation word, "待补充" is a placeholder.
PLACEHOLDER_VALUES = frozenset(
    {
        "-",
        "--",
        "---",
        "—",
        "–",
        ".",
        "..",
        "...",
        "…",
        "?",
        "??",
        "???",
        "*",
        "**",
        "_",
        "__",
        "tbd",
        "todo",
        "xxx",
        "待补充",
        "待填写",
        "待填",
        "待定",
        "暂填",
        "待确认",
    }
)

#: Punctuation stripped from both ends before the evaluation-word lookup.
_EDGE_PUNCTUATION = " \t\r\n\"'“”‘’`.,;:!?，。；：！？、"


def _is_filled(value: object) -> bool:
    """True when ``value`` is a string with something other than blanks."""
    return isinstance(value, str) and value.strip() != ""


def _normalize_evaluation_candidate(value: str) -> str:
    """Strip blanks and edge punctuation, then case-fold."""
    return value.strip().strip(_EDGE_PUNCTUATION).strip().casefold()


def is_placeholder(value: object) -> bool:
    """True when ``value`` is nothing but a placeholder.

    The comparison is on the **whole** value: "—" is a placeholder,
    "— (the device stayed silent)" is an observation.
    """
    if not isinstance(value, str):
        return False
    normalized = value.strip().casefold()
    if normalized == "":
        return False
    return normalized in PLACEHOLDER_VALUES


def is_filled_markdown(value: object) -> bool:
    """True when the Markdown source carries text of its own.

    The markup is removed first: a value that consists only of an image
    (``![](…)``) carries no text, and a placeholder carries no text
    either. ``**不一致**`` carries the text ``不一致`` — the markup is not
    what makes a value empty.
    """
    return _is_filled_text(_read_markdown(value)[0])


def _is_filled_text(text: str) -> bool:
    """True when the text carries something of its own."""
    if text.strip() == "":
        return False
    return not is_placeholder(text)


def is_evaluation_phrase(value: object) -> bool:
    """True when ``value`` is nothing but an evaluation word.

    The comparison is on the **whole** value, not a substring: "命令失败，
    退出码 1" reports an observation and stays valid, while "不一致" and
    "失败" alone are judgements and are rejected.

    The markup is removed first, so ``**不一致**`` and ``<b>正常</b>``
    are judged on the text they carry. A code span is literal text:
    `` `不一致` `` is the word itself and is rejected, while
    `` `**不一致**` `` is a quotation of the raw source and is not.
    """
    if not isinstance(value, str):
        return False
    return _is_evaluation_text(_read_markdown(value)[0])


def _is_evaluation_text(text: str) -> bool:
    """True when the text is nothing but an evaluation word."""
    normalized = _normalize_evaluation_candidate(text)
    if normalized == "":
        return False
    return normalized in EVALUATION_PHRASES


# --------------------------------------------------------------------------- #
# Input shapes (plain dataclasses — no Django involved)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StepRecordData:
    """One row of the step table of a batch case.

    ``attachment_count`` is the number of files that are attached to the
    step record. A file is a measured result in its own right: the
    screenshot of the screen is what the tester saw. Only the number is
    carried, because the rules ask one question about it: is there at
    least one attachment?
    """

    sequence_id: int
    action: str = ""
    expected_result: str = ""
    conclusion: str = STEP_VERDICT_UNSET
    actual_value: str = ""
    attachment_count: int = 0


@dataclass(frozen=True)
class RunCaseData:
    """One test case inside a batch, with the header snapshot and the steps.

    ``evidence_attachment_count`` is the number of files that are
    attached to the case record. The evidence of a case is often a
    screenshot, and a screenshot that is only written down as a link in
    the Markdown is not a file the batch can show.
    """

    case_number: str = ""
    module_code: str = ""
    title: str = ""
    purpose: str = ""
    object_overview: str = ""
    priority: str = ""
    preconditions: str = ""
    environment: str = ""
    steps: tuple[StepRecordData, ...] = ()
    conclusion: str = ""
    evidence: str = ""
    na_reason: str = ""
    na_criteria_source: str = ""
    manual_executor: str = ""
    manual_condition: str = ""
    remarks: str = ""
    actual_result_summary: str = ""
    evidence_attachment_count: int = 0


@dataclass(frozen=True)
class RunData:
    """One batch: the header of the execution plus the cases it holds.

    ``executed_by`` is the name of the executor as it can be read from
    the live user row; ``executed_by_name`` is the text snapshot the
    batch keeps next to the foreign key. The batch is valid when either
    of them is filled — a deleted user leaves the foreign key empty, but
    the snapshot keeps the name of the person who executed the batch.

    ``executed_by_name`` is the **last** field on purpose: a field that
    is added in the middle would silently bind the wrong value in a
    positional construction.
    """

    number: str = ""
    run_date: date | None = None
    version_number: str = ""
    build_number: str = ""
    commit_hash: str = ""
    environment: str = ""
    executed_by: str = ""
    status: str = ""
    cases: tuple[RunCaseData, ...] = ()
    executed_by_name: str = ""


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #


def validate_run_case(case: RunCaseData) -> list[str]:
    """Return the reasons why one batch case record is not valid.

    An empty list means the record satisfies every rule of the standard.
    """
    reasons: list[str] = []

    # --- number and module code -------------------------------------------
    parsed_number = parse_case_number(case.case_number)
    if parsed_number is None:
        reasons.append(CASE_NUMBER_FORMAT)
    if not is_valid_module_code(case.module_code):
        reasons.append(MODULE_CODE_FORMAT)
    elif parsed_number is not None and parsed_number[0] != case.module_code:
        reasons.append(CASE_NUMBER_MODULE_MISMATCH)

    # --- header (the eight mandatory items, the step table is below) ------
    if not _is_filled(case.title):
        reasons.append(TITLE_MISSING)
    if not _is_filled(case.purpose):
        reasons.append(PURPOSE_MISSING)
    if not _is_filled(case.object_overview):
        reasons.append(OBJECT_OVERVIEW_MISSING)
    if not _is_filled(case.preconditions):
        reasons.append(PRECONDITIONS_MISSING)
    if not _is_filled(case.environment):
        reasons.append(ENVIRONMENT_MISSING)
    if case.priority not in PRIORITIES:
        reasons.append(PRIORITY_INVALID)

    # --- step table --------------------------------------------------------
    failed_steps: list[StepRecordData] = []
    unset_steps: list[StepRecordData] = []

    if not case.steps:
        reasons.append(STEP_COUNT_ZERO)

    for step in case.steps:
        label = f"step_{step.sequence_id}:"

        if not _is_filled(step.action):
            reasons.append(label + ACTION_EMPTY)
        if not _is_filled(step.expected_result):
            reasons.append(label + EXPECTED_EMPTY)

        if step.conclusion not in STEP_VERDICTS:
            reasons.append(label + STEP_CONCLUSION_INVALID)
            continue

        # The measured value of a step is the text it carries or the
        # files that are attached to it. The Markdown source is read
        # once: a value that holds nothing but an image (``![](…)``)
        # carries no text, and a placeholder carries no text either.
        step_text, step_has_image = _read_markdown(step.actual_value)
        step_has_text = _is_filled_text(step_text)
        step_has_attachment = step.attachment_count > 0
        step_has_measurement = step_has_text or step_has_attachment

        if step.conclusion == STEP_VERDICT_UNSET:
            unset_steps.append(step)
            if case.conclusion in (
                RUN_CASE_CONCLUSION_PASS,
                RUN_CASE_CONCLUSION_FAIL,
            ):
                # The verdict box of an executed step may not stay blank.
                # A case that has not reached a conclusion yet may leave
                # its steps open.
                reasons.append(label + STEP_CONCLUSION_UNSET)
        if step.conclusion == STEP_VERDICT_FAIL:
            failed_steps.append(step)
            if not step_has_measurement:
                reasons.append(label + FAIL_WITHOUT_ACTUAL_VALUE)
                if step_has_image:
                    # The tester wrote a picture but did not attach the
                    # file: an inline image is not a measured value.
                    reasons.append(label + ACTUAL_VALUE_IS_IMAGE_ONLY)

        if step_has_text and _is_evaluation_text(step_text):
            reasons.append(label + ACTUAL_VALUE_IS_EVALUATION)
        if step_has_measurement and step.conclusion == STEP_VERDICT_UNSET:
            reasons.append(label + ACTUAL_VALUE_WITHOUT_CONCLUSION)

    # --- conclusion --------------------------------------------------------
    if case.conclusion not in RUN_CASE_CONCLUSIONS:
        reasons.append(CONCLUSION_INVALID)
    elif case.conclusion == RUN_CASE_CONCLUSION_PASS:
        if failed_steps:
            reasons.append(CONCLUSION_PASS_WITH_FAILED_STEP)
        if unset_steps:
            reasons.append(CONCLUSION_PASS_WITH_UNSET_STEP)
    elif case.conclusion == RUN_CASE_CONCLUSION_FAIL:
        if not failed_steps:
            reasons.append(CONCLUSION_FAIL_WITHOUT_FAILED_STEP)
        if unset_steps:
            reasons.append(CONCLUSION_FAIL_WITH_UNSET_STEP)
    elif case.conclusion == RUN_CASE_CONCLUSION_NA:
        if not _is_filled(case.na_reason):
            reasons.append(CONCLUSION_NA_WITHOUT_REASON)
        if not _is_filled(case.na_criteria_source):
            reasons.append(CONCLUSION_NA_WITHOUT_CRITERIA_SOURCE)
        if failed_steps:
            reasons.append(CONCLUSION_NA_WITH_FAILED_STEP)
    elif case.conclusion == RUN_CASE_CONCLUSION_MANUAL:
        if not _is_filled(case.manual_executor):
            reasons.append(CONCLUSION_MANUAL_WITHOUT_EXECUTOR)
        if not _is_filled(case.manual_condition):
            reasons.append(CONCLUSION_MANUAL_WITHOUT_CONDITION)
        if failed_steps:
            reasons.append(CONCLUSION_MANUAL_WITH_FAILED_STEP)

    # --- evidence ----------------------------------------------------------
    # The evidence is the text the case carries or the files that are
    # attached to it. A screenshot of the report is the evidence, and an
    # inline image in the Markdown is not a file the batch can show.
    if not (is_filled_markdown(case.evidence) or case.evidence_attachment_count > 0):
        reasons.append(EVIDENCE_MISSING)

    return reasons


def validate_run(run: RunData) -> list[str]:
    """Return the reasons why a batch is not valid.

    Batch-level problems (the four environment items of the header, a
    duplicated case) come first; the problems of a single case follow,
    prefixed with the case number so the caller can route them.
    """
    reasons: list[str] = []

    if parse_run_number(run.number) is None:
        reasons.append(RUN_NUMBER_FORMAT)
    if run.run_date is None:
        reasons.append(RUN_DATE_MISSING)
    if not _is_filled(run.version_number):
        reasons.append(RUN_VERSION_NUMBER_MISSING)
    if not _is_filled(run.build_number):
        reasons.append(RUN_BUILD_NUMBER_MISSING)
    if not _is_filled(run.commit_hash):
        reasons.append(RUN_COMMIT_HASH_MISSING)
    if not _is_filled(run.environment):
        reasons.append(RUN_ENVIRONMENT_MISSING)
    if not (_is_filled(run.executed_by) or _is_filled(run.executed_by_name)):
        reasons.append(RUN_EXECUTED_BY_MISSING)

    seen_numbers: set[str] = set()
    for case in run.cases:
        case_label = case.case_number.strip() or "?"
        for reason in validate_run_case(case):
            reasons.append(f"{case_label}|{reason}")
        if case.case_number.strip():
            if case.case_number.strip() in seen_numbers:
                reasons.append(f"{case_label}|{DUPLICATE_CASE_NUMBER}")
            seen_numbers.add(case.case_number.strip())

    return reasons


def validate_run_write(run: RunData) -> list[str]:
    """Return the reasons why the batch may no longer be written to.

    A conclusion that is written into a batch is never changed afterwards,
    so a completed batch is frozen: a re-test opens a new batch.
    """
    if run.status == RUN_STATUS_COMPLETED:
        return [RUN_COMPLETED]
    return []
