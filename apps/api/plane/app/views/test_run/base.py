# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Views of the batch side of the test-case feature (STD-SW-003).

A **batch** (:class:`TestRunViewSet`) is one continuous execution. It
holds **case records** (:class:`TestRunCaseEndpoint`), every case record
holds the **step records** (:class:`TestRunStepRecordEndpoint`) that
carry the verdict and the measured value, and the files of the measured
value and of the evidence hang on those records
(:class:`TestRunAttachmentEndpoint`).

The rules of the standard are enforced by the server:

* a case that is added to a batch is **snapshotted** — a later edit of
  the case never rewrites the history of the batch;
* a verdict and a conclusion are written **once** (the model refuses a
  second write) — a wrong verdict is corrected by voiding the record and
  running the case again in a new batch;
* ``finalize`` runs the validity rules and reports every problem it
  finds, one entry per problem;
* a **completed batch is frozen**: every write returns 409.

The authorization goes through the existing layer: every method is
decorated with :func:`plane.app.permissions.allow_permission`, declares
the ``resource_type`` of the feature (``test_run``) and hands the row
the write acts on to the engine, so the per-resource ownership step
applies — a member-tier service principal may only write the rows its
own bot user created.
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
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers.test_case import FileAssetAttachmentSerializer
from plane.app.serializers.test_run import (
    TestRunCaseSerializer,
    TestRunCaseWriteSerializer,
    TestRunSerializer,
    TestRunStepRecordSerializer,
    TestRunStepRecordWriteSerializer,
    TestRunWriteSerializer,
)
from plane.app.testcase_validity import (
    build_run_data,
    describe_reasons,
)
from plane.core.authz import Action as AuthzAction
from plane.core.authz import ResourceType
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
from ..base import BaseAPIView, BaseViewSet
from ..test_case.base import (
    APP_REASON_MESSAGES_ZH,
    error_entry,
    serializer_error_response,
    stale_record_response,
    validation_error_response,
)


def _is_uuid(value) -> bool:
    """True when the value looks like a UUID."""
    from uuid import UUID

    try:
        UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return False
    return True


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


def run_conflict_response(message: str, code: str = "run_completed") -> Response:
    """The 409 body of a write that a completed batch refuses."""
    return Response(
        {
            "error": message,
            "code": code,
            "errors": [error_entry(code)],
        },
        status=status.HTTP_409_CONFLICT,
    )


#: The foreign keys of ``FileAsset`` that name the entity a file hangs
#: on. A file that already carries one of them may not get a second
#: owner: the row would then belong to two entities at once, and the
#: unbind of one of them would take the file away from the other.
_ASSET_ENTITY_FIELDS = (
    "issue_id",
    "page_id",
    "comment_id",
    "draft_issue_id",
    "user_id",
    "test_run_case_id",
    "test_run_step_record_id",
)

#: The entity types a file may carry when it is bound to a record of a
#: batch. A file that belongs to a whole entity as such — the cover of a
#: project, the logo of a workspace, an avatar — is not a file of a
#: record: binding it would take it away from that entity, and the
#: unbind would flag the row deleted. Only a file without an entity (or
#: one that was already meant for a batch) may be attached to a record.
_BINDABLE_ENTITY_TYPES = frozenset(
    {
        "",
        None,
        FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD,
        FileAsset.EntityTypeContext.TEST_RUN_CASE_EVIDENCE,
    }
)


