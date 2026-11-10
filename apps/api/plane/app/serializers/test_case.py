# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Serializers of the test case and its module registry (STD-SW-003).

Two groups live here:

* the **module registry** (:class:`TestCaseModuleSerializer`) — the
  codes a project registered before a case may use them;
* the **case** (:class:`TestCaseSerializer` /
  :class:`TestCaseWriteSerializer`) with its ordered step table and its
  links to work items.

The batch side lives in :mod:`plane.app.serializers.test_run`.
"""

# Third party imports
from rest_framework import serializers

# Module imports
from plane.db.models import (
    TestCase,
    TestCaseIssue,
    TestCaseModule,
    TestCaseStep,
)

from .base import BaseSerializer
from .user import UserLiteSerializer


def actor_id(serializer):
    """The user a write through this serializer is credited to.

    ``actor_for_request`` resolves a service-principal request to the
    shadow bot of the principal — never to the human the token is filed
    under — and a human request to the user itself. The id is handed to
    ``save(created_by_id=…)``, which is the only way to pin the
    attribution: ``BaseModel.save`` overwrites ``created_by`` with the
    current request user otherwise.
    """
    from plane.service_principals.attribution import actor_id_for_request

    request = serializer.context.get("request")
    if request is None:
        return None
    return actor_id_for_request(request)


def live_assets(record) -> list:
    """The files attached to a record that are not deleted.

    The prefetched rows are used when the caller prefetched the
    ``assets`` relation, so a list endpoint does not run one query per
    record.
    """
    cache = getattr(record, "_prefetched_objects_cache", None)
    if cache is not None and "assets" in cache:
        return [asset for asset in cache["assets"] if not asset.is_deleted]
    return list(record.assets.filter(is_deleted=False))


class FileAssetAttachmentSerializer(serializers.Serializer):
    """One file attached to a step record or to a case record.

    ``id`` and ``asset_id`` are the same value — the id of the
    :class:`FileAsset` row — in two shapes: a UUID for the JSON
    serialiser and a string for the clients that key their own maps by
    it. ``url`` is the path the file is served from; the front end does
    not have to look the asset up to show it.
    """

    id = serializers.UUIDField(read_only=True)
    asset_id = serializers.SerializerMethodField()
    name = serializers.SerializerMethodField()
    size = serializers.FloatField(read_only=True)
    url = serializers.SerializerMethodField()

    def get_asset_id(self, asset):
        return str(asset.id)

    def get_name(self, asset):
        name = (asset.attributes or {}).get("name")
        if name:
            return name
        return asset.asset.name.rsplit("/", 1)[-1] if asset.asset else ""

    def get_url(self, asset):
        return asset.asset_url


class TestCaseModuleSerializer(BaseSerializer):
    """A registered module code of a project."""

    class Meta:
        model = TestCaseModule
        fields = [
            "id",
            "code",
            "description",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_code(self, value):
        code = (value or "").strip().upper()
        if not code:
            raise serializers.ValidationError("The module code is required.")
        return code

    def create(self, validated_data):
        instance = TestCaseModule(**validated_data)
        instance.save(created_by_id=actor_id(self))
        return instance


class TestCaseStepSerializer(BaseSerializer):
    """One row of the step table of a case."""

    class Meta:
        model = TestCaseStep
        fields = [
            "id",
            "sequence_id",
            "action",
            "expected_result",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class TestCaseIssueSerializer(BaseSerializer):
    """A link between a case and a work item."""

    issue_identifier = serializers.SerializerMethodField()
    issue_name = serializers.SerializerMethodField()

    class Meta:
        model = TestCaseIssue
        fields = [
            "id",
            "issue_id",
            "issue_identifier",
            "issue_name",
            "created_at",
        ]
        read_only_fields = fields

    def get_issue_identifier(self, link):
        if not link.issue_id:
            return None
        # The identifier of a work item is the identifier of its project
        # and its sequence: ``<PROJECT>-<NNN>``.
        return f"{link.issue.project.identifier}-{link.issue.sequence_id}"

    def get_issue_name(self, link):
        return link.issue.name if link.issue_id else None


class TestCaseSerializer(BaseSerializer):
    """A case as it is read."""

    module_code = serializers.CharField(source="module.code", read_only=True)
    # The creator travels as the user itself: the front end renders the
    # actor (a human or the shadow bot of a service principal) without
    # a second request. The field is read-only, so the shape of the
    # answer never reaches a write.
    created_by = UserLiteSerializer(read_only=True)
    steps = TestCaseStepSerializer(many=True, read_only=True)
    issue_links = TestCaseIssueSerializer(many=True, read_only=True)

    class Meta:
        model = TestCase
        fields = [
            "id",
            "number",
            "module",
            "module_code",
            "title",
            "purpose",
            "object_overview",
            "priority",
            "preconditions",
            "environment",
            "status",
            "steps",
            "issue_links",
            "created_at",
            "updated_at",
            "created_by",
        ]
        read_only_fields = fields


class TestCaseWriteSerializer(BaseSerializer):
    """A case as it is written.

    ``steps`` is the **whole** step table: the client sends the list it
    wants to have, the server replaces the stored one with it. The
    numbers of the steps are assigned by the server (1, 2, 3 …), so a
    client may send them or leave them out.
    """

    steps = serializers.ListField(child=serializers.DictField(), required=False, write_only=True)

    class Meta:
        model = TestCase
        fields = [
            "id",
            "module",
            "title",
            "purpose",
            "object_overview",
            "priority",
            "preconditions",
            "environment",
            "status",
            "steps",
        ]
        read_only_fields = ["id"]

    def validate_module(self, value):
        return value

    def validate_priority(self, value):
        priority = (value or "").strip().upper()
        if priority and priority not in dict(TestCase.PRIORITY_CHOICES):
            raise serializers.ValidationError("The priority must be one of P0, P1, P2, P3.")
        return priority

    def validate_status(self, value):
        status = (value or "").strip().lower()
        if status and status not in dict(TestCase.STATUS_CHOICES):
            raise serializers.ValidationError("The status must be active or void.")
        return status

    def create(self, validated_data):
        instance = TestCase(**validated_data)
        instance.save(created_by_id=actor_id(self))
        return instance


