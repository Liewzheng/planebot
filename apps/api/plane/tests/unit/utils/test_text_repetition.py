# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest

from plane.utils.text_repetition import collapse_repeated_text


@pytest.mark.unit
class TestCollapseRepeatedText:
    def test_collapses_exact_doubling(self):
        title = "自动曝光算法溯源：formal_calibrate 的来龙去脉"
        assert collapse_repeated_text(title + title) == title

    def test_collapses_more_than_two_copies(self):
        assert collapse_repeated_text("abcabcabc", min_unit_length=3) == "abc"

    def test_keeps_ordinary_value(self):
        value = "检测流程性能实测：目标机 5 轮基线（2026-09-14）"
        assert collapse_repeated_text(value) == value

    def test_ignores_short_repetitions_below_the_unit_threshold(self):
        assert collapse_repeated_text("ABAB") == "ABAB"

    def test_does_not_collapse_partial_repetition(self):
        value = "标题标题标题X"
        assert collapse_repeated_text(value) == value

    def test_trims_and_handles_empty(self):
        assert collapse_repeated_text("  IncidentIncident  ") == "Incident"
        assert collapse_repeated_text("") == ""
