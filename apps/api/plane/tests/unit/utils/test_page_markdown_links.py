# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest

from plane.utils.page_markdown_links import has_broken_markdown_links, normalize_markdown_links

ANCHOR = (
    '<a target="_blank" class="text-accent-secondary underline" '
    'href="https://example.com/doc" rel="noopener noreferrer">https://example.com/doc</a>'
)


@pytest.mark.unit
class TestNormalizeMarkdownLinks:
    def test_folds_a_half_converted_link(self):
        html = f"见 [官方文档]({ANCHOR})"
        repaired, count = normalize_markdown_links(html)
        assert count == 1
        # the anchor keeps its attributes; only its inner text becomes the link text
        assert repaired == f"见 {ANCHOR.replace('>https://example.com/doc<', '>官方文档<')}"
        assert "](" not in repaired

    def test_folds_several_links(self):
        html = f"[A]({ANCHOR}) 与 [B]({ANCHOR})"
        repaired, count = normalize_markdown_links(html)
        assert count == 2
        assert repaired.count(">A</a>") == 1
        assert repaired.count(">B</a>") == 1

    def test_keeps_plain_markdown_without_an_anchor(self):
        html = "<p>[文字](plain-text)</p>"
        repaired, count = normalize_markdown_links(html)
        assert count == 0
        assert repaired == html

    def test_keeps_normal_links_untouched(self):
        html = '<p><a href="https://example.com">官方文档</a></p>'
        repaired, count = normalize_markdown_links(html)
        assert count == 0
        assert repaired == html

    def test_requires_an_href_inside_the_anchor(self):
        html = "<p>[文字](<a name='x'>url</a>)</p>"
        repaired, count = normalize_markdown_links(html)
        assert count == 0
        assert repaired == html

    def test_handles_empty_input(self):
        assert normalize_markdown_links("") == ("", 0)

    def test_detection_helper(self):
        assert has_broken_markdown_links(f"[A]({ANCHOR})") is True
        assert has_broken_markdown_links("<p>plain</p>") is False
        assert has_broken_markdown_links("") is False
