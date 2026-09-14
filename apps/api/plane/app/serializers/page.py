# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import logging

# Third party imports
from rest_framework import serializers
import base64

# Module imports
from .base import BaseSerializer
from plane.utils.content_validator import (
    validate_binary_data,
    validate_html_content,
)
from plane.utils.page_duplication import CannotDeduplicate, assert_not_duplicated, repair_duplicated_html
from plane.utils.page_markdown_links import normalize_markdown_links
from plane.utils.page_frontmatter import (
    json_safe_metadata,
    normalize_tags,
    split_frontmatter,
    sync_tags_to_page_labels,
    with_created_date,
)
from plane.utils.text_repetition import collapse_repeated_text
from plane.db.models import (
    Page,
    PageLabel,
    Label,
    ProjectPage,
    Project,
    PageVersion,
)

logger = logging.getLogger(__name__)


class PageSerializer(BaseSerializer):
    is_favorite = serializers.BooleanField(read_only=True)
    labels = serializers.ListField(
        child=serializers.PrimaryKeyRelatedField(queryset=Label.objects.all()),
        write_only=True,
        required=False,
    )
    # Many to many
    label_ids = serializers.ListField(child=serializers.UUIDField(), required=False)
    project_ids = serializers.ListField(child=serializers.UUIDField(), required=False)

    class Meta:
        model = Page
        fields = [
            "id",
            "name",
            "owned_by",
            "access",
            "color",
            "labels",
            "parent",
            "is_favorite",
            "is_locked",
            "archived_at",
            "workspace",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
            "view_props",
            "logo_props",
            "label_ids",
            "project_ids",
            "frontmatter",
        ]
        read_only_fields = ["workspace", "owned_by"]

    def validate_name(self, value):
        """Fold a self-repeating name back to one copy (stale-merge artifact)."""
        return collapse_repeated_text(value)

    def create(self, validated_data):
        labels = validated_data.pop("labels", None)
        project_id = self.context["project_id"]
        owned_by_id = self.context["owned_by_id"]
        description_json = self.context["description_json"]
        description_binary = self.context["description_binary"]
        # frontmatter never belongs to the body: strip it before persisting and
        # map its tags to project labels below
        description_html, metadata = split_frontmatter(self.context["description_html"])
        frontmatter_tags = normalize_tags(metadata.get("tags"))
        # clients that convert markdown themselves can leave a half-converted
        # `[text](url)` behind — fold it back into a proper anchor
        description_html, repaired_links = normalize_markdown_links(description_html)
        if repaired_links:
            logger.warning("repaired %d half-converted markdown link(s) on create", repaired_links)
        # a union-merged body must never be persisted as-is: fold it back to a
        # single copy when that can be done losslessly, otherwise refuse
        try:
            description_html, repaired = repair_duplicated_html(description_html)
        except CannotDeduplicate as error:
            raise serializers.ValidationError({"description_html": str(error)})
        if repaired:
            logger.warning("page body repaired on create: %s", repaired)

        # Get the workspace id from the project
        project = Project.objects.get(pk=project_id)

        # Create the page
        page = Page.objects.create(
            **validated_data,
            description_json=description_json,
            description_binary=description_binary,
            description_html=description_html,
            owned_by_id=owned_by_id,
            workspace_id=project.workspace_id,
        )

        # Create the project page
        ProjectPage.objects.create(
            workspace_id=page.workspace_id,
            project_id=project_id,
            page_id=page.id,
            created_by_id=page.created_by_id,
            updated_by_id=page.updated_by_id,
        )

        # Create page labels
        if labels is not None:
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
                ],
                batch_size=10,
            )

        # frontmatter tags become project labels on top of the explicit ones
        sync_tags_to_page_labels(page, frontmatter_tags, project_id)

        frontmatter = with_created_date(json_safe_metadata(metadata), page.created_at)
        if frontmatter:
            page.frontmatter = frontmatter
            page.save(update_fields=["frontmatter"])
        return page

    def update(self, instance, validated_data):
        labels = validated_data.pop("labels", None)
        if labels is not None:
            PageLabel.objects.filter(page=instance).delete()
            PageLabel.objects.bulk_create(
                [
                    PageLabel(
                        label=label,
                        page=instance,
                        workspace_id=instance.workspace_id,
                        created_by_id=instance.created_by_id,
                        updated_by_id=instance.updated_by_id,
                    )
                    for label in labels
                ],
                batch_size=10,
            )

        page = super().update(instance, validated_data)
        # frontmatter tags become project labels on top of the explicit ones
        sync_tags_to_page_labels(page, getattr(self, "_frontmatter_tags", []), self._project_id_for(page))

        metadata = getattr(self, "_frontmatter_metadata", None)
        if metadata:
            page.frontmatter = with_created_date(json_safe_metadata(metadata), page.created_at)
            page.save(update_fields=["frontmatter"])
        return page

    def validate_description_html(self, value):
        """Pull a leading YAML frontmatter block out of the body (update path).

        Also rejects a body that looks like a union-merged (duplicated) page.
        """
        body, metadata = split_frontmatter(value)
        self._frontmatter_tags = normalize_tags(metadata.get("tags"))
        self._frontmatter_metadata = metadata
        if body:
            body, repaired_links = normalize_markdown_links(body)
            if repaired_links:
                logger.warning("repaired %d half-converted markdown link(s) on update", repaired_links)
            try:
                body, repaired = repair_duplicated_html(body)
            except CannotDeduplicate as error:
                raise serializers.ValidationError(str(error))
            if repaired:
                logger.warning("page body repaired on update: %s", repaired)
        return body

    def _project_id_for(self, page):
        """Project to scope frontmatter labels to: serializer context first,
        then the page's own project link."""
        project_id = self.context.get("project_id")
        if project_id:
            return project_id
        return (
            ProjectPage.objects.filter(page=page, deleted_at__isnull=True).values_list("project_id", flat=True).first()
        )


