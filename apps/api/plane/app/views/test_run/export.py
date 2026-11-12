# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The raw-data export of one batch (TCASE-EXPORT).

The export is the record of an execution in one document: the header of
the batch, every case record with its snapshotted header and its outcome,
every step of those records, the files of both levels and the summary of
§6.2 rule 3. It is a **core, default-on** feature — the report template is
a separate plugin and this endpoint does not depend on it.

The document is a **contract**: the PDF renderer of the report and the CLI
read it, so its shape is pinned by the contract tests and it carries a
``schema_version``. A change of the shape is a new version, never a silent
edit of this one.

Three properties of the document are worth naming:

* the **text is the source that was typed** — the evidence of a case and
  the measured value of a step are the Markdown source, exactly as the
  read endpoints hand it out (:mod:`plane.app.serializers.test_run`);
* the **summary is not recounted** — it is the answer of
  :func:`plane.db.testcase_summary.summarize_run` over the same rows the
  ``/summary/`` endpoint reads, so the two surfaces cannot drift;
* a file travels as a **short-lived signed URL** of the existing
  asset-signing path (:class:`plane.settings.storage.S3Storage`), never as
  a bucket path and never as the bytes inline. When the signer answers
  nothing (an S3 client error) the row still carries the API path the
  other read endpoints use, which redirects to a freshly signed URL — the
  export does not leak a storage key as a fallback.

The queries are those of ``TestRunSummaryEndpoint`` and the case list: the
batch once, the case records once, their files and their step records
prefetched. Signing is the only per-file work, and one storage client is
built for the whole document.
"""

# Django imports
from django.db.models import Prefetch

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers.test_case import live_assets
from plane.app.testcase_validity import build_run_data
from plane.core.authz import ResourceType
from plane.db.models import TestRun, TestRunCase, TestRunStepRecord
from plane.db.testcase_summary import summarize_run
from plane.settings.storage import S3Storage

from ..base import BaseAPIView

#: The version of the document this module answers with. A consumer reads
#: it before it reads anything else: a shape that is not this version is
#: not this document.
SCHEMA_VERSION = 1


def attachment_name(asset) -> str:
    """The name the file was uploaded under.

    Same rule as ``FileAssetAttachmentSerializer.get_name``: the name the
    client sent, else the last segment of the storage key of a file that
    was uploaded without one.
    """
    name = (asset.attributes or {}).get("name")
    if name:
        return name
    return asset.asset.name.rsplit("/", 1)[-1] if asset.asset else ""


def attachment_document(asset, sign_url) -> dict:
    """One file of a case record or of a step record."""
    return {
        "id": asset.id,
        "name": attachment_name(asset),
        "mime_type": (asset.attributes or {}).get("type") or "",
        "size": asset.size,
        "url": sign_url(asset),
    }


def step_document(record, sign_url) -> dict:
    """One step of a case record: the snapshot, the verdict, the files."""
    return {
        "sequence_id": record.sequence_id,
        "action": record.action_snapshot,
        "expected_result": record.expected_result_snapshot,
        "conclusion": record.conclusion,
        "actual_value": record.actual_value,
        "attachments": [attachment_document(asset, sign_url) for asset in live_assets(record)],
    }


def case_document(run_case, sign_url) -> dict:
    """One case record: its snapshot, its outcome, its steps and its files."""
    return {
        # The id of the record, so a rendered document can point back at
        # the row it was built from. The steps are keyed by their sequence.
        "id": run_case.id,
        "case_number": run_case.case_number,
        "case_title": run_case.case_title,
        "case_module_code": run_case.case_module_code,
        "case_priority": run_case.case_priority,
        "case_purpose": run_case.case_purpose,
        "case_object_overview": run_case.case_object_overview,
        "case_preconditions": run_case.case_preconditions,
        "case_environment": run_case.case_environment,
        "conclusion": run_case.conclusion,
        "evidence": run_case.evidence,
        "remarks": run_case.remarks,
        "actual_result_summary": run_case.actual_result_summary,
        "na_reason": run_case.na_reason,
        "na_criteria_source": run_case.na_criteria_source,
        "manual_executor": run_case.manual_executor,
        "manual_condition": run_case.manual_condition,
        "steps": [step_document(record, sign_url) for record in run_case.step_records.all()],
        "attachments": [attachment_document(asset, sign_url) for asset in live_assets(run_case)],
    }


def export_document(run, run_cases, sign_url) -> dict:
    """The whole document of one batch.

    ``run_cases`` is the list the caller already loaded (with its files
    and step records prefetched); ``sign_url`` mints the URL of one file.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": {
            "number": run.number,
            "run_date": run.run_date,
            "version_number": run.version_number,
            "build_number": run.build_number,
            "commit_hash": run.commit_hash,
            "environment": run.environment,
            "executed_by_name": run.executed_by_name,
            "status": run.status,
            "retest_of": run.retest_of_id,
            # A soft-deleted original keeps its row and therefore its
            # number (see ``TestRunSerializer.get_retest_of_number``).
            "retest_of_number": run.retest_of.number if run.retest_of_id else None,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
        },
        "cases": [case_document(run_case, sign_url) for run_case in run_cases],
        # Never recounted here: the numbers and the deviation list are the
        # answer of the pure function the ``/summary/`` endpoint uses.
        "summary": summarize_run(build_run_data(run, run_cases)),
    }


