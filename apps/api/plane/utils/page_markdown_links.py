# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Repair markdown inline links that arrived half-converted.

Markdown clients convert markdown to HTML before sending it (e.g. `pbot doc
create --content-md`). The CLI's converter autolinks bare URLs but does not
understand `[text](url)`, so

    [Teledyne 技术支持文档](https://example.com/doc)

reaches the API as the literal text `[Teledyne 技术支持文档](` followed by an
anchor whose text is the URL, and a stray `)`:

    [Teledyne 技术支持文档](<a … href="https://example.com/doc" …>https://example.com/doc</a>)

This helper rewrites that deterministic shape into the anchor the markdown
meant: the link text becomes the anchor's text and the surrounding syntax
disappears. The root fix belongs in the client's converter; this is the
server-side net plus the repair path for content already stored this way.
"""

# Python imports
import re

# `[text](` + an anchor whose href we keep + the anchor's own (URL) text + `)`
MARKDOWN_LINK_PATTERN = re.compile(
    r"\[(?P<text>[^\]\n]{1,300})\]\((?P<anchor><a\b[^>]*\bhref=\"(?P<href>[^\"]+)\"[^>]*>)(?P<inner>[^<]*)</a>\)"
)


def has_broken_markdown_links(html: str) -> bool:
    """True when the HTML contains the half-converted markdown link shape."""
    return bool(html) and MARKDOWN_LINK_PATTERN.search(html) is not None


def normalize_markdown_links(description_html: str) -> tuple[str, int]:
    """Fold `[text](<a …>url</a>)` into `<a …>text</a>`.

    Returns `(html, repaired_count)`; the input is returned untouched when the
    shape is absent.
    """
    if not description_html:
        return description_html, 0

    repaired = 0

    def replace(match: re.Match) -> str:
        nonlocal repaired
        repaired += 1
        # keep the anchor's attributes (target/class/href/rel) but use the
        # markdown link text — the anchor's inner text was the raw URL
        return f"{match.group('anchor')}{match.group('text')}</a>"

    return MARKDOWN_LINK_PATTERN.sub(replace, description_html), repaired
