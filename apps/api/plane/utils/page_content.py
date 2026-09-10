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

# Third party imports
import requests

# Django imports
from django.conf import settings

# Module imports
from plane.utils.exception_logger import log_exception
from plane.utils.url import normalize_url_path


def convert_page_html_to_formats(description_html: str) -> dict:
    """Convert page HTML into the document editor's JSON and Yjs binary.

    Returns a dict with `description_json` and `description_binary` (base64),
    or an empty dict when the conversion is unavailable / fails.
    """
    if not description_html or "<" not in description_html:
        return {}

    live_url = settings.LIVE_URL
    if not live_url:
        return {}

    try:
        url = normalize_url_path(f"{live_url}/convert-document/")
        headers = {}
        if settings.LIVE_INTERNAL_API_KEY:
            headers["X-Api-Key"] = settings.LIVE_INTERNAL_API_KEY
        response = requests.post(
            url,
            json={"description_html": description_html, "variant": "document"},
            headers=headers,
            timeout=20,
        )
        if response.status_code == 200:
            return response.json()
    except requests.RequestException as e:
        log_exception(e)
    return {}


def sync_page_description_formats(page) -> bool:
    """Backfill `description_binary` / `description_json` for a page that has
    HTML content but no Yjs binary. No-op when a binary is already present.

    Returns True when the page was updated.
    """
    if page.description_binary or not page.description_html:
        return False

    converted = convert_page_html_to_formats(page.description_html)
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
    page.save(update_fields=["description_binary", "description_json"])
    return True
