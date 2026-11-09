# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Number formats for test cases and test runs (STD-SW-003).

Pure functions and constants only — no Django import, no database — so the
numbering rules can be unit-tested on their own and reused by the model
layer (:mod:`plane.db.models.test_case`) as the single source of truth.

Two numbering schemes live here:

``TC-<CODE>-<NNN>``
    A test-case number. ``CODE`` is the module code (2–6 uppercase
    letters), ``NNN`` is a three-digit counter that increases **inside the
    module** (per project × module). A voided case keeps its number: the
    standard forbids handing the same number to a different case.

``RUN-<YYYYMMDD>-<NN>``
    A test-run (batch) number. The counter is per project × execution
    date, so one number identifies one continuous execution. A re-test opens
    a new batch instead of writing back into an old one.
"""

import re
from datetime import date

# Module code: 2–6 uppercase letters (STD-SW-003 §4.1).
MODULE_CODE_MIN_LENGTH = 2
MODULE_CODE_MAX_LENGTH = 6

# Counter ceilings. The standard fixes the digit count, so the ceiling is
# fixed with it: 999 cases per module, 99 batches per project per day.
CASE_SEQUENCE_MAX = 999
RUN_SEQUENCE_MAX = 99

MODULE_CODE_PATTERN = re.compile(r"^[A-Z]{2,6}$")
CASE_NUMBER_PATTERN = re.compile(r"^TC-([A-Z]{2,6})-(\d{3})$")
RUN_NUMBER_PATTERN = re.compile(r"^RUN-(\d{8})-(\d{2})$")


def is_valid_module_code(code: object) -> bool:
    """True when ``code`` is a well-formed module code.

    A module code is 2–6 uppercase ASCII letters — the same code may only
    stand for one group of functions, so the format is deliberately narrow.
    """
    if not isinstance(code, str):
        return False
    return MODULE_CODE_PATTERN.match(code) is not None


def format_case_number(module_code: str, sequence: int) -> str:
    """Render the test-case number for ``module_code`` / ``sequence``."""
    if not is_valid_module_code(module_code):
        raise ValueError(f"invalid module code: {module_code!r}")
    if not isinstance(sequence, int) or isinstance(sequence, bool):
        raise ValueError(f"invalid sequence: {sequence!r}")
    if not 1 <= sequence <= CASE_SEQUENCE_MAX:
        raise ValueError(f"sequence out of range (1..{CASE_SEQUENCE_MAX}): {sequence!r}")
    return f"TC-{module_code}-{sequence:03d}"


def parse_case_number(number: object) -> tuple[str, int] | None:
    """Split a test-case number into ``(module_code, sequence)``.

    ``None`` when ``number`` does not match the standard's format. The
    check is strict: the canonical form carries no blanks around it and
    the counter starts at 001, so ``TC-CLI-000`` is not a number either.
    """
    if not isinstance(number, str):
        return None
    match = CASE_NUMBER_PATTERN.match(number)
    if match is None:
        return None
    sequence = int(match.group(2))
    if sequence < 1:
        return None
    return match.group(1), sequence


def format_run_number(run_date: date, sequence: int) -> str:
    """Render the batch number for ``run_date`` / ``sequence``."""
    if not isinstance(run_date, date):
        raise ValueError(f"invalid run date: {run_date!r}")
    if not isinstance(sequence, int) or isinstance(sequence, bool):
        raise ValueError(f"invalid sequence: {sequence!r}")
    if not 1 <= sequence <= RUN_SEQUENCE_MAX:
        raise ValueError(f"sequence out of range (1..{RUN_SEQUENCE_MAX}): {sequence!r}")
    return f"RUN-{run_date.strftime('%Y%m%d')}-{sequence:02d}"


def parse_run_number(number: object) -> tuple[date, int] | None:
    """Split a batch number into ``(run_date, sequence)``.

    ``None`` when ``number`` does not match the standard's format (an
    impossible calendar date or a counter below 01 is a format error too).
    """
    if not isinstance(number, str):
        return None
    match = RUN_NUMBER_PATTERN.match(number)
    if match is None:
        return None
    raw_date = match.group(1)
    try:
        parsed_date = date(int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:8]))
    except ValueError:
        return None
    sequence = int(match.group(2))
    if sequence < 1:
        return None
    return parsed_date, sequence