class AssetUrlSigner:
    """Mints the short-lived signed URL of the files of one document.

    The storage client is built on the first file, so a batch without
    files does not pay for it, and it is reused for every file of the
    document. The signing itself is the one the asset download endpoint
    performs: the same ``generate_presigned_url`` with an inline
    disposition, because the files of a batch are rendered in the report
    rather than downloaded.
    """

    def __init__(self, request, workspace_slug):
        self.request = request
        self.workspace_slug = workspace_slug
        self.storage = None

    def __call__(self, asset) -> str:
        if self.storage is None:
            self.storage = S3Storage(request=self.request)
        signed_url = self.storage.generate_presigned_url(
            object_name=asset.asset.name,
            disposition="inline",
            filename=attachment_name(asset),
        )
        if signed_url:
            return signed_url
        # The signer failed. The API path is not a storage key: it answers
        # 302 to a freshly signed URL for a reader who may see the file,
        # and 403 for one who may not, so the document carries no key.
        return f"/api/assets/v2/workspaces/{self.workspace_slug}/{asset.id}/"


class TestRunExportEndpoint(BaseAPIView):
    """The raw data of one batch as one versioned JSON document.

    Read-only: the endpoint is a GET, so a write on it is a 405. The
    readers are the ones the rest of the batch surface admits — a member
    or guest of the project (PROJECT level, at least GUEST) and a service
    principal holding the ``read test_run`` scope. A batch of another
    project, or one that does not exist, is a 404.
    """

    allow_service_principal = True

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST],
        level="PROJECT",
        resource_type=ResourceType.TEST_RUN,
    )
    def get(self, request, slug, project_id, test_run_id):
        run = (
            TestRun.objects.filter(
                pk=test_run_id, project_id=project_id, workspace__slug=slug
            )
            .select_related("executed_by", "retest_of")
            .first()
        )
        if run is None:
            return Response({"error": "Test run not found"}, status=status.HTTP_404_NOT_FOUND)

        run_cases = list(
            TestRunCase.objects.filter(test_run=run)
            .select_related("test_case")
            .prefetch_related(
                "assets",
                Prefetch(
                    "step_records",
                    queryset=TestRunStepRecord.objects.order_by("sequence_id").prefetch_related(
                        "assets"
                    ),
                ),
            )
            .order_by("created_at")
        )

        return Response(
            export_document(run, run_cases, AssetUrlSigner(request, slug)),
            status=status.HTTP_200_OK,
        )
