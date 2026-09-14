# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Detection and repair helpers for the "page content duplicated" failure mode.

When a client reconnects with a stale copy of a document (IndexedDB cache or a
long-lived tab), Yjs takes the union of both states and the whole page ends up
in the document two or more times — the body grows on every reconnect. The same
signature is used by the live server's store guard (see
`apps/live/src/utils/document-duplication.ts`); the helpers here run the same
check inside the API and can rebuild a single-copy version of the HTML.

The repair keeps one copy: it prefers a copy that already contains every long
line of the document and, when no single copy does (versions merged together),
grafts the missing blocks from the other copies and verifies the result still
contains all of them.
"""

# Python imports
import html as html_module
import re
from html.parser import HTMLParser

DEFAULT_MIN_LINES = 120
DEFAULT_MIN_UNIQUE_RATIO = 0.6
ANCHOR_SAMPLE_CHARS = 40

# Tags that never open a block
_VOID_TAGS = {
    "br",
    "img",
    "hr",
    "input",
    "meta",
    "link",
    "source",
    "col",
    "area",
    "base",
    "embed",
    "param",
    "track",
    "wbr",
}


class CannotDeduplicate(Exception):
    """Raised when a duplicated document cannot be rebuilt safely."""


def _plain_text(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "\n", text)
    return html_module.unescape(text)


def _normalize_line(value: str) -> str:
    """Normalize a single line: collapse whitespace, drop punctuation.

    A leading list marker is dropped as well: the same item is stored as
    "1. item" inside a list block and as "1item" when a merge flattened the
    list, and both must compare equal.
    """
    value = re.sub(r"[\s│|`]+", " ", value).strip()
    value = re.sub(r"^(?:[-*•·]|\d{1,3}[.、)）]?)\s*", "", value)
    value = re.sub(r"[，。；：、,.;:!?！？（）()「」【】\-—…·\"']+", "", value)
    return value.strip().lower()


def _normalize(value: str, keep_punctuation: bool = False) -> str:
    """Normalize text for comparison: drop markup noise and whitespace."""
    value = _plain_text(value) if "<" in value else value
    if keep_punctuation:
        return re.sub(r"\s+", " ", value).strip().lower()
    return _normalize_line(value)


def document_lines(html: str, min_length: int = 20) -> list[str]:
    """Long text lines of a document (the unit used for duplication checks).

    Lines are derived before whitespace collapsing, so each editor block is one
    line.
    """
    lines = []
    for raw_line in _plain_text(html).split("\n"):
        line = _normalize_line(raw_line)
        if len(line) > min_length:
            lines.append(line)
    return lines


def line_stats(html: str, min_length: int = 20) -> dict:
    lines = document_lines(html, min_length=min_length)
    unique = len(set(lines))
    return {
        "total_lines": len(lines),
        "unique_lines": unique,
        "unique_ratio": round(unique / len(lines), 3) if lines else 1.0,
    }


def is_document_duplicated(
    html: str,
    min_lines: int = DEFAULT_MIN_LINES,
    min_unique_ratio: float = DEFAULT_MIN_UNIQUE_RATIO,
) -> bool:
    """True when a document looks like a union-merged (duplicated) page."""
    stats = line_stats(html)
    if stats["total_lines"] < min_lines:
        return False
    return stats["unique_ratio"] < min_unique_ratio


def assert_not_duplicated(html: str) -> None:
    """Raise when incoming content looks like a union-merged document.

    Used by the write paths so a duplicated body can never be persisted; the
    caller maps it to a `PAGE_CONTENT_DUPLICATED` response.
    """
    if is_document_duplicated(html):
        stats = line_stats(html)
        raise CannotDeduplicate(
            "PAGE_CONTENT_DUPLICATED: %d long lines but only %d unique (ratio %.2f)"
            % (stats["total_lines"], stats["unique_lines"], stats["unique_ratio"])
        )


def _top_level_blocks(html: str) -> list[tuple[int, int]]:
    """Source spans of the document's top-level elements."""

    class _BlockParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=False)
            self.depth = 0
            self.start: int | None = None
            self.spans: list[tuple[int, int]] = []
            self.line_offsets = [0]
            for match in re.finditer("\n", html):
                self.line_offsets.append(match.end())

        def _position(self) -> int:
            line, column = self.getpos()
            return self.line_offsets[line - 1] + column

        def _opening_tag_end(self) -> int:
            return self.rawdata.find(">", self._position()) + 1

        def handle_starttag(self, tag, attrs):
            if self.depth == 0:
                self.start = self._position()
            if tag not in _VOID_TAGS:
                self.depth += 1

        def handle_startendtag(self, tag, attrs):
            if self.depth == 0:
                self.spans.append((self._position(), self._opening_tag_end()))

        def handle_endtag(self, tag):
            if self.depth > 0:
                self.depth -= 1
                if self.depth == 0 and self.start is not None:
                    self.spans.append((self.start, self._opening_tag_end()))

    parser = _BlockParser()
    parser.feed(html)
    return parser.spans


def _snap_to_block_start(offset: int, blocks: list[tuple[int, int]]) -> int:
    """Move an offset back to the start of the top-level block containing it.

    The anchor lives inside the copy's first block, so cutting at the raw offset
    would leave a half block at the end of the previous copy.
    """
    candidate = offset
    for start, end in blocks:
        if start >= offset:
            break
        if start <= offset < end:
            candidate = start
            break
    return candidate


