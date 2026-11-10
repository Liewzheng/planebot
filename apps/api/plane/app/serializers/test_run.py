"""Serializers of the batch side of the test-case feature (STD-SW-003).

A batch (:class:`TestRunSerializer`) holds case records
(:class:`TestRunCaseSerializer`); every case record holds the step
records (:class:`TestRunStepRecordSerializer`) that carry the verdict
and the measured value.

The measured value of a step and the evidence of a case are handed out
as the **Markdown source** that was typed: no HTML conversion, no
stripping of the markup. The front end renders them.
"""

# Third party imports
from rest_framework import serializers

# Module imports
from plane.db.models import (
    FileAsset,
    TestRun,
    TestRunCase,
    TestRunStepRecord,
    User,
)
from plane.db.testcase_validity import RUN_STATUS_COMPLETED

from .base import BaseSerializer
from .test_case import FileAssetAttachmentSerializer, actor_id, live_assets
from .user import UserLiteSerializer


class TestRunStepRecordSerializer(BaseSerializer):
    """The verdict and the measured value of one step of a batch case."""

    attachments = serializers.SerializerMethodField()

    class Meta:
        model = TestRunStepRecord
        fields = [
            "id",
            "sequence_id",
            "action_snapshot",
            "expected_result_snapshot",
            "conclusion",
            "actual_value",
            "attachments",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_attachments(self, record):
        return FileAssetAttachmentSerializer(live_assets(record), many=True).data


class TestRunCaseSerializer(BaseSerializer):
    """One case inside a batch, with the snapshot of the case header."""

    steps = TestRunStepRecordSerializer(source="step_records", many=True, read_only=True)
    attachments = serializers.SerializerMethodField()

    class Meta:
        model = TestRunCase
        fields = [
            "id",
            "test_case",
            "case_number",
            "case_module_code",
            "case_title",
            "case_purpose",
            "case_object_overview",
            "case_priority",
            "case_preconditions",
            "case_environment",
            "conclusion",
            "evidence",
            "remarks",
            "actual_result_summary",
            "na_reason",
            "na_criteria_source",
            "manual_executor",
            "manual_condition",
            "steps",
            "attachments",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_attachments(self, record):
        return FileAssetAttachmentSerializer(live_assets(record), many=True).data


class TestRunSerializer(BaseSerializer):
    """A batch: one continuous execution."""

    # The related name of the case records of a batch is ``run_cases``;
    # without the source DRF would look for ``run.cases``, find nothing
    # and drop the field silently.
    cases = TestRunCaseSerializer(source="run_cases", many=True, read_only=True)
    # The creator travels as the user itself (see the case serializer).
    # ``executed_by`` stays the id of the user: it is a field that is
    # written as well, and ``executed_by_name`` is the text snapshot the
    # server keeps next to it.
    created_by = UserLiteSerializer(read_only=True)

    class Meta:
        model = TestRun
        fields = [
            "id",
            "number",
            "run_date",
            "version_number",
            "build_number",
            "commit_hash",
            "environment",
            "executed_by",
            "executed_by_name",
            "status",
            "cases",
            "created_at",
            "updated_at",
            "created_by",
        ]
        read_only_fields = fields


class TestRunWriteSerializer(BaseSerializer):
    """A batch as it is written.

    The number of a batch is assigned by the server, so it is not a
    field of this serializer. A batch is completed through the
    ``finalize`` endpoint (which runs the validity rules), never by
    writing the status directly.
    """

    executed_by = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = TestRun
        fields = [
            "id",
            "run_date",
            "version_number",
            "build_number",
            "commit_hash",
            "environment",
            "executed_by",
            "status",
        ]
        read_only_fields = ["id"]

    def validate_status(self, value):
        status = (value or "").strip().lower()
        if status == RUN_STATUS_COMPLETED:
            raise serializers.ValidationError(
                "A batch is completed through the finalize endpoint, which runs the "
                "validity rules first."
            )
        if status and status not in dict(TestRun.STATUS_CHOICES):
            raise serializers.ValidationError("The status must be draft or in_progress.")
        return status

    def create(self, validated_data):
        instance = TestRun(**validated_data)
        instance.save(created_by_id=actor_id(self))
        return instance


class TestRunCaseWriteSerializer(BaseSerializer):
    """The outcome of one case inside a batch."""

    class Meta:
        model = TestRunCase
        fields = [
            "id",
            "conclusion",
            "evidence",
            "remarks",
            "actual_result_summary",
            "na_reason",
            "na_criteria_source",
            "manual_executor",
            "manual_condition",
        ]
        read_only_fields = ["id"]

    def validate_conclusion(self, value):
        conclusion = (value or "").strip().upper()
        if conclusion and conclusion not in dict(TestRunCase.CONCLUSION_CHOICES):
            raise serializers.ValidationError("The conclusion must be PASS, FAIL, N/A or MANUAL.")
        return conclusion


class TestRunStepRecordWriteSerializer(BaseSerializer):
    """The verdict and the measured value of one step, as they are written."""

    class Meta:
        model = TestRunStepRecord
        fields = [
            "id",
            "conclusion",
            "actual_value",
        ]
        read_only_fields = ["id"]

    def validate_conclusion(self, value):
        conclusion = (value or "").strip().lower()
        if conclusion and conclusion not in ("pass", "fail", "unset"):
            raise serializers.ValidationError("The step verdict must be pass, fail or unset.")
        return conclusion


class TestRunAttachmentSerializer(serializers.Serializer):
    """The body of a request that binds a file to a record."""

    asset_id = serializers.UUIDField()

    def validate_asset_id(self, value):
        if not FileAsset.objects.filter(id=value, is_deleted=False).exists():
            raise serializers.ValidationError("The asset does not exist.")
        return value
