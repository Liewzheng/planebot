# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
from uuid import uuid4

# Django import
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

# Module import
from plane.utils.path_validator import sanitize_filename

from .base import BaseModel


def get_upload_path(instance, filename):
    filename = sanitize_filename(filename) or uuid4().hex
    if instance.workspace_id is not None:
        return f"{instance.workspace.id}/{uuid4().hex}-{filename}"
    return f"user-{uuid4().hex}-{filename}"


def file_size(value):
    if value.size > settings.FILE_SIZE_LIMIT:
        raise ValidationError("File too large. Size should not exceed 5 MB.")


class FileAsset(BaseModel):
    """
    A file asset.
    """

    class EntityTypeContext(models.TextChoices):
        ISSUE_ATTACHMENT = "ISSUE_ATTACHMENT"
        ISSUE_DESCRIPTION = "ISSUE_DESCRIPTION"
        COMMENT_DESCRIPTION = "COMMENT_DESCRIPTION"
        PAGE_DESCRIPTION = "PAGE_DESCRIPTION"
        USER_COVER = "USER_COVER"
        USER_AVATAR = "USER_AVATAR"
        WORKSPACE_LOGO = "WORKSPACE_LOGO"
        PROJECT_COVER = "PROJECT_COVER"
        DRAFT_ISSUE_ATTACHMENT = "DRAFT_ISSUE_ATTACHMENT"
        DRAFT_ISSUE_DESCRIPTION = "DRAFT_ISSUE_DESCRIPTION"
        MERMAID_DIAGRAM = "MERMAID_DIAGRAM"
        #: A file attached to the measured value of one step of a batch
        #: case — the screenshot of what the tester saw.
        TEST_RUN_STEP_RECORD = "TEST_RUN_STEP_RECORD"
        #: A file attached to the evidence of one case inside a batch.
        TEST_RUN_CASE_EVIDENCE = "TEST_RUN_CASE_EVIDENCE"

    attributes = models.JSONField(default=dict)
    asset = models.FileField(upload_to=get_upload_path, max_length=800)
    user = models.ForeignKey("db.User", on_delete=models.CASCADE, null=True, related_name="assets")
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, null=True, related_name="assets")
    draft_issue = models.ForeignKey("db.DraftIssue", on_delete=models.CASCADE, null=True, related_name="assets")
    project = models.ForeignKey("db.Project", on_delete=models.CASCADE, null=True, related_name="assets")
    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, null=True, related_name="assets")
    comment = models.ForeignKey("db.IssueComment", on_delete=models.CASCADE, null=True, related_name="assets")
    page = models.ForeignKey("db.Page", on_delete=models.CASCADE, null=True, related_name="assets")
    # The attachments of a test run are bound by a foreign key and not by
    # a link that is parsed out of the Markdown source of the measured
    # value or of the evidence:
    #
    # * the Markdown source is written by hand and may be reworded or
    #   re-ordered at any time — a parsed link would break the moment
    #   the sentence around it moves,
    # * the file row knows its owner, so "the attachments of this step"
    #   is a query and not a text search over a column that anybody may
    #   rewrite,
    # * deleting an attachment is a row operation (the file row is
    #   flagged deleted); the Markdown source does not have to be edited
    #   along with it,
    # * the URL inside the Markdown is not an identity: it may point at
    #   a copy of the file, and it may be pasted from another system.
    test_run_step_record = models.ForeignKey(
        "db.TestRunStepRecord",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="assets",
    )
    test_run_case = models.ForeignKey(
        "db.TestRunCase",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="assets",
    )
    entity_type = models.CharField(max_length=255, null=True, blank=True)
    entity_identifier = models.CharField(max_length=255, null=True, blank=True)
    is_deleted = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)
    external_id = models.CharField(max_length=255, null=True, blank=True)
    external_source = models.CharField(max_length=255, null=True, blank=True)
    size = models.FloatField(default=0)
    is_uploaded = models.BooleanField(default=False)
    storage_metadata = models.JSONField(default=dict, null=True, blank=True)

    class Meta:
        verbose_name = "File Asset"
        verbose_name_plural = "File Assets"
        db_table = "file_assets"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["entity_type"], name="asset_entity_type_idx"),
            models.Index(fields=["entity_identifier"], name="asset_entity_identifier_idx"),
            models.Index(fields=["entity_type", "entity_identifier"], name="asset_entity_idx"),
            models.Index(fields=["asset"], name="asset_asset_idx"),
        ]

    def __str__(self):
        return str(self.asset)

    @property
    def asset_url(self):
        if (
            self.entity_type == self.EntityTypeContext.WORKSPACE_LOGO
            or self.entity_type == self.EntityTypeContext.USER_AVATAR
            or self.entity_type == self.EntityTypeContext.USER_COVER
            or self.entity_type == self.EntityTypeContext.PROJECT_COVER
        ):
            return f"/api/assets/v2/static/{self.id}/"

        if self.entity_type == self.EntityTypeContext.ISSUE_ATTACHMENT:
            return f"/api/assets/v2/workspaces/{self.workspace.slug}/projects/{self.project_id}/issues/{self.issue_id}/attachments/{self.id}/"  # noqa: E501

        if self.entity_type in [
            self.EntityTypeContext.ISSUE_DESCRIPTION,
            self.EntityTypeContext.COMMENT_DESCRIPTION,
            self.EntityTypeContext.PAGE_DESCRIPTION,
            self.EntityTypeContext.DRAFT_ISSUE_DESCRIPTION,
        ]:
            if self.project_id:
                return f"/api/assets/v2/workspaces/{self.workspace.slug}/projects/{self.project_id}/{self.id}/"
            return f"/api/assets/v2/workspaces/{self.workspace.slug}/{self.id}/"

        # Mermaid diagrams are served by the workspace asset endpoint with an
        # inline disposition (the project-path endpoint forces attachment, which
        # makes browsers download instead of rendering the image).
        if self.entity_type == self.EntityTypeContext.MERMAID_DIAGRAM:
            return f"/api/assets/v2/workspaces/{self.workspace.slug}/{self.id}/"

        # The attachments of a test run live in the workspace of the
        # project; they are served by the workspace asset endpoint.
        if self.entity_type in [
            self.EntityTypeContext.TEST_RUN_STEP_RECORD,
            self.EntityTypeContext.TEST_RUN_CASE_EVIDENCE,
        ]:
            return f"/api/assets/v2/workspaces/{self.workspace.slug}/{self.id}/"

        return None
