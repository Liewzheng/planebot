# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Add AIAccountMigrationRecord — the idempotency marker for the
AIAccount → ServicePrincipal conversion command (M12).

The migration command is expected to run safely on top of an
already-converted database (e.g. a second deploy cycle during the
dual-read observation period). The marker is the single source of truth
for "this AI account has been converted", so the converter can detect
that case without touching the SP module or touching a flag on the SP
model itself.
"""

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ai_accounts", "0002_alter_aiscopepolicy_action_and_more"),
        ("service_principals", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AIAccountMigrationRecord",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, verbose_name="Created At"
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True, verbose_name="Last Modified At"
                    ),
                ),
                (
                    "deleted_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="Deleted At"
                    ),
                ),
                (
                    "id",
                    models.UUIDField(
                        db_index=True,
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        unique=True,
                    ),
                ),
                ("scopes_migrated", models.PositiveIntegerField(default=0)),
                ("grants_migrated", models.PositiveIntegerField(default=0)),
                ("tokens_migrated", models.PositiveIntegerField(default=0)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Last Modified By",
                    ),
                ),
                (
                    "ai_account",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="migration_record",
                        to="ai_accounts.aiaccount",
                    ),
                ),
                (
                    "service_principal",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ai_account_migrations",
                        to="service_principals.serviceprincipal",
                    ),
                ),
            ],
            options={
                "verbose_name": "AI Account Migration Record",
                "verbose_name_plural": "AI Account Migration Records",
                "db_table": "ai_account_migration_records",
                "ordering": ("-created_at",),
            },
        ),
    ]
