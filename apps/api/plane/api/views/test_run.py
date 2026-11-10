# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The v1 surface of the batch side of the test-case feature (STD-SW-003).

A **batch** is one continuous execution. It holds **case records**, every
case record holds the **step records** that carry the verdict and the
measured value, and the files of the measured value and of the evidence
hang on those records.

The rules of the standard are enforced by the server, and they are the
rules the app surface enforces — the validity functions live in
:mod:`plane.db.testcase_validity` and are not re-implemented here:

* a case that is added to a batch is **snapshotted** — a later edit of
  the case never rewrites the history of the batch;
* a verdict and a conclusion are written **once** (the model refuses a
  second write) — a wrong verdict is corrected by voiding the record and
  running the case again in a new batch;
* ``finalize`` runs the validity rules and reports every problem it
  finds, one entry per problem;
* a **completed batch is frozen**: every write returns 409.

The **field names are the contract** of the feature: they are the fields
the app surface hands out, so the serializers are imported from there
(:mod:`plane.api.serializers.test_run`).

The authorization goes through the existing chain
(:class:`plane.api.views.base.AIScopeEnforcementMixin`): every view
declares the ``resource_type`` of the feature (``test_run``) and hands
the row the write acts on to the engine, so the per-resource ownership
step applies — a member-tier service principal may only write the rows
its own bot user created.
"""

# Django imports
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.db.models import Prefetch
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.api.serializers.test_case import FileAssetAttachmentSerializer
from plane.api.serializers.test_run import (
    TestRunCaseSerializer,
    TestRunCaseWriteSerializer,
    TestRunSerializer,
    TestRunStepRecordSerializer,
    TestRunStepRecordWriteSerializer,
    TestRunWriteSerializer,
)
from plane.api.views.test_case import (
    ProjectScopedAuthzMixin,
    UpdateOnExistingRowMixin,
    project_admin,
)
from plane.app.testcase_validity import build_run_data, describe_reasons
from plane.app.views.test_case.base import (
    error_entry,
    object_body,
    serializer_error_response,
    stale_record_response,
    validation_error_response,
)
from plane.app.views.test_run.base import bindable_asset, run_conflict_response
from plane.db.models import (
    FileAsset,
    Project,
    TestCase,
    TestRun,
    TestRunCase,
    TestRunStepRecord,
)
from plane.db.testcase_validity import (
    DUPLICATE_CASE_NUMBER,
    RUN_STATUS_COMPLETED,
    validate_run,
    validate_run_write,
)
from plane.service_principals.attribution import actor_id_for_request
from plane.utils.permissions.project import ProjectEntityPermission

from .base import BaseAPIView


def _same_date(value, other) -> bool:
    """True when the client sent the day the batch carries.

    The client may send the day as ``2026-11-09`` or as a full
    timestamp; the batch stores the day.
    """
    from datetime import date

    try:
        sent = date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return False
    return sent == other


def credit_rows(rows, actor_id):
    """Pin the attribution of the rows a model helper has written.

    ``TestRunCase.create_for_test_case`` writes the case record and its
    step records itself, and it does not know the actor of the request.
    The rows are therefore written a second time with the attribution
    pinned — through ``created_by_id``, the kwarg ``BaseModel.save``
    honours. Without the pin the rows would carry no creator, and the
    ownership step of the authz chain (which matches the creator against
    the bot of the service principal) would refuse every later write.
    """
    for row in rows:
        row.save(created_by_id=actor_id)


class TestRunListCreateEndpoint(BaseAPIView):
    """The batches of a project."""

    resource_type = "test_run"

    permission_classes = [ProjectEntityPermission]

    def get_queryset(self):
        return (
            TestRun.objects.filter(
                project_id=self.kwargs.get("project_id"),
                workspace__slug=self.kwargs.get("slug"),
            )
            .select_related(
                "executed_by",
                "created_by",
                "created_by__avatar_asset__workspace",
            )
            .prefetch_related(
                Prefetch(
                    "run_cases",
                    queryset=TestRunCase.objects.select_related("test_case").prefetch_related(
                        "assets",
                        Prefetch(
                            "step_records",
                            queryset=TestRunStepRecord.objects.order_by("sequence_id").prefetch_related(
                                "assets"
                            ),
                        ),
                    ),
                )
            )
        )

    def get(self, request, slug, project_id):
        queryset = self.get_queryset()

        status_filter = request.GET.get("status")
        if status_filter and status_filter != "all":
            queryset = queryset.filter(status=status_filter)

        run_date = request.GET.get("run_date")
        if run_date:
            queryset = queryset.filter(run_date=run_date)

        return self.paginate(
            request=request,
            queryset=queryset,
            order_by="-run_date",
            on_results=lambda runs: TestRunSerializer(runs, many=True).data,
        )

    def post(self, request, slug, project_id):
        project = Project.objects.filter(pk=project_id, workspace__slug=slug).first()
        if project is None:
            return Response({"error": "Project not found"}, status=status.HTTP_404_NOT_FOUND)

        serializer = TestRunWriteSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return serializer_error_response(serializer)

        try:
            run = serializer.save(project=project, workspace=project.workspace)
        except DjangoValidationError as exc:
            return validation_error_response(exc, "The batch could not be saved.")

        return Response(TestRunSerializer(run).data, status=status.HTTP_201_CREATED)


class TestRunDetailEndpoint(ProjectScopedAuthzMixin, BaseAPIView):
    """One batch of a project."""

    resource_type = "test_run"

    permission_classes = [ProjectEntityPermission]

    # The row a PATCH/DELETE owns.
    authz_resource_model = TestRun

    def get_queryset(self):
        return TestRun.objects.filter(
            project_id=self.kwargs.get("project_id"),
            workspace__slug=self.kwargs.get("slug"),
        ).select_related("executed_by", "created_by", "created_by__avatar_asset__workspace")

    def get(self, request, slug, project_id, pk):
        run = self.get_queryset().filter(pk=pk).first()
        if run is None:
            return Response({"error": "Test run not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(TestRunSerializer(run).data, status=status.HTTP_200_OK)

    def patch(self, request, slug, project_id, pk):
        run = self.get_queryset().filter(pk=pk).first()
        if run is None:
            return Response({"error": "Test run not found"}, status=status.HTTP_404_NOT_FOUND)

        if run.status == RUN_STATUS_COMPLETED:
            return run_conflict_response("A completed batch is frozen.")

        body = object_body(request)
        if isinstance(body, Response):
            return body

        stale = stale_record_response(request, run, TestRunSerializer(run).data)
        if stale is not None:
            return stale

        # One batch stands for one tested version: the version fields are
        # frozen once they are recorded. The model refuses the change
        # too; the check here names the field on the wire.
        for field in ("version_number", "build_number", "commit_hash"):
            if field in body and body.get(field) != getattr(run, field):
                return Response(
                    {
                        "error": "The tested version of a batch cannot be changed.",
                        "code": "run_version_fixed",
                        "errors": [
                            {
                                "code": "run_version_fixed",
                                "message": "批次的被测版本在创建后不能修改，补测请新建批次。",
                                "path": field,
                                "field": field,
                            }
                        ],
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if "run_date" in body and not _same_date(body.get("run_date"), run.run_date):
            return Response(
                {
                    "error": "The execution date of a batch cannot be changed.",
                    "code": "run_date_fixed",
                    "errors": [
                        {
                            "code": "run_date_fixed",
                            "message": "批次的执行日期在创建后不能修改。",
                            "path": "run_date",
                            "field": "run_date",
                        }
                    ],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = TestRunWriteSerializer(
            run, data=request.data, partial=True, context={"request": request}
        )
        if not serializer.is_valid():
            return serializer_error_response(serializer)

        try:
            serializer.save()
        except DjangoValidationError as exc:
            return validation_error_response(exc, "The batch could not be saved.")

        run = self.get_queryset().filter(pk=pk).first()
        return Response(TestRunSerializer(run).data, status=status.HTTP_200_OK)

    def delete(self, request, slug, project_id, pk):
        run = self.get_queryset().filter(pk=pk).first()
        if run is None:
            return Response({"error": "Test run not found"}, status=status.HTTP_404_NOT_FOUND)
        if not project_admin(request, project_id):
            return Response(
                {"error": "Only a project admin can delete a test run."},
                status=status.HTTP_403_FORBIDDEN,
            )
        # A completed batch is frozen: its verdict is the history of the
        # execution and may not disappear afterwards. Every other write on
        # a completed batch answers 409 as well.
        if run.status == RUN_STATUS_COMPLETED:
            return run_conflict_response("A completed batch is frozen.")
        run.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class TestRunCaseEndpoint(ProjectScopedAuthzMixin, UpdateOnExistingRowMixin, BaseAPIView):
    """The case records of a batch.

    ``POST`` adds cases to the batch. Every added case is **snapshotted**
    — its header and its step table are copied into the batch — so a
    later edit of the case never rewrites what the batch recorded.
    """

    resource_type = "test_run"

    permission_classes = [ProjectEntityPermission]

    # The write acts on the batch the case records belong to.
    authz_resource_model = TestRun
    authz_resource_lookup = "test_run_id"

    def _run(self, slug, project_id, test_run_id):
        return TestRun.objects.filter(
            pk=test_run_id, project_id=project_id, workspace__slug=slug
        ).first()

    def get(self, request, slug, project_id, test_run_id):
        run = self._run(slug, project_id, test_run_id)
        if run is None:
            return Response({"error": "Test run not found"}, status=status.HTTP_404_NOT_FOUND)
        run_cases = (
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
        )
        return Response(
            TestRunCaseSerializer(run_cases, many=True).data,
            status=status.HTTP_200_OK,
        )

    def post(self, request, slug, project_id, test_run_id):
        run = self._run(slug, project_id, test_run_id)
        if run is None:
            return Response({"error": "Test run not found"}, status=status.HTTP_404_NOT_FOUND)

        if run.status == RUN_STATUS_COMPLETED:
            return run_conflict_response("A completed batch is frozen.")

        body = object_body(request)
        if isinstance(body, Response):
            return body

        case_ids = body.get("test_case_ids")
        if case_ids is None:
            case_ids = body.get("test_cases")
        if not isinstance(case_ids, list) or not case_ids:
            return Response(
                {
                    "error": "At least one test case is required.",
                    "code": "test_case_ids_missing",
                    "errors": [error_entry("test_case_ids_missing", "test_case_ids")],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Every case is checked before the first one is added: a batch
        # add is all or nothing.
        cases = []
        for case_id in case_ids:
            case = TestCase.objects.filter(
                pk=case_id, project_id=project_id, workspace__slug=slug
            ).first()
            if case is None:
                return Response(
                    {
                        "error": "A test case does not belong to this project.",
                        "code": "case_not_in_project",
                        "errors": [error_entry("case_not_in_project", "test_case_ids")],
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if TestRunCase.objects.filter(test_run=run, test_case=case).exists():
                return Response(
                    {
                        "error": "The test case is already in this batch.",
                        "code": DUPLICATE_CASE_NUMBER,
                        "errors": [error_entry(DUPLICATE_CASE_NUMBER, "test_case_ids")],
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            cases.append(case)

        created = []
        actor_id = actor_id_for_request(request)
        for case in cases:
            try:
                run_case = TestRunCase.create_for_test_case(
                    test_case=case,
                    test_run=run,
                )
            except DjangoValidationError as exc:
                # The model refuses a case that is already in the batch.
                return validation_error_response(exc, "The test case could not be added.")
            except IntegrityError:
                # A concurrent add won the race against the partial
                # unique index; the reason is the same one the model
                # would have raised.
                return Response(
                    {
                        "error": "The test case is already in this batch.",
                        "code": DUPLICATE_CASE_NUMBER,
                        "errors": [error_entry(DUPLICATE_CASE_NUMBER, "test_case_ids")],
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            # The snapshot rows are written by the model helper, which
            # does not know the actor of the request.
            credit_rows([run_case, *run_case.step_records.all()], actor_id)
            created.append(run_case)

        return Response(
            TestRunCaseSerializer(created, many=True).data,
            status=status.HTTP_201_CREATED,
        )


class TestRunCaseDetailEndpoint(ProjectScopedAuthzMixin, BaseAPIView):
    """The outcome of one case inside a batch."""

    resource_type = "test_run"

    permission_classes = [ProjectEntityPermission]

    # The write acts on the case record itself.
    authz_resource_model = TestRunCase
    authz_resource_lookup = "run_case_id"

    def _run_case(self, slug, project_id, test_run_id, run_case_id):
        return (
            TestRunCase.objects.filter(
                pk=run_case_id,
                test_run_id=test_run_id,
                project_id=project_id,
                workspace__slug=slug,
            )
            .select_related("test_run", "test_case")
            .prefetch_related(
                "assets",
                Prefetch(
                    "step_records",
                    queryset=TestRunStepRecord.objects.order_by("sequence_id").prefetch_related(
                        "assets"
                    ),
                ),
            )
            .first()
        )

    def get(self, request, slug, project_id, test_run_id, run_case_id):
        run_case = self._run_case(slug, project_id, test_run_id, run_case_id)
        if run_case is None:
            return Response({"error": "Test run case not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(TestRunCaseSerializer(run_case).data, status=status.HTTP_200_OK)

    def patch(self, request, slug, project_id, test_run_id, run_case_id):
        run_case = self._run_case(slug, project_id, test_run_id, run_case_id)
        if run_case is None:
            return Response({"error": "Test run case not found"}, status=status.HTTP_404_NOT_FOUND)

        if run_case.test_run.status == RUN_STATUS_COMPLETED:
            return run_conflict_response("A completed batch is frozen.")

        serializer = TestRunCaseWriteSerializer(
            run_case, data=request.data, partial=True, context={"request": request}
        )
        if not serializer.is_valid():
            return serializer_error_response(serializer)

        try:
            serializer.save()
        except DjangoValidationError as exc:
            return validation_error_response(exc, "The case record could not be saved.")

        run_case = self._run_case(slug, project_id, test_run_id, run_case_id)
        return Response(TestRunCaseSerializer(run_case).data, status=status.HTTP_200_OK)


class TestRunStepRecordEndpoint(ProjectScopedAuthzMixin, BaseAPIView):
    """The verdict and the measured value of one step of a batch case.

    This is the write that happens most often while a batch is executed,
    so it is a single row and a single save: the update is atomic for the
    step and the last write wins.
    """

    resource_type = "test_run"

    permission_classes = [ProjectEntityPermission]

    # The write acts on the case record the step belongs to.
    authz_resource_model = TestRunCase
    authz_resource_lookup = "run_case_id"

    def _step_record(self, slug, project_id, test_run_id, run_case_id, sequence_id):
        return (
            TestRunStepRecord.objects.filter(
                test_run_case_id=run_case_id,
                test_run_case__test_run_id=test_run_id,
                test_run_case__project_id=project_id,
                test_run_case__workspace__slug=slug,
                sequence_id=sequence_id,
            )
            .select_related("test_run_case", "test_run_case__test_run")
            .prefetch_related("assets")
            .first()
        )

    def get(self, request, slug, project_id, test_run_id, run_case_id, sequence_id):
        record = self._step_record(slug, project_id, test_run_id, run_case_id, sequence_id)
        if record is None:
            return Response({"error": "Step record not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(TestRunStepRecordSerializer(record).data, status=status.HTTP_200_OK)

    def patch(self, request, slug, project_id, test_run_id, run_case_id, sequence_id):
        record = self._step_record(slug, project_id, test_run_id, run_case_id, sequence_id)
        if record is None:
            return Response({"error": "Step record not found"}, status=status.HTTP_404_NOT_FOUND)

        if record.test_run_case.test_run.status == RUN_STATUS_COMPLETED:
            return run_conflict_response("A completed batch is frozen.")

        serializer = TestRunStepRecordWriteSerializer(
            record, data=request.data, partial=True, context={"request": request}
        )
        if not serializer.is_valid():
            return serializer_error_response(serializer)

        try:
            serializer.save()
        except DjangoValidationError as exc:
            return validation_error_response(exc, "The step record could not be saved.")

        record = self._step_record(slug, project_id, test_run_id, run_case_id, sequence_id)
        return Response(TestRunStepRecordSerializer(record).data, status=status.HTTP_200_OK)


class TestRunFinalizeEndpoint(ProjectScopedAuthzMixin, UpdateOnExistingRowMixin, BaseAPIView):
    """Close a batch: the server runs the validity rules over it.

    Every problem is reported on its own — the field path of the
    problem, the stable reason code and the Chinese sentence — so the
    client can put each message next to the input it belongs to.
    """

    resource_type = "test_run"

    permission_classes = [ProjectEntityPermission]

    # The write acts on the batch that is closed.
    authz_resource_model = TestRun
    authz_resource_lookup = "test_run_id"

    def post(self, request, slug, project_id, test_run_id):
        run = (
            TestRun.objects.filter(pk=test_run_id, project_id=project_id, workspace__slug=slug)
            .select_related("executed_by")
            .first()
        )
        if run is None:
            return Response({"error": "Test run not found"}, status=status.HTTP_404_NOT_FOUND)

        if run.status == RUN_STATUS_COMPLETED:
            return run_conflict_response("A completed batch is frozen.")

        run_cases = (
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

        run_data = build_run_data(run, list(run_cases))
        reasons = validate_run_write(run_data) + validate_run(run_data)

        if reasons:
            return Response(
                {
                    "error": "The batch is not valid.",
                    "code": "invalid_batch",
                    "errors": describe_reasons(reasons),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        run.status = RUN_STATUS_COMPLETED
        try:
            run.save()
        except DjangoValidationError as exc:
            return validation_error_response(exc, "The batch could not be completed.")

        run = (
            TestRun.objects.filter(pk=test_run_id)
            .select_related("executed_by")
            .prefetch_related(
                Prefetch(
                    "run_cases",
                    queryset=TestRunCase.objects.select_related("test_case").prefetch_related(
                        "assets",
                        Prefetch(
                            "step_records",
                            queryset=TestRunStepRecord.objects.order_by("sequence_id").prefetch_related(
                                "assets"
                            ),
                        ),
                    ),
                )
            )
            .first()
        )
        return Response(TestRunSerializer(run).data, status=status.HTTP_200_OK)


class TestRunAttachmentEndpoint(ProjectScopedAuthzMixin, UpdateOnExistingRowMixin, BaseAPIView):
    """The files of one case record of a batch (its evidence).

    The upload itself goes through the v1 asset endpoint
    (``POST /api/v1/workspaces/<slug>/assets/``), which a service
    principal may use; this endpoint binds an uploaded file to the
    record, lists the files of the record and unbinds one of them.
    """

    resource_type = "test_run"

    permission_classes = [ProjectEntityPermission]

    # The write acts on the case record the file hangs on.
    authz_resource_model = TestRunCase
    authz_resource_lookup = "run_case_id"

    def _run_case(self, slug, project_id, test_run_id, run_case_id):
        return TestRunCase.objects.filter(
            pk=run_case_id,
            test_run_id=test_run_id,
            project_id=project_id,
            workspace__slug=slug,
        ).first()

    def get(self, request, slug, project_id, test_run_id, run_case_id):
        run_case = self._run_case(slug, project_id, test_run_id, run_case_id)
        if run_case is None:
            return Response({"error": "Test run case not found"}, status=status.HTTP_404_NOT_FOUND)
        assets = FileAsset.objects.filter(test_run_case=run_case, is_deleted=False)
        return Response(
            FileAssetAttachmentSerializer(assets, many=True).data,
            status=status.HTTP_200_OK,
        )

    def post(self, request, slug, project_id, test_run_id, run_case_id):
        run_case = self._run_case(slug, project_id, test_run_id, run_case_id)
        if run_case is None:
            return Response({"error": "Test run case not found"}, status=status.HTTP_404_NOT_FOUND)

        if run_case.test_run.status == RUN_STATUS_COMPLETED:
            return run_conflict_response("A completed batch is frozen.")

        asset = bindable_asset(request, slug, run_case)
        if isinstance(asset, Response):
            return asset

        asset.test_run_case = run_case
        asset.entity_type = FileAsset.EntityTypeContext.TEST_RUN_CASE_EVIDENCE
        asset.project_id = run_case.project_id
        asset.workspace_id = run_case.workspace_id
        asset.save()

        return Response(
            FileAssetAttachmentSerializer(asset).data,
            status=status.HTTP_201_CREATED,
        )

    def delete(self, request, slug, project_id, test_run_id, run_case_id, asset_id):
        run_case = self._run_case(slug, project_id, test_run_id, run_case_id)
        if run_case is None:
            return Response({"error": "Test run case not found"}, status=status.HTTP_404_NOT_FOUND)

        if run_case.test_run.status == RUN_STATUS_COMPLETED:
            return run_conflict_response("A completed batch is frozen.")

        asset = FileAsset.objects.filter(
            id=asset_id, test_run_case=run_case, is_deleted=False
        ).first()
        if asset is None:
            return Response({"error": "Attachment not found"}, status=status.HTTP_404_NOT_FOUND)

        # Unbinding is a row operation: the file row is flagged deleted
        # and the record no longer holds it. The Markdown source of the
        # evidence does not have to be edited along with it. Only the
        # foreign key the record owns is cleared.
        asset.test_run_case = None
        asset.is_deleted = True
        asset.deleted_at = timezone.now()
        asset.save()

        return Response(status=status.HTTP_204_NO_CONTENT)


class TestRunStepAttachmentEndpoint(ProjectScopedAuthzMixin, UpdateOnExistingRowMixin, BaseAPIView):
    """The files of one step record of a batch case (its measured value)."""

    resource_type = "test_run"

    permission_classes = [ProjectEntityPermission]

    # The write acts on the case record the step belongs to.
    authz_resource_model = TestRunCase
    authz_resource_lookup = "run_case_id"

    def _step_record(self, slug, project_id, test_run_id, run_case_id, sequence_id):
        return TestRunStepRecord.objects.filter(
            test_run_case_id=run_case_id,
            test_run_case__test_run_id=test_run_id,
            test_run_case__project_id=project_id,
            test_run_case__workspace__slug=slug,
            sequence_id=sequence_id,
        ).select_related("test_run_case", "test_run_case__test_run").first()

    def get(self, request, slug, project_id, test_run_id, run_case_id, sequence_id):
        record = self._step_record(slug, project_id, test_run_id, run_case_id, sequence_id)
        if record is None:
            return Response({"error": "Step record not found"}, status=status.HTTP_404_NOT_FOUND)
        assets = FileAsset.objects.filter(test_run_step_record=record, is_deleted=False)
        return Response(
            FileAssetAttachmentSerializer(assets, many=True).data,
            status=status.HTTP_200_OK,
        )

    def post(self, request, slug, project_id, test_run_id, run_case_id, sequence_id):
        record = self._step_record(slug, project_id, test_run_id, run_case_id, sequence_id)
        if record is None:
            return Response({"error": "Step record not found"}, status=status.HTTP_404_NOT_FOUND)

        if record.test_run_case.test_run.status == RUN_STATUS_COMPLETED:
            return run_conflict_response("A completed batch is frozen.")

        asset = bindable_asset(request, slug, record)
        if isinstance(asset, Response):
            return asset

        asset.test_run_step_record = record
        asset.entity_type = FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD
        asset.project_id = record.project_id
        asset.workspace_id = record.workspace_id
        asset.save()

        return Response(
            FileAssetAttachmentSerializer(asset).data,
            status=status.HTTP_201_CREATED,
        )

    def delete(self, request, slug, project_id, test_run_id, run_case_id, sequence_id, asset_id):
        record = self._step_record(slug, project_id, test_run_id, run_case_id, sequence_id)
        if record is None:
            return Response({"error": "Step record not found"}, status=status.HTTP_404_NOT_FOUND)

        if record.test_run_case.test_run.status == RUN_STATUS_COMPLETED:
            return run_conflict_response("A completed batch is frozen.")

        asset = FileAsset.objects.filter(
            id=asset_id, test_run_step_record=record, is_deleted=False
        ).first()
        if asset is None:
            return Response({"error": "Attachment not found"}, status=status.HTTP_404_NOT_FOUND)

        asset.test_run_step_record = None
        asset.is_deleted = True
        asset.deleted_at = timezone.now()
        asset.save()

        return Response(status=status.HTTP_204_NO_CONTENT)