def bindable_asset(request, slug, record):
    """The asset the request wants to bind to ``record``, or the error.

    The asset has to belong to the **project of the record**: a file of
    another project may not be moved into this one — the bind would
    silently re-home it, and the unbind would then soft-delete a row of a
    project the caller has no rights in. A file that already hangs on
    another entity may not get a second owner either, and a file that
    belongs to a whole entity (the cover of a project, the logo of a
    workspace) may not be attached to a record at all.

    Returns the asset, or a :class:`Response` carrying the reason.
    """
    asset_id = request.data.get("asset_id")
    if not asset_id or not _is_uuid(asset_id):
        return Response(
            {
                "error": "asset_id is required.",
                "code": "asset_not_found",
                "errors": [error_entry("asset_not_found", "asset_id")],
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    asset = FileAsset.objects.filter(
        id=asset_id, workspace__slug=slug, is_deleted=False
    ).first()
    if asset is None:
        return Response(
            {
                "error": "The asset does not exist.",
                "code": "asset_not_found",
                "errors": [error_entry("asset_not_found", "asset_id")],
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    if asset.project_id and asset.project_id != record.project_id:
        return Response(
            {
                "error": "The asset belongs to another project.",
                "code": "asset_project_mismatch",
                "errors": [error_entry("asset_project_mismatch", "asset_id")],
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    if any(getattr(asset, field) for field in _ASSET_ENTITY_FIELDS):
        # The file hangs on another entity (a work item, a page, a
        # comment, a record of another batch). The check comes first: the
        # answer names the entity the file already belongs to, not the
        # kind of entity type it carries.
        return Response(
            {
                "error": "The asset is already bound to a record.",
                "code": "asset_already_bound",
                "errors": [error_entry("asset_already_bound", "asset_id")],
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    if asset.entity_type not in _BINDABLE_ENTITY_TYPES:
        # The file belongs to a whole entity — the cover of a project,
        # the logo of a workspace, an avatar. It carries no entity
        # foreign key, so the check above cannot see it.
        return Response(
            {
                "error": "The asset belongs to another entity.",
                "code": "asset_not_bindable",
                "errors": [error_entry("asset_not_bindable", "asset_id")],
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    return asset


class TestRunViewSet(BaseViewSet):
    """The batches of a project."""

    model = TestRun
    allow_service_principal = True

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

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST],
        level="PROJECT",
        resource_type=ResourceType.TEST_RUN,
    )
    def list(self, request, slug, project_id):
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

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER],
        level="PROJECT",
        resource_type=ResourceType.TEST_RUN,
    )
    def create(self, request, slug, project_id):
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

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST],
        level="PROJECT",
        resource_type=ResourceType.TEST_RUN,
    )
    def retrieve(self, request, slug, project_id, pk):
        run = self.get_queryset().filter(pk=pk).first()
        if run is None:
            return Response({"error": "Test run not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(TestRunSerializer(run).data, status=status.HTTP_200_OK)

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER],
        level="PROJECT",
        resource_type=ResourceType.TEST_RUN,
        action=AuthzAction.UPDATE,
        resource_model=TestRun,
        resource_lookup="pk",
    )
    def partial_update(self, request, slug, project_id, pk):
        run = self.get_queryset().filter(pk=pk).first()
        if run is None:
            return Response({"error": "Test run not found"}, status=status.HTTP_404_NOT_FOUND)

        if run.status == RUN_STATUS_COMPLETED:
            return run_conflict_response("A completed batch is frozen.")

        stale = stale_record_response(request, run, TestRunSerializer(run).data)
        if stale is not None:
            return stale

        # One batch stands for one tested version: the version fields are
        # frozen once they are recorded. The model refuses the change
        # too; the check here names the field on the wire.
        for field in ("version_number", "build_number", "commit_hash"):
            if field in request.data and request.data.get(field) != getattr(run, field):
                return Response(
                    {
                        "error": "The tested version of a batch cannot be changed.",
                        "code": "run_version_fixed",
                        "errors": [
                            {
                                "code": "run_version_fixed",
                                "message": APP_REASON_MESSAGES_ZH["run_version_fixed"],
                                "path": field,
                                "field": field,
                            }
                        ],
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if "run_date" in request.data and not _same_date(
            request.data.get("run_date"), run.run_date
        ):
            return Response(
                {
                    "error": "The execution date of a batch cannot be changed.",
                    "code": "run_date_fixed",
                    "errors": [
                        {
                            "code": "run_date_fixed",
                            "message": APP_REASON_MESSAGES_ZH["run_date_fixed"],
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

    @allow_permission(
        [ROLE.ADMIN],
        level="PROJECT",
        resource_type=ResourceType.TEST_RUN,
        action=AuthzAction.DELETE,
        resource_model=TestRun,
        resource_lookup="pk",
    )
    def destroy(self, request, slug, project_id, pk):
        run = self.get_queryset().filter(pk=pk).first()
        if run is None:
            return Response({"error": "Test run not found"}, status=status.HTTP_404_NOT_FOUND)
        # A completed batch is frozen: its verdict is the history of the
        # execution and may not disappear afterwards. Every other write on
        # a completed batch answers 409 as well.
        if run.status == RUN_STATUS_COMPLETED:
            return run_conflict_response("A completed batch is frozen.")
        run.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class TestRunCaseEndpoint(BaseAPIView):
    """The case records of a batch.

    ``POST`` adds cases to the batch. Every added case is **snapshotted**
    — its header and its step table are copied into the batch — so a
    later edit of the case never rewrites what the batch recorded.
    """

    allow_service_principal = True

    def _run(self, slug, project_id, test_run_id):
        return TestRun.objects.filter(
            pk=test_run_id, project_id=project_id, workspace__slug=slug
        ).first()

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST],
        level="PROJECT",
        resource_type=ResourceType.TEST_RUN,
    )
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

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER],
        level="PROJECT",
        resource_type=ResourceType.TEST_RUN,
        action=AuthzAction.UPDATE,
        resource_model=TestRun,
        resource_lookup="test_run_id",
    )
    def post(self, request, slug, project_id, test_run_id):
        run = self._run(slug, project_id, test_run_id)
        if run is None:
            return Response({"error": "Test run not found"}, status=status.HTTP_404_NOT_FOUND)

        if run.status == RUN_STATUS_COMPLETED:
            return run_conflict_response("A completed batch is frozen.")

        case_ids = request.data.get("test_case_ids")
        if case_ids is None:
            case_ids = request.data.get("test_cases")
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
            created.append(run_case)

        return Response(
            TestRunCaseSerializer(created, many=True).data,
            status=status.HTTP_201_CREATED,
        )


class TestRunCaseDetailEndpoint(BaseAPIView):
    """The outcome of one case inside a batch."""

    allow_service_principal = True

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

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST],
        level="PROJECT",
        resource_type=ResourceType.TEST_RUN,
    )
    def get(self, request, slug, project_id, test_run_id, run_case_id):
        run_case = self._run_case(slug, project_id, test_run_id, run_case_id)
        if run_case is None:
            return Response({"error": "Test run case not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(TestRunCaseSerializer(run_case).data, status=status.HTTP_200_OK)

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER],
        level="PROJECT",
        resource_type=ResourceType.TEST_RUN,
        action=AuthzAction.UPDATE,
        resource_model=TestRunCase,
        resource_lookup="run_case_id",
    )
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


class TestRunStepRecordEndpoint(BaseAPIView):
    """The verdict and the measured value of one step of a batch case.

    This is the write that happens most often while a batch is executed,
    so it is a single row and a single save: the update is atomic for
    the step and the last write wins.
    """

    allow_service_principal = True

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

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST],
        level="PROJECT",
        resource_type=ResourceType.TEST_RUN,
    )
    def get(self, request, slug, project_id, test_run_id, run_case_id, sequence_id):
        record = self._step_record(slug, project_id, test_run_id, run_case_id, sequence_id)
        if record is None:
            return Response({"error": "Step record not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(TestRunStepRecordSerializer(record).data, status=status.HTTP_200_OK)

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER],
        level="PROJECT",
        resource_type=ResourceType.TEST_RUN,
        action=AuthzAction.UPDATE,
        resource_model=TestRunCase,
        resource_lookup="run_case_id",
    )
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


class TestRunFinalizeEndpoint(BaseAPIView):
    """Close a batch: the server runs the validity rules over it.

    Every problem is reported on its own — the field path of the
    problem, the stable reason code and the Chinese sentence — so the
    front end can put each message next to the input it belongs to.
    """

    allow_service_principal = True

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER],
        level="PROJECT",
        resource_type=ResourceType.TEST_RUN,
        action=AuthzAction.UPDATE,
        resource_model=TestRun,
        resource_lookup="test_run_id",
    )
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


