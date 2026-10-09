# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""APIToken gets ``principal_type`` and a nullable FK to ``ServicePrincipal``.

User tokens keep ``principal_type=0`` and ``service_principal=NULL``; service
tokens are ``principal_type=1`` with the FK set. The FK is added before the
service_principals app migration lands so the column has a target to point
at.
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0125_page_frontmatter_alter_fileasset_entity_type"),
        ("service_principals", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="apitoken",
            name="principal_type",
            field=models.PositiveSmallIntegerField(
                choices=[(0, "User"), (1, "Service")],
                default=0,
            ),
        ),
        migrations.AddField(
            model_name="apitoken",
            name="service_principal",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="tokens",
                to="service_principals.serviceprincipal",
            ),
        ),
    ]