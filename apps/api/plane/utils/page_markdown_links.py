# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Repair markdown inline links and images that arrived half-converted.

Markdown clients convert markdown to HTML before sending it (e.g. `pbot doc
create --content-md`). The CLI's converter autolinks bare URLs but does not
understand `[text](url)` or `![alt](url)`, so

    [Teledyne 技术支持文档](https://example.com/doc)
    ![示意图](https://example.com/diagram.png)

reach the API as the literal syntax around an anchor whose text is the URL:

    [Teledyne 技术支持文档](<a … href="https://example.com/doc" …>https://example.com/doc</a>)
    ![示意图](<a … href="https://example.com/diagram.png" …>https://example.com/diagram.png</a>)

This helper rewrites both deterministic shapes into the markup the markdown
meant: a link keeps the anchor and takes the link text, an image becomes
`<img src alt>`. The root fix belongs in the client's converter; this is the
server-side net plus the repair path for content already stored this way.
"""

# Python imports
import re

# `![alt](` or `[text](` + an anchor whose href we keep + the anchor's own
# (URL) text + `)`; the leading `!` distinguishes the image form.
MARKDOWN_LINK_PATTERN = re.compile(
    r"(?P<bang>!?)\[(?P<text>[^\]\n]{1,300})\]\((?P<anchor><a\b[^>]*\bhref=\"(?P<href>[^\"]+)\"[^>]*>)(?P<inner>[^<]*)</a>\)"
)

def has_broken_markdown_links(html: str) -> bool:
    """True when the HTML contains the half-converted markdown link shape."""
    return bool(html) and MARKDOWN_LINK_PATTERN.search(html) is not None


def normalize_markdown_links(description_html: str) -> tuple[str, int]:
    """Fold `[text](<a …>url</a>)` into `<a …>text</a>` and
    `![alt](<a …>url</a>)` into `<img src="url" alt="alt">`.

    Returns `(html, repaired_count)`; the input is returned untouched when
    neither shape is present.
    """
    if not description_html:
        return description_html, 0

    repaired = 0

    def replace(match: re.Match) -> str:
        nonlocal repaired
        repaired += 1
        if match.group("bang"):
            # the markdown asked for an image: keep the target, move the text
            # into the alt attribute (the anchor's inner text was the raw URL).
            # the converter already escaped `&`/`<`/`>` in the text, so only the
            # attribute-breaking quote is neutralized here — escaping again
            # would double-encode the rest
            return '<img src="{}" alt="{}">'.format(
                match.group("href"), match.group("text").replace('"', "&quot;")
            )
        # keep the anchor's attributes (target/class/href/rel) but use the
        # markdown link text — the anchor's inner text was the raw URL
        return f"{match.group('anchor')}{match.group('text')}</a>"

    return MARKDOWN_LINK_PATTERN.sub(replace, description_html), repaired
