# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest

from plane.utils.page_duplication import (
    CannotDeduplicate,
    deduplicate_page_html,
    is_document_duplicated,
    line_stats,
)


def paragraph(text: str) -> str:
    return f'<p class="editor-paragraph-block">{text}</p>'


def paragraph_with_code(text: str, code: str) -> str:
    return f'<p class="editor-paragraph-block">{text} <code>{code}</code></p>'


BASE = "".join(
    paragraph(f"第 {index} 节：这是一段用于测试重复检测的较长正文，唯一标记 unique-{index}-{'x' * 20}")
    for index in range(1, 131)
)
EXTRA_X = paragraph("第一份独有的一段话，讲的是内核编译参数与工具链版本，标记 marker-alpha-kernel-toolchain")
EXTRA_Y = paragraph("第二份独有的一段话，讲的是串口排障步骤与供电注意事项，标记 marker-beta-uart-power")


@pytest.mark.unit
class TestLineStats:
    def test_counts_long_lines(self):
        html = paragraph("短") + paragraph("这是一段足够长的文本，用于统计唯一行数" * 2)
        stats = line_stats(html)
        assert stats["total_lines"] == 1
        assert stats["unique_lines"] == 1
        assert stats["unique_ratio"] == 1.0


@pytest.mark.unit
class TestIsDocumentDuplicated:
    def test_clean_document_is_not_flagged(self):
        assert is_document_duplicated(BASE) is False

    def test_short_repetitive_document_is_not_flagged(self):
        # below the min_lines threshold, repetition alone must not flag a page
        small = paragraph("重复内容重复内容重复内容重复内容") * 3
        assert is_document_duplicated(small) is False

    def test_document_with_every_line_twice_is_flagged(self):
        assert is_document_duplicated(BASE + BASE) is True


@pytest.mark.unit
class TestDeduplicatePageHtml:
    def test_clean_document_is_refused(self):
        with pytest.raises(CannotDeduplicate):
            deduplicate_page_html(BASE)

    def test_two_identical_copies_collapse_to_one(self):
        repaired, report = deduplicate_page_html(BASE + BASE)
        assert report["strategy"] == "single-copy"
        assert report["copies"] == 2
        assert repaired == BASE
        assert report["after_chars"] < report["before_chars"]

    def test_four_copies_collapse_to_one(self):
        repaired, report = deduplicate_page_html(BASE + BASE + BASE + BASE)
        assert repaired == BASE
        assert report["copies"] == 4

    def test_superset_copy_wins(self):
        # the second copy carries an extra paragraph, so it is the complete one
        repaired, report = deduplicate_page_html(BASE + BASE + EXTRA_Y)
        assert "marker-beta-uart-power" in repaired
        assert report["after_chars"] < report["before_chars"]

    def test_merged_versions_are_grafted_and_verified(self):
        repaired, report = deduplicate_page_html(BASE + EXTRA_X + BASE + EXTRA_Y)
        # both unique paragraphs survive whichever copy was chosen as skeleton
        assert "marker-alpha-kernel-toolchain" in repaired
        assert "marker-beta-uart-power" in repaired

    def test_missing_anchor_is_refused(self):
        with pytest.raises(CannotDeduplicate):
            deduplicate_page_html(BASE)

    def test_unique_lines_are_preserved(self):
        repaired, _ = deduplicate_page_html(BASE + BASE)
        assert repaired.count("第 1 节") == 1
        assert repaired.count("第 130 节") == 1
