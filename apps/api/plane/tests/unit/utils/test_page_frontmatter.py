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


@pytest.mark.unit
class TestJsonSafeMetadata:
    def test_dates_become_iso_strings(self):
        import datetime

        from plane.utils.page_frontmatter import json_safe_metadata

        metadata = {
            "created": datetime.date(2026, 9, 14),
            "verified": datetime.datetime(2026, 9, 14, 5, 30),
            "tags": ["a", {"nested": datetime.date(2026, 1, 2)}],
            "count": 3,
        }
        assert json_safe_metadata(metadata) == {
            "created": "2026-09-14",
            "verified": "2026-09-14T05:30:00",
            "tags": ["a", {"nested": "2026-01-02"}],
            "count": 3,
        }

    def test_created_is_filled_from_the_page_when_missing(self):
        import datetime

        from plane.utils.page_frontmatter import with_created_date

        created_at = datetime.datetime(2026, 9, 1, 12, 0)
        assert with_created_date({"tags": ["a"]}, created_at) == {"tags": ["a"], "created": "2026-09-01"}
        assert with_created_date({"created": "2020-01-01"}, created_at) == {"created": "2020-01-01"}
        assert with_created_date({"tags": []}, None) == {"tags": []}
