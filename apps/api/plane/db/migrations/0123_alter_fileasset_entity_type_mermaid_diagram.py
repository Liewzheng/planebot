# Generated manually for PLANE-30

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0122_alter_draftissue_assignees_alter_issue_assignees_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="fileasset",
            name="entity_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("ISSUE_ATTACHMENT", "Issue Attachment"),
                    ("ISSUE_DESCRIPTION", "Issue Description"),
                    ("COMMENT_DESCRIPTION", "Comment Description"),
                    ("PAGE_DESCRIPTION", "Page Description"),
                    ("USER_COVER", "User Cover"),
                    ("USER_AVATAR", "User Avatar"),
                    ("WORKSPACE_LOGO", "Workspace Logo"),
                    ("PROJECT_COVER", "Project Cover"),
                    ("DRAFT_ISSUE_ATTACHMENT", "Draft Issue Attachment"),
                    ("DRAFT_ISSUE_DESCRIPTION", "Draft Issue Description"),
                    ("MERMAID_DIAGRAM", "Mermaid Diagram"),
                ],
                max_length=255,
                null=True,
            ),
        ),
    ]
