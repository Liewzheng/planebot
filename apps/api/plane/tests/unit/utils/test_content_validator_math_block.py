# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from bs4 import BeautifulSoup

from plane.utils.content_validator import validate_html_content


def latex_attribute(clean_html: str) -> str | None:
    """Read `data-latex` back the way a browser would parse it.

    nh3 re-serializes after parsing, so comparing raw strings is too strict:
    HTML5 does not require `<` or `>` to be escaped inside a quoted attribute
    value. The meaningful assertion is what the attribute decodes to, plus that
    no new element/attribute can break out of it.
    """
    soup = BeautifulSoup(clean_html, "html.parser")
    tag = soup.find(["math-block", "math-inline"])
    return tag.get("data-latex") if tag else None


@pytest.mark.unit
class TestMathBlockSanitization:
    """Editor math blocks (`<math-block>`) must survive HTML sanitization."""

    def test_math_block_tag_is_preserved(self):
        html = '<p>before</p><math-block data-latex="E = mc^2"></math-block><p>after</p>'
        is_valid, error, clean_html = validate_html_content(html)
        assert is_valid is True
        assert error is None
        assert '<math-block data-latex="E = mc^2">' in clean_html

    def test_math_block_multiline_latex_is_preserved(self):
        latex = "\\begin{aligned}\na &= b \\\\ c &= d\n\\end{aligned}"
        html = f'<math-block data-latex="{latex}"></math-block>'
        is_valid, error, clean_html = validate_html_content(html)
        assert is_valid is True
        assert error is None
        assert "<math-block" in clean_html
        assert 'data-latex="' in clean_html
        assert "aligned" in clean_html

    def test_math_block_escapes_are_not_double_encoded(self):
        """`&lt;` stays a literal `<`, it is not turned into `&amp;lt;`"""
        html = '<math-block data-latex="a &lt; b"></math-block>'
        is_valid, error, clean_html = validate_html_content(html)
        assert is_valid is True
        assert error is None
        assert latex_attribute(clean_html) == "a < b"

    def test_math_block_keeps_an_escaped_entity_literal(self):
        """A literal `&lt;` written as `&amp;lt;` must survive as that text"""
        html = '<math-block data-latex="a &amp;lt; b"></math-block>'
        is_valid, error, clean_html = validate_html_content(html)
        assert is_valid is True
        assert error is None
        assert latex_attribute(clean_html) == "a &lt; b"

    def test_math_block_quotes_in_latex_cannot_break_the_attribute(self):
        html = '<math-block data-latex="say &quot;hi&quot;" onclick="alert(1)"></math-block>'
        is_valid, error, clean_html = validate_html_content(html)
        assert is_valid is True
        assert error is None
        soup = BeautifulSoup(clean_html, "html.parser")
        tag = soup.find("math-block")
        assert tag is not None
        assert tag.get("data-latex") == 'say "hi"'
        # the event handler attribute must not have been smuggled through
        assert tag.get("onclick") is None
        assert "onclick" not in clean_html

    def test_math_block_script_fragment_stays_inside_the_attribute(self):
        """A `<script>` sequence inside the value must stay inert text"""
        html = '<math-block data-latex="&lt;script&gt;alert(1)&lt;/script&gt;"></math-block>'
        is_valid, error, clean_html = validate_html_content(html)
        assert is_valid is True
        assert error is None
        soup = BeautifulSoup(clean_html, "html.parser")
        # it must not become a real script element …
        assert soup.find("script") is None
        # … while the original text is preserved in the attribute
        assert soup.find("math-block").get("data-latex") == "<script>alert(1)</script>"

    def test_math_block_strips_script_attributes(self):
        html = '<math-block data-latex="x" onclick="alert(1)"><script>alert(1)</script></math-block>'
        is_valid, error, clean_html = validate_html_content(html)
        assert is_valid is True
        assert error is None
        assert "onclick" not in clean_html
        assert "<script" not in clean_html
        assert 'data-latex="x"' in clean_html


@pytest.mark.unit
class TestMathInlineSanitization:
    """Editor inline math (`<math-inline>`) must survive HTML sanitization."""

    def test_math_inline_tag_is_preserved(self):
        html = '<p>the equation <math-inline data-latex="x+1"></math-inline> holds</p>'
        is_valid, error, clean_html = validate_html_content(html)
        assert is_valid is True
        assert error is None
        assert '<math-inline data-latex="x+1">' in clean_html

    def test_math_inline_strips_script_attributes(self):
        html = '<math-inline data-latex="x" onmouseover="alert(1)"></math-inline>'
        is_valid, error, clean_html = validate_html_content(html)
        assert is_valid is True
        assert error is None
        assert "onmouseover" not in clean_html
        assert 'data-latex="x"' in clean_html
