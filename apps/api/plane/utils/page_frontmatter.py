# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
YAML frontmatter support for pages written from markdown.

Markdown clients (e.g. `pbot doc create --content-md`) convert markdown to HTML
client-side, so a leading frontmatter block reaches the API as the first element
of `description_html`:

    <pre><code class="language-yaml">title: "..."
tags: ["a", "b"]
</code></pre><h1>...

The helpers below pull that block out of the body so it is not rendered as page
content, and map the supported `tags` field onto project labels attached to the
page (`PageLabel`). Only `tags` is mapped today; the remaining keys are parsed
and returned for future use.
"""

# Python imports
import html as html_module
import re

# Third party imports
import yaml

# Django imports
from django.db import IntegrityError, transaction

# Module imports
from plane.db.models import Label, PageLabel

# A leading fenced YAML block, as produced by the markdown clients
_FRONTMATTER_BLOCK = re.compile(
    r"^\s*<pre><code(?:\s+class=\"(?:language-yaml|language-yml)\")?>(?P<body>.*?)</code></pre>\s*",
    re.DOTALL,
)

# Only treat a leading YAML block as frontmatter when it carries at least one of
# these keys; a page whose first block is an ordinary yaml code sample (e.g.
# `kind: Deployment`) must stay in the body.
KNOWN_FRONTMATTER_KEYS = {
    "title",
    "tags",
    "status",
    "created",
    "updated",
    "verified",
    "category",
    "date",
    "author",
    "aliases",
}

MAX_FRONTMATTER_CHARS = 4000
MAX_TAGS = 30
LABEL_NAME_MAX_LENGTH = 255
DEFAULT_LABEL_COLOR = "#3f76ff"


def split_frontmatter(description_html: str) -> tuple[str, dict]:
    """Split a leading YAML frontmatter block off page HTML.

    Returns `(body_html, metadata)`; when the input has no recognizable
    frontmatter the original HTML is returned with an empty metadata dict.
    """
    if not description_html:
        return description_html, {}

    match = _FRONTMATTER_BLOCK.match(description_html)
    if not match:
        return description_html, {}

    raw = html_module.unescape(match.group("body")).strip()
    if not raw or len(raw) > MAX_FRONTMATTER_CHARS or ":" not in raw:
        return description_html, {}

    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError:
        return description_html, {}

    if not isinstance(loaded, dict) or not loaded:
        return description_html, {}

    metadata = {str(key).strip().lower(): value for key, value in loaded.items() if str(key).strip()}
    if not set(metadata) & KNOWN_FRONTMATTER_KEYS:
        return description_html, {}

    body = description_html[match.end() :]
    return (body or "<p></p>"), metadata


def normalize_tags(raw) -> list[str]:
    """Normalize the `tags` frontmatter value into a clean list of names.

    Accepts a YAML list or a comma/newline separated string; drops empties and
    case-insensitive duplicates, and bounds both the count and the name length
    (the label name column is 255 chars).
    """
    if raw is None:
        return []

    if isinstance(raw, str):
        items = re.split(r"[,\n]", raw)
    elif isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        return []

    tags: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, (str, int, float)) or isinstance(item, bool):
            continue
        name = str(item).strip()[:LABEL_NAME_MAX_LENGTH]
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        tags.append(name)
        if len(tags) >= MAX_TAGS:
            break
    return tags


def sync_tags_to_page_labels(page, tags: list[str], project_id) -> list[Label]:
    """Attach the frontmatter tags to a page as project labels.

    Labels are project scoped: an existing label with the same name is reused,
    a missing one is created. Existing page labels are kept — tags only add.
    """
    if not tags or not project_id:
        return []

    labels: list[Label] = []
    for name in tags:
        label = Label.objects.filter(
            name__iexact=name,
            project_id=project_id,
            deleted_at__isnull=True,
        ).first()
        if label is None:
            try:
                with transaction.atomic():
                    label = Label.objects.create(
                        name=name,
                        project_id=project_id,
                        workspace_id=page.workspace_id,
                        color=DEFAULT_LABEL_COLOR,
                        created_by_id=page.created_by_id,
                        updated_by_id=page.updated_by_id,
                    )
            except IntegrityError:
                # another request created the same label concurrently
                label = Label.objects.filter(
                    name__iexact=name,
                    project_id=project_id,
                    deleted_at__isnull=True,
                ).first()
        if label is not None:
            labels.append(label)

    if not labels:
        return []

    existing_label_ids = set(PageLabel.objects.filter(page=page).values_list("label_id", flat=True))
    PageLabel.objects.bulk_create(
        [
            PageLabel(
                label=label,
                page=page,
                workspace_id=page.workspace_id,
                created_by_id=page.created_by_id,
                updated_by_id=page.updated_by_id,
            )
            for label in labels
            if label.id not in existing_label_ids
        ],
        batch_size=10,
        ignore_conflicts=True,
    )
    return labels
