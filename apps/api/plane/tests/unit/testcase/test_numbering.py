# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Unit tests for the number formats of test cases and batches (STD-SW-003 §4)."""

from datetime import date

import pytest

from plane.db.testcase_numbering import (
    CASE_SEQUENCE_MAX,
    RUN_SEQUENCE_MAX,
    format_case_number,
    format_run_number,
    is_valid_module_code,
    parse_case_number,
    parse_run_number,
)


pytestmark = pytest.mark.unit


class TestModuleCode:
    @pytest.mark.parametrize("code", ["CL", "CLI", "FIRM", "SYSTEM"])
    def test_a_well_formed_module_code_is_accepted(self, code):
        assert is_valid_module_code(code) is True

    @pytest.mark.parametrize(
        "code",
        ["C", "", "cli", "Cli", "CL-I", "CL I", "CL1", "TOOLONGX", "CL_", None, 42],
    )
    def test_a_malformed_module_code_is_rejected(self, code):
        assert is_valid_module_code(code) is False


class TestCaseNumber:
    def test_a_number_is_rendered_with_three_digits(self):
        assert format_case_number("CLI", 1) == "TC-CLI-001"
        assert format_case_number("CLI", 42) == "TC-CLI-042"
        assert format_case_number("CLI", 999) == "TC-CLI-999"

    def test_a_number_round_trips(self):
        assert parse_case_number(format_case_number("FIRM", 7)) == ("FIRM", 7)

    @pytest.mark.parametrize(
        "number",
        [
            "TC-CLI-1",
            "TC-CLI-01",
            "TC-CLI-0001",
            "TC-CLI-001 ",
            "tc-cli-001",
            "TC-CLI_001",
            "TC-CLI-001-1",
            "TC-C-001",
            "TC-TOOLONGX-001",
            "CLI-001",
            "",
            None,
        ],
    )
    def test_a_malformed_number_is_not_parsed(self, number):
        assert parse_case_number(number) is None

    def test_the_counter_ceiling_is_999(self):
        assert CASE_SEQUENCE_MAX == 999
        with pytest.raises(ValueError):
            format_case_number("CLI", 1000)

    def test_the_counter_starts_at_one(self):
        with pytest.raises(ValueError):
            format_case_number("CLI", 0)

    def test_a_malformed_module_code_cannot_produce_a_number(self):
        with pytest.raises(ValueError):
            format_case_number("cli", 1)


class TestRunNumber:
    def test_a_batch_number_is_rendered_with_two_digits(self):
        assert format_run_number(date(2026, 11, 9), 1) == "RUN-20261109-01"
        assert format_run_number(date(2026, 11, 9), 42) == "RUN-20261109-42"

    def test_a_batch_number_round_trips(self):
        assert parse_run_number(format_run_number(date(2026, 1, 2), 3)) == (
            date(2026, 1, 2),
            3,
        )

    @pytest.mark.parametrize(
        "number",
        [
            "RUN-20261109-1",
            "RUN-20261109-001",
            "RUN-20261109",
            "run-20261109-01",
            "RUN-2026110-01",
            "RUN-20261109-00",
            "",
            None,
        ],
    )
    def test_a_malformed_batch_number_is_not_parsed(self, number):
        assert parse_run_number(number) is None

    def test_an_impossible_date_is_a_format_error(self):
        assert parse_run_number("RUN-20260230-01") is None

    def test_the_counter_ceiling_is_99(self):
        assert RUN_SEQUENCE_MAX == 99
        with pytest.raises(ValueError):
            format_run_number(date(2026, 11, 9), 100)

    def test_the_counter_starts_at_one(self):
        with pytest.raises(ValueError):
            format_run_number(date(2026, 11, 9), 0)
