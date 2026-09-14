# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest

from plane.utils.page_frontmatter import (
    MAX_FRONTMATTER_CHARS,
    normalize_tags,
    split_frontmatter,
)


def frontmatter_block(body: str) -> str:
    """Markdown clients convert frontmatter into a leading yaml code block."""
    return f'<pre><code class="language-yaml">{body}\n</code></pre>'


@pytest.mark.unit
class TestSplitFrontmatter:
    def test_frontmatter_is_removed_from_body(self):
        html = frontmatter_block('title: "Doc"\ntags: ["plane", "yjs"]') + "<h1>Doc</h1><p>body</p>"
        body, metadata = split_frontmatter(html)
        assert body == "<h1>Doc</h1><p>body</p>"
        assert metadata == {"title": "Doc", "tags": ["plane", "yjs"]}

    def test_body_only_without_frontmatter_is_untouched(self):
        html = "<h1>Doc</h1><p>body</p>"
        body, metadata = split_frontmatter(html)
        assert body == html
        assert metadata == {}

    def test_leading_yaml_code_block_without_known_keys_stays_in_body(self):
        html = frontmatter_block("kind: Deployment\nmetadata: {}") + "<p>k8s sample</p>"
        body, metadata = split_frontmatter(html)
        assert body == html
        assert metadata == {}

    def test_yaml_code_block_later_in_the_document_is_untouched(self):
        html = "<p>intro</p>" + frontmatter_block("tags: [a]") + "<p>tail</p>"
        body, metadata = split_frontmatter(html)
        assert body == html
        assert metadata == {}

    def test_invalid_yaml_is_ignored(self):
        html = frontmatter_block("title: [unclosed") + "<p>body</p>"
        body, metadata = split_frontmatter(html)
        assert body == html
        assert metadata == {}

    def test_oversized_block_is_ignored(self):
        html = frontmatter_block("title: " + "x" * (MAX_FRONTMATTER_CHARS + 10)) + "<p>body</p>"
        body, metadata = split_frontmatter(html)
        assert body == html
        assert metadata == {}

    def test_escaped_quotes_in_frontmatter_are_decoded(self):
        html = frontmatter_block("title: &quot;Doc&quot;\ntags: [&quot;a&quot;]") + "<p>body</p>"
        body, metadata = split_frontmatter(html)
        assert body == "<p>body</p>"
        assert metadata["title"] == "Doc"
        assert metadata["tags"] == ["a"]

    def test_empty_body_after_frontmatter_becomes_empty_paragraph(self):
        body, metadata = split_frontmatter(frontmatter_block("tags: [a]"))
        assert body == "<p></p>"
        assert metadata["tags"] == ["a"]


@pytest.mark.unit
class TestNormalizeTags:
    def test_string_input_is_split_on_commas(self):
        assert normalize_tags("a, b ,, c") == ["a", "b", "c"]

    def test_case_insensitive_duplicates_are_dropped(self):
        assert normalize_tags(["Plane", "plane", "yjs"]) == ["Plane", "yjs"]

    def test_non_string_values_are_coerced_and_bools_dropped(self):
        assert normalize_tags([1, True, None, "x"]) == ["1", "x"]

    def test_none_and_unknown_types(self):
        assert normalize_tags(None) == []
        assert normalize_tags({"a": 1}) == []

    def test_names_are_truncated_to_the_label_column_length(self):
        assert normalize_tags(["y" * 300]) == ["y" * 255]