class TestRunAttachmentEndpoint(BaseAPIView):
    """The files of one case record of a batch (its evidence).

    The upload itself goes through the existing v2 asset endpoint; this
    endpoint only binds an uploaded file to the record, lists the files
    of the record and unbinds one of them.
    """

    allow_service_principal = True

    def _run_case(self, slug, project_id, test_run_id, run_case_id):
        return TestRunCase.objects.filter(
            pk=run_case_id,
            test_run_id=test_run_id,
            project_id=project_id,
            workspace__slug=slug,
        ).first()

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST],
        level="PROJECT",
        resource_type=ResourceType.TEST_RUN,
    )
    def get(self, request, slug, project_id, test_run_id, run_case_id):
        run_case = self._run_case(slug, project_id, test_run_id, run_case_id)
        if run_case is None:
            return Response({"error": "Test run case not found"}, status=status.HTTP_404_NOT_FOUND)
        assets = FileAsset.objects.filter(test_run_case=run_case, is_deleted=False)
        return Response(
            FileAssetAttachmentSerializer(assets, many=True).data,
            status=status.HTTP_200_OK,
        )

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER],
        level="PROJECT",
        resource_type=ResourceType.TEST_RUN,
        action=AuthzAction.UPDATE,
        resource_model=TestRunCase,
        resource_lookup="run_case_id",
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

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER],
        level="PROJECT",
        resource_type=ResourceType.TEST_RUN,
        action=AuthzAction.UPDATE,
        resource_model=TestRunCase,
        resource_lookup="run_case_id",
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


class TestRunStepAttachmentEndpoint(BaseAPIView):
    """The files of one step record of a batch case (its measured value)."""

    allow_service_principal = True

    def _step_record(self, slug, project_id, test_run_id, run_case_id, sequence_id):
        return TestRunStepRecord.objects.filter(
            test_run_case_id=run_case_id,
            test_run_case__test_run_id=test_run_id,
            test_run_case__project_id=project_id,
            test_run_case__workspace__slug=slug,
            sequence_id=sequence_id,
        ).select_related("test_run_case", "test_run_case__test_run").first()

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST],
        level="PROJECT",
        resource_type=ResourceType.TEST_RUN,
    )
    def get(self, request, slug, project_id, test_run_id, run_case_id, sequence_id):
        record = self._step_record(slug, project_id, test_run_id, run_case_id, sequence_id)
        if record is None:
            return Response({"error": "Step record not found"}, status=status.HTTP_404_NOT_FOUND)
        assets = FileAsset.objects.filter(test_run_step_record=record, is_deleted=False)
        return Response(
            FileAssetAttachmentSerializer(assets, many=True).data,
            status=status.HTTP_200_OK,
        )

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER],
        level="PROJECT",
        resource_type=ResourceType.TEST_RUN,
        action=AuthzAction.UPDATE,
        resource_model=TestRunCase,
        resource_lookup="run_case_id",
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

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER],
        level="PROJECT",
        resource_type=ResourceType.TEST_RUN,
        action=AuthzAction.UPDATE,
        resource_model=TestRunCase,
        resource_lookup="run_case_id",
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
