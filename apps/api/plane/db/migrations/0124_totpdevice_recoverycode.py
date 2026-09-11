# Generated manually for MFA (TOTP + recovery codes)

from django.conf import settings
from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0123_alter_fileasset_entity_type_mermaid_diagram"),
    ]

    operations = [
        migrations.CreateModel(
            name="TOTPDevice",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Last Modified At")),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        unique=True,
                        editable=False,
                        db_index=True,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("secret", models.TextField()),
                ("confirmed", models.BooleanField(default=False)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=models.deletion.CASCADE,
                        related_name="totp_device",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "TOTP Device",
                "verbose_name_plural": "TOTP Devices",
                "db_table": "totp_devices",
                "ordering": ("-created_at",),
            },
        ),
        migrations.CreateModel(
            name="RecoveryCode",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Last Modified At")),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        unique=True,
                        editable=False,
                        db_index=True,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("code_hash", models.CharField(max_length=64)),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="recovery_codes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Recovery Code",
                "verbose_name_plural": "Recovery Codes",
                "db_table": "recovery_codes",
                "ordering": ("-created_at",),
            },
        ),
    ]
