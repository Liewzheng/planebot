# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Helpers to keep a page's collaborative-editor formats in sync when content is
written as HTML only (e.g. by the public API / CLI).

Pages are edited with the collaborative document editor, which loads a Yjs
binary (description_binary) through the live service. When a page is created or
updated with only `description_html`, `description_binary` stays null; the live
service then converts the HTML on the fly at first load, and applying that
document after the editor has already mounted crashes the web editor
(React "removeChild of null" in the commit phase).

Converting the HTML to the document JSON + Yjs binary at write time keeps every
page loadable through the normal sync path.
"""

# Python imports
import base64
import binascii
import hashlib
import html as html_module
import re

# Third party imports
import requests

# Django imports
from django.conf import settings

# Module imports
from plane.utils.exception_logger import log_exception
from plane.utils.url import normalize_url_path


def convert_page_html_to_formats(description_html: str, document_name: str | None = None) -> dict:
    """Convert page HTML into the document editor's JSON and Yjs binary.

    `document_name` is written into the Yjs `title` fragment of the binary, so
    the collaborative editor does not have to rebuild the title from scratch.
    Returns a dict with `description_json` and `description_binary` (base64),
    or an empty dict when the conversion is unavailable / fails.
    """
    if not description_html or "<" not in description_html:
        return {}

    live_url = settings.LIVE_URL
    if not live_url:
        return {}

    payload: dict = {"description_html": description_html, "variant": "document"}
    if document_name:
        payload["document_name"] = document_name

    try:
        url = normalize_url_path(f"{live_url}/convert-document/")
        headers = {}
        if settings.LIVE_INTERNAL_API_KEY:
            headers["X-Api-Key"] = settings.LIVE_INTERNAL_API_KEY
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=20,
        )
        if response.status_code == 200:
            return response.json()
        log_exception(Exception(f"convert-document returned {response.status_code}: {response.text[:200]}"))
    except requests.RequestException as e:
        log_exception(e)
    return {}


def _visible_text(description_html: str) -> str:
    """Page body with tags, entities and whitespace removed — what a reader sees.

    Entities are decoded so `&quot;` and `"` read the same: the editor and the
    import path encode the same character differently, which used to look like a
    content change.
    """
    return re.sub(r"\s+", "", html_module.unescape(re.sub(r"<[^>]*>", "", description_html or "")))


def _asset_targets(description_html: str) -> str:
    """Ordered link/image targets, entity-decoded."""
    values = re.findall(r"""(?:src|href)\s*=\s*["']([^"']*)["']""", description_html or "", flags=re.IGNORECASE)
    return "|".join(html_module.unescape(value) for value in values)


def page_content_text_length(description_html: str) -> int:
    """Visible-text length of a page body.

    Mirrors `contentTextLength` in the live server (apps/live/src/utils/
    content-replacement.ts): the two sides of the "was content dropped?" check
    must measure the same thing.
    """
    return len(_visible_text(description_html))


def page_content_fingerprint(description_html: str) -> str:
    """Content identity of a page body, ignoring how the editor serialized it.

    The same content is written in different flavours: the collaborative editor
    emits markup with presentation attributes (`class`, `style`, `id`, …) while
    the markdown/CLI import path emits plain tags. Comparing `description_html`
    byte-for-byte therefore records "edits" that changed nothing a reader would
    see, which is how a page opened without being touched gained a page version.

    The fingerprint keeps what a reader sees — the tag sequence (headings,
    tables, lists), the visible text, and link/image targets — and drops every
    attribute, so presentation-only differences collide. Trade-off: an edit that
    changes nothing but an attribute (a table cell span, a text colour) is not
    treated as a content change.
    """
    html_body = description_html or ""
    tags = "|".join(re.findall(r"<\s*([a-zA-Z][a-zA-Z0-9]*)", html_body))
    return hashlib.sha256(f"{tags}\n{_asset_targets(html_body)}\n{_visible_text(html_body)}".encode()).hexdigest()


MIN_RE_SERIALIZATION_LENGTH = 1000


def content_is_unchanged(stored_html: str | None, incoming_html: str | None) -> bool:
    """True when an incoming write says the same thing as what is stored.

    Page content is written by several clients (the editor's save button, the
    public API / CLI, a version restore). Every one of them re-serializes the
    content it received, so a byte comparison reports a change nobody can see
    and the write lands as a revision attributed to whoever sent it. Content
    identity ignores the serialization; an empty incoming body is never a
    no-op, so a client cannot blank a page by omitting it.
    """
    if not incoming_html:
        return False
    return page_content_fingerprint(stored_html) == page_content_fingerprint(incoming_html)


DUPLICATED_BLOCK_MIN_LENGTH = 600


def duplicated_block_length(description_html: str | None, min_length: int | None = None) -> int:
    """Length of the longest block of body text the body repeats verbatim.

    A page ballooned by a stale client's union-merge holds its own content
    twice, so a large block of it appears twice. A document that merely repeats
    a sentence or a table row does not repeat a quarter of itself. Returns 0
    when nothing that long repeats.
    """
    text = _visible_text(description_html)
    if min_length is None:
        min_length = max(DUPLICATED_BLOCK_MIN_LENGTH, len(text) // 4)
    if len(text) < min_length * 2:
        return 0

    # Rolling hash over a window the size of the smallest block we care about,
    # so every offset is compared (a fixed stride misses the second copy when
    # it starts between two sampled positions).
    window = max(min_length, len(text) // 8)
    mask = (1 << 64) - 1
    base = 1_000_003
    high = pow(base, window - 1, 1 << 64)
    digest = 0
    for index in range(window):
        digest = (digest * base + ord(text[index])) & mask

    seen = {digest: 0}
    for index in range(1, len(text) - window + 1):
        digest = ((digest - ord(text[index - 1]) * high) * base + ord(text[index + window - 1])) & mask
        previous = seen.get(digest)
        if previous is not None:
            # the hashes match: measure how far the two blocks really agree
            length = window
            while (
                previous + length < len(text)
                and index + length < len(text)
                and text[previous + length] == text[index + length]
            ):
                length += 1
            if length >= min_length:
                return length
        seen[digest] = index
    return 0


def page_content_is_re_serialization(previous_html: str, current_html: str) -> bool:
    """True when the two bodies say the same thing in different markup.

    The collaborative store round-trips a page through Yjs, which re-encodes
    entities (`&quot;` → `"`) and splits text nodes around marks, moving a
    handful of characters (observed: 21 of 5998) without changing the content.
    Recording that as an edit attributed a change to whoever happened to have the
    page open. Targets must match, so swapping an image is still an edit; a text
    change within one percent of the body is treated as the same content.
    """
    if _asset_targets(previous_html) != _asset_targets(current_html):
        return False
    previous_text = _visible_text(previous_html)
    current_text = _visible_text(current_html)
    if previous_text == current_text:
        return True
    # Only long bodies get a tolerance: on a short page a couple of characters is
    # a real edit, while the round-trip difference grows with the body.
    longest = max(len(previous_text), len(current_text))
    if longest < MIN_RE_SERIALIZATION_LENGTH:
        return False
    return abs(len(previous_text) - len(current_text)) <= max(20, int(0.01 * longest))


def invalidate_live_document(page_id: str, content_text_length: int | None = None) -> None:
    """Drop a page's collaborative document from the live server's memory.

    Called after `description_binary` was replaced in the database (overwrite
    semantics for API/CLI uploads). Without this, the live server keeps
    serving and re-persisting its stale in-memory copy, and connected clients
    merge the old content back in (yjs union semantics) — the page balloons
    on every re-upload.

    `content_text_length` arms the live server's shrink guard: until the
    replacement is part of the shared document, a store that drops most of it
    (a stale client deleting the fresh content) is refused and the clients are
    closed so they reload from the database.

    Best-effort: any failure is logged and swallowed, the API write itself
    has already succeeded at this point.
    """
    live_url = settings.LIVE_URL
    if not live_url:
        return

    try:
        url = normalize_url_path(f"{live_url}/invalidate-document/")
        headers = {}
        if settings.LIVE_INTERNAL_API_KEY:
            headers["X-Internal-Api-Key"] = settings.LIVE_INTERNAL_API_KEY
        payload: dict = {"docId": str(page_id)}
        if content_text_length is not None:
            payload["contentTextLength"] = int(content_text_length)
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code != 200:
            log_exception(
                Exception(f"invalidate-document returned {response.status_code}: {response.text[:200]}")
            )
    except requests.RequestException as e:
        log_exception(e)


def sync_page_description_formats(page, force: bool = False) -> bool:
    """Backfill `description_binary` / `description_json` for a page written as
    HTML only.

    `force=True` regenerates the formats even when a binary already exists —
    used when `description_html` changes through an endpoint whose serializer
    does not carry the binary, so the collaborative editor does not keep
    serving the previous document. Returns True when the page was updated.
    """
    if not page.description_html:
        return False
    if page.description_binary and not force:
        return False

    converted = convert_page_html_to_formats(page.description_html, page.name)
    encoded_binary = converted.get("description_binary")
    if not encoded_binary:
        return False

    try:
        binary = base64.b64decode(encoded_binary)
    except (binascii.Error, ValueError):
        return False

    page.description_binary = binary
    if converted.get("description_json"):
        page.description_json = converted["description_json"]
    update_fields = ["description_binary", "description_json"]

    # Store the editor's own serialization of the content, so a client that opens
    # the page has nothing to rewrite. The client used to normalize this markup on
    # open, and that write landed as a revision authored by whoever merely had the
    # page open (PLANE-76). Only adopt the canonical html when the conversion kept
    # every character of the original, so importing cannot silently lose content.
    canonical_html = converted.get("description_html")
    if canonical_html and _visible_text(canonical_html) == _visible_text(page.description_html):
        page.description_html = canonical_html
        update_fields.append("description_html")

    page.save(update_fields=update_fields)

    # The binary in the database has been replaced: drop the live server's
    # in-memory copy (and connected clients' stale local state) so the new
    # content is what gets served and synced from now on.
    invalidate_live_document(page.id, page_content_text_length(page.description_html))
    return True
