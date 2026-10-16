# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Backfill Page.owned_by to the workspace owner when it is currently a bot.

Background
----------
A historical authenticator bug authenticated ``plane_api_`` tokens as their
shadow bot ``User`` (``is_bot=True``). Both ``Page.created_by`` and
``Page.owned_by`` recorded the bot. The carve-out that keeps
``request.user`` as the human owner under ``plane_svc_`` service-principal
tokens only landed on 2026-10-10 (see ``api/views/page.py``); pages
created through the bot path before then still carry the bot on both
columns today.

Why ``owned_by`` matters
------------------------
``Page.owned_by`` is the access-check column: the product refuses to let
anybody but the owner change ``access`` ("Access cannot be updated since
this page is owned by someone else" in
``app/views/page/base.py:236-242``). A bot-owned page therefore cannot
be flipped to private by the human owner — a real, user-visible problem
hidden until today only because all affected pages happened to be
public.

Scope
-----
* Reshapes ``Page.owned_by_id`` from the bot to the workspace's owner
  (``Workspace.owner_id``). The workspace owner is the correct human
  surrogate: every affected workspace's ``owner`` is the human who
  provisioned the bot in the first place, so this gives the same human
  the missing ``access`` control.
* Leaves ``Page.created_by`` alone. It is the attribution column read
  by ``core/authz/engine.py`` and rewriting it would silently change
  what users see as the page's author.
* Leaves ``PageVersion.owned_by`` alone. Those rows record "who saved
  this version" — overwriting history is worse than the bug.
* Leaves ``PageDraft`` alone. Zero rows are bot-owned by design (drafts
  belong to whoever is editing).
* Leaves ``Page.access`` alone. Visibility is the user's choice.

Idempotency
-----------
The forward pass filters on ``owned_by__is_bot=True``. After the first
run, no rows match that predicate, so any subsequent run is a zero-row
update — the SQL is identical, the effect is identical.

Irreversibility
---------------
This is a **one-way** data fix. The original bot FKs have been
overwritten by the workspace-owner's FK; there is no surviving record
of which pages used to be bot-owned. ``reverse_code`` therefore is
``RunPython.noop``: a reverse migration is a no-op. Any "undo" must
come from a backup or by re-identifying bot-owned pages against a pre-
migration snapshot.

Implementation notes
--------------------
* Uses ``apps.get_model`` for historical-model access, so makemigrations
  does not need a current model import (the prior breakage mode).
* Runs as a single ``UPDATE ... FROM workspace`` subquery rather than a
  per-page ``save()``: the production footprint is 73 rows, the test
  fixture can be larger, and a per-row save would fire the model's
  ``save()`` and recompute ``description_stripped``.
"""

from django.db import migrations
from django.db.models import OuterRef, Subquery


def backfill_page_owned_by(apps, schema_editor):
    Page = apps.get_model("db", "Page")
    Workspace = apps.get_model("db", "Workspace")

    Page.objects.filter(owned_by__is_bot=True).update(
        owned_by_id=Subquery(
            Workspace.objects.filter(id=OuterRef("workspace_id")).values(
                "owner_id"
            )[:1]
        )
    )


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0128_apitoken_principal_type_service_principal"),
    ]

    operations = [
        migrations.RunPython(backfill_page_owned_by, migrations.RunPython.noop),
    ]