def _copy_boundaries(html: str) -> list[tuple[int, int]]:
    """Split a duplicated document into its copies.

    The copies are consecutive and each starts with the same text, so the
    boundaries are the occurrences of an anchor taken from the document's own
    opening lines, snapped to the enclosing top-level block.
    """
    lines = [line for line in _plain_text(html).split("\n") if len(line.strip()) > 20]
    if len(lines) < 2:
        raise CannotDeduplicate("document has too little text to locate copies")

    anchor = max(lines[:8], key=len).strip()[:ANCHOR_SAMPLE_CHARS]
    positions = [match.start() for match in re.finditer(re.escape(anchor), html)]
    if len(positions) < 2:
        raise CannotDeduplicate(f"anchor {anchor[:40]!r} appears only once — not a whole-document duplication")

    blocks = _top_level_blocks(html)
    snapped = [_snap_to_block_start(position, blocks) for position in positions]
    boundaries = [0] + sorted({offset for offset in snapped if offset > 0}) + [len(html)]
    return [(boundaries[index], boundaries[index + 1]) for index in range(len(boundaries) - 1)]


def _missing_lines(document_lines_set: set[str], candidate_lines: set[str]) -> list[str]:
    """Lines of the document that the candidate does not contain.

    Membership is exact on normalized lines: a substring hit would let a short
    line be "covered" by a longer one and hide missing content.
    """
    return [line for line in document_lines_set if line not in candidate_lines]


def _raw_line_for(line: str, html: str) -> str | None:
    """The original (un-normalized) text of a line, to append it verbatim."""
    for raw_line in _plain_text(html).split("\n"):
        normalized = _normalize_line(raw_line)
        if normalized == line and raw_line.strip():
            return raw_line.strip()
    return None


def _source_block_for(line: str, copies: list[dict]) -> str | None:
    """The top-level block (verbatim source) whose own lines carry this line."""
    for copy in copies:
        for start, end in _top_level_blocks(copy["html"]):
            block = copy["html"][start:end]
            if line in document_lines(block):
                return block
    return None


def deduplicate_page_html(html: str) -> tuple[str, dict]:
    """Rebuild a single-copy version of a duplicated page.

    Returns `(repaired_html, report)`. Raises `CannotDeduplicate` when no safe
    reconstruction exists, so a caller never writes a lossy document.
    """
    if not html:
        raise CannotDeduplicate("empty document")

    stats = line_stats(html)
    if not is_document_duplicated(html):
        raise CannotDeduplicate("document does not look duplicated")

    document_line_set = set(document_lines(html))
    copies = [{"start": start, "end": end, "html": html[start:end]} for start, end in _copy_boundaries(html)]
    for index, copy in enumerate(copies):
        copy["index"] = index
        copy["lines"] = set(document_lines(copy["html"]))
        copy["missing"] = _missing_lines(document_line_set, copy["lines"])
        copy["chars"] = len(copy["html"])

    copies.sort(key=lambda copy: (len(copy["missing"]), -copy["chars"]))
    chosen = copies[0]

    if not chosen["missing"]:
        report = {
            "strategy": "single-copy",
            "copies": len(copies),
            "chosen_copy": chosen["index"],
            "before_chars": len(html),
            "after_chars": chosen["chars"],
            "stats": stats,
        }
        return chosen["html"], report

    # Versions were merged: keep the most complete copy and graft only the
    # blocks whose content is genuinely absent from it. A block that is merely a
    # reformatted variant (list markers, table cells split differently, <br>
    # placement) is already represented and must not be appended, or the page
    # ends up showing the same content twice in two shapes.
    skeleton = chosen["html"]
    skeleton_lines = set(document_lines(skeleton))
    grafted: list[str] = []
    for copy in copies[1:]:
        for start, end in _top_level_blocks(copy["html"]):
            block = copy["html"][start:end]
            block_lines = document_lines(block)
            if not block_lines:
                continue
            missing_in_block = _missing_lines(set(block_lines), skeleton_lines)
            # a block that is merely a reformatted variant is already
            # represented; only graft blocks whose content is genuinely absent
            if len(missing_in_block) < max(1, len(block_lines) // 2):
                continue
            skeleton_lines.update(block_lines)
            grafted.append(block)

    merged = skeleton + "".join(grafted)

    # Final pass: grafted blocks were filtered to avoid variant noise, so a few
    # genuinely-absent lines may remain. Append their source blocks verbatim -
    # a repair must never drop text.
    appended: list[str] = []
    remaining = _missing_lines(document_line_set, set(document_lines(merged)))
    for line in remaining:
        source = _source_block_for(line, copies)
        if source is None:
            # the line only exists as the concatenation of adjacent blocks:
            # keep its original text (normalized when no raw match exists) as
            # its own paragraph so no text is dropped
            raw_line = _raw_line_for(line, html) or line
            source = f'<p class="editor-paragraph-block">{html_module.escape(raw_line)}</p>'
        if source is None or source in appended:
            continue
        appended.append(source)
    merged_with_fragments = merged + "".join(appended)

    still_missing = _missing_lines(document_line_set, set(document_lines(merged_with_fragments)))
    if still_missing:
        raise CannotDeduplicate(
            "cannot rebuild a lossless copy — %d long line(s) would be lost, e.g. %r"
            % (len(still_missing), still_missing[0][:80])
        )

    merged = merged_with_fragments

    report = {
        "strategy": "union-merge",
        "copies": len(copies),
        "chosen_copy": chosen["index"],
        "grafted_blocks": len(grafted),
        "appended_fragments": len(appended),
        "before_chars": len(html),
        "after_chars": len(merged),
        "stats": stats,
    }
    return merged, report
