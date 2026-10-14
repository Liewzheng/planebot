# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Give every pre-existing service principal its shadow bot user.

0004 added ``ServicePrincipal.bot_user``; this migration fills it in for the
SPs that already existed, so attribution does not depend on a lazy first use
(and so an ownership check can rely on ``bot_user`` being set for every live
SP — a NULL there must be read as "no attribution target", never as "matches
another NULL").

Two sources, in priority order:

1. **An inherited bot user.** An SP converted from an ``AIAccount`` by
   ``migrate_ai_accounts_to_service_principals`` already had a bot user, and
   rows that bot wrote *before* the conversion are already attributed to it.
   Re-linking that same user keeps "this SP created that" true across the
   migration instead of orphaning its history behind a fresh bot.
   ``bot_type`` is deliberately left as it was (``AI_AGENT``): it is still a
   recognisable bot, and re-typing it would move the row between the
   visibility predicates other surfaces already key on. Only ``display_name``
   is refreshed to the ``svc-<name> (bot)`` convention.
2. **A new bot user**, matching ``attribution.ensure_bot_user`` exactly
   (``is_bot=True``, ``bot_type=SERVICE_PRINCIPAL``, ``username``
   ``bot_user_<spid>``, unusable ``bot_user_<spid>@<host>`` address) — so a
   later lazy call finds this row instead of creating a second one.

Neither path creates a ``WorkspaceMember`` / ``ProjectMember`` row: the bot is
an attribution target, not a member.

Reverse is an intentional no-op: unlinking would strip attribution targets
from live SPs and re-creating them is exactly what a re-apply does.
"""

import uuid
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import migrations


def _bot_host():
    return urlparse(settings.WEB_URL or "https://plane.so").hostname or "plane.so"


def _display_name(sp_name):
    return f"svc-{sp_name} (bot)"


def _first_name(sp_name):
    return f"svc-{sp_name}"


def backfill_bot_users(apps, schema_editor):
    ServicePrincipal = apps.get_model("service_principals", "ServicePrincipal")
    User = apps.get_model("db", "User")
    MigrationRecord = apps.get_model("ai_accounts", "AIAccountMigrationRecord")

    db_alias = schema_editor.connection.alias

    inherited = {
        sp_id: bot_id
        for sp_id, bot_id in MigrationRecord.objects.using(db_alias)
        .filter(ai_account__bot_user_id__isnull=False)
        .values_list("service_principal_id", "ai_account__bot_user_id")
    }

    for sp_id, sp_name in (
        ServicePrincipal.objects.using(db_alias)
        .filter(bot_user__isnull=True, deleted_at__isnull=True)
        .values_list("id", "name")
    ):
        bot_id = inherited.get(sp_id)
        if bot_id is None:
            username = f"bot_user_{sp_id}"
            bot = User.objects.using(db_alias).filter(username=username).first()
            if bot is None:
                bot = User.objects.using(db_alias).create(
                    username=username,
                    display_name=_display_name(sp_name),
                    first_name=_first_name(sp_name),
                    last_name="Bot",
                    email=f"{username}@{_bot_host()}",
                    is_bot=True,
                    bot_type="SERVICE_PRINCIPAL",
                    is_active=True,
                    password=make_password(uuid.uuid4().hex),
                    is_password_autoset=True,
                )
            bot_id = bot.id
        else:
            User.objects.using(db_alias).filter(id=bot_id).update(
                display_name=_display_name(sp_name)
            )

        ServicePrincipal.objects.using(db_alias).filter(id=sp_id).update(
            bot_user_id=bot_id
        )


class Migration(migrations.Migration):

    dependencies = [
        ("service_principals", "0004_serviceprincipal_bot_user"),
        ("ai_accounts", "0003_aiaccountmigrationrecord"),
        ("db", "0128_apitoken_principal_type_service_principal"),
    ]

    operations = [
        migrations.RunPython(backfill_bot_users, migrations.RunPython.noop),
    ]