class PageDetailSerializer(PageSerializer):
    description_html = serializers.CharField()

    class Meta(PageSerializer.Meta):
        fields = PageSerializer.Meta.fields + ["description_html"]


class PageVersionSerializer(BaseSerializer):
    class Meta:
        model = PageVersion
        fields = [
            "id",
            "workspace",
            "page",
            "last_saved_at",
            "owned_by",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
        ]
        read_only_fields = ["workspace", "page"]


class PageVersionDetailSerializer(BaseSerializer):
    class Meta:
        model = PageVersion
        fields = [
            "id",
            "workspace",
            "page",
            "last_saved_at",
            "description_binary",
            "description_html",
            "description_json",
            "owned_by",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
        ]
        read_only_fields = ["workspace", "page"]


class PageBinaryUpdateSerializer(serializers.Serializer):
    """Serializer for updating page binary description with validation"""

    description_binary = serializers.CharField(required=False, allow_blank=True)
    description_html = serializers.CharField(required=False, allow_blank=True)
    description_json = serializers.JSONField(required=False, allow_null=True)

    def validate_description_binary(self, value):
        """Validate the base64-encoded binary data"""
        if not value:
            return value

        try:
            # Decode the base64 data
            binary_data = base64.b64decode(value)

            # Validate the binary data
            is_valid, error_message = validate_binary_data(binary_data)
            if not is_valid:
                raise serializers.ValidationError(f"Invalid binary data: {error_message}")

            return binary_data
        except Exception as e:
            if isinstance(e, serializers.ValidationError):
                raise
            raise serializers.ValidationError("Failed to decode base64 data")

    def validate_description_html(self, value):
        """Validate the HTML content"""
        if not value:
            return value

        # Use the validation function from utils
        is_valid, error_message, sanitized_html = validate_html_content(value)
        if not is_valid:
            raise serializers.ValidationError(error_message)

        # This path stores the client's binary alongside the html, so a repair
        # here would leave the two formats inconsistent: refuse instead (the
        # live store guard already blocks the case upstream).
        try:
            assert_not_duplicated(value)
        except CannotDeduplicate as error:
            raise serializers.ValidationError(str(error))

        # Return sanitized HTML if available, otherwise return original
        return sanitized_html if sanitized_html is not None else value

    def update(self, instance, validated_data):
        """Update the page instance with validated data"""
        if "description_binary" in validated_data:
            instance.description_binary = validated_data.get("description_binary")

        if "description_html" in validated_data:
            instance.description_html = validated_data.get("description_html")

        if "description_json" in validated_data:
            instance.description_json = validated_data.get("description_json")

        instance.save()
        return instance
