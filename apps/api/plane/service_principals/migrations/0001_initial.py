# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Service Principal foundation: SP / scope / grant / workspace settings tables.

Schema choices:

- ``service_principals`` is a separate principal table; it has no User link
  beyond ``owner`` and never appears in member pickers or seat counts.
- ``service_principal_scopes`` carries the per-resource action set. A row
  with ``project_id NULL`` is workspace-wide; the unique-together on
  (sp, project, resource_type, action, deleted_at) follows the same shape as
  AIScopePolicy.
- ``service_principal_project_grants`` is the per-project opt-in: presence
  of an active row means the SP may reach that project, with ``role_cap`` as
  the action-role upper bound. Default-deny holds because the SP has no
  ProjectMember rows anywhere.
- ``workspace_sp_settings`` is a single-row-per-workspace toggle carrier,
  created lazily. Only flag carried today is ``sp_assignable`` (default off).
"""

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("db", "0125_page_frontmatter_alter_fileasset_entity_type"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="WorkspaceSPSettings",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Created At"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Last Modified At"),
                ),
                (
                    "deleted_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="Deleted At"),
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
                ("sp_assignable", models.BooleanField(default=False)),
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
                    "workspace",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sp_settings",
                        to="db.workspace",
                    ),
                ),
            ],
            options={
                "verbose_name": "Workspace SP Settings",
                "verbose_name_plural": "Workspace SP Settings",
                "db_table": "workspace_sp_settings",
                "ordering": ("-created_at",),
            },
        ),
        migrations.CreateModel(
            name="ServicePrincipal",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Created At"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Last Modified At"),
                ),
                (
                    "deleted_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="Deleted At"),
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
                ("name", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True, default="")),
                ("avatar", models.CharField(blank=True, default="", max_length=800)),
                ("is_active", models.BooleanField(default=True)),
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
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="owned_service_principals",
                        to=settings.AUTH_USER_MODEL,
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
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="service_principals",
                        to="db.workspace",
                    ),
                ),
            ],
            options={
                "verbose_name": "Service Principal",
                "verbose_name_plural": "Service Principals",
                "db_table": "service_principals",
                "ordering": ("-created_at",),
            },
        ),
        migrations.CreateModel(
            name="ServiceScope",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Created At"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Last Modified At"),
                ),
                (
                    "deleted_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="Deleted At"),
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
                (
                    "resource_type",
                    models.CharField(
                        choices=[
                            ("project", "Project"),
                            ("member", "Member"),
                            ("user", "User"),
                            ("asset", "Asset"),
                            ("estimate", "Estimate"),
                            ("cycle", "Cycle"),
                            ("module", "Module"),
                            ("sticky", "Sticky"),
                            ("label", "Label"),
                            ("intake", "Intake"),
                            ("work_item", "Work Item"),
                            ("comment", "Comment"),
                            ("state", "State"),
                            ("page", "Page"),
                            ("invite", "Invite"),
                        ],
                        max_length=50,
                    ),
                ),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("read", "Read"),
                            ("create", "Create"),
                            ("update", "Update"),
                            ("delete", "Delete"),
                        ],
                        max_length=20,
                    ),
                ),
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
                    "project",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sp_scopes",
                        to="db.project",
                    ),
                ),
                (
                    "service_principal",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="scopes",
                        to="service_principals.serviceprincipal",
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
            ],
            options={
                "verbose_name": "Service Scope",
                "verbose_name_plural": "Service Scopes",
                "db_table": "service_principal_scopes",
                "ordering": ("-created_at",),
                "unique_together": {
                    ("service_principal", "project", "resource_type", "action", "deleted_at")
                },
            },
        ),
        migrations.CreateModel(
            name="ProjectGrant",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Created At"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Last Modified At"),
                ),
                (
                    "deleted_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="Deleted At"),
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
                (
                    "role_cap",
                    models.PositiveSmallIntegerField(
                        choices=[(20, "Admin"), (15, "Member"), (5, "Guest")],
                        default=15,
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
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
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sp_grants",
                        to="db.project",
                    ),
                ),
                (
                    "service_principal",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="grants",
                        to="service_principals.serviceprincipal",
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
            ],
            options={
                "verbose_name": "Project Grant",
                "verbose_name_plural": "Project Grants",
                "db_table": "service_principal_project_grants",
                "ordering": ("-created_at",),
                "unique_together": {("service_principal", "project", "deleted_at")},
            },
        ),
    ]