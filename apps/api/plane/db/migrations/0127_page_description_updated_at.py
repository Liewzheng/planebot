# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only

import django.utils.timezone
from django.db import migrations, models


def backfill_description_updated_at(apps, schema_editor):
    """The field is brand new: existing pages get the newest known content-write
    time — the newest page version when one exists, otherwise the creation —
    so a publish base from before a content write is still refused, while a
    property-only change (rename, access) does not falsely refuse a publish.
    """
    Page = apps.get_model("db", "Page")
    PageVersion = apps.get_model("db", "PageVersion")

    for page in Page.objects.filter(description_updated_at__isnull=True).iterator():
        latest_version = (
            PageVersion.objects.filter(page_id=page.id).order_by("-last_saved_at").values("last_saved_at").first()
        )
        page.description_updated_at = latest_version["last_saved_at"] if latest_version else page.created_at
        page.save(update_fields=["description_updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0126_pagedraft"),
    ]

    operations = [
        migrations.AddField(
            model_name="page",
            name="description_updated_at",
            field=models.DateTimeField(default=django.utils.timezone.now, null=True),
        ),
        migrations.RunPython(backfill_description_updated_at, migrations.RunPython.noop),
    ]
