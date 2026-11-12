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
* a case enters a batch only if it still passes the authoring rules:
  the same check the write surface runs is run again at add time, so a
  case written before the rules existed (or written through a path that
  skips them) cannot bring an unfinished step table into an execution;
* while the batch is open a case that was never executed may be taken
  out of it again — the removal edits the composition of the batch and
  leaves the case itself and its other batches alone; a record that
  already carries a verdict or a conclusion is refused with 409, because
  removing it would let a second verdict be written inside the same
  batch;
* a verdict and a conclusion are written **once** (the model refuses a
  second write) — a wrong verdict is corrected by voiding the record and
  running the case again in a new batch;
* ``finalize`` runs the validity rules and reports every problem it
  finds, one entry per problem;
* ``summary`` answers the four counts and the deviation list of §6.2
  rule 3 / §6.3 — read-only, computed by the pure function of the db
  layer, never from a number the client sends;
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
from rest_framework.exceptions import PermissionDenied
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
from plane.app.testcase_validity import build_run_data, check_authoring, describe_reasons
from plane.app.views.test_run.export import AssetUrlSigner, export_document
from plane.db.models import (
    FileAsset,
    Project,
    TestCase,
    TestRun,
    TestRunCase,
    TestRunStepRecord,
)
from plane.db.testcase_summary import summarize_run
from plane.db.testcase_validity import (
    DUPLICATE_CASE_NUMBER,
    RUN_STATUS_COMPLETED,
    STEP_VERDICT_UNSET,
    validate_run,
    validate_run_write,
)
from plane.service_principals.attribution import actor_id_for_request
from plane.utils.permissions.project import ProjectEntityPermission
from plane.utils.testcase_api import (
    bindable_asset,
    error_entry,
    object_body,
    run_conflict_response,
    same_date,
    serializer_error_response,
    stale_record_response,
    validation_error_response,
)

from .base import BaseAPIView


#: The stable reason code of a removal that would erase what the batch
#: recorded. It is the endpoint's own code: neither reason-code map of
#: the feature carries it, so the sentence of its entry is handed to
#: :func:`error_entry` here. The two view modules of the feature may not
#: import each other (see :mod:`plane.utils.testcase_api`), so the code
#: and the response are defined on both surfaces and kept identical.
RUN_CASE_REMOVAL_AFTER_EXECUTION = "run_case_removal_after_execution"

_RUN_CASE_REMOVAL_AFTER_EXECUTION_ZH = (
    "该用例已记录执行结果，不能从批次移除；重跑请作废本批次。"
)


def removal_after_execution_response() -> Response:
    """The 409 of a removal that would erase what the batch recorded.

    The body is the envelope of :func:`run_conflict_response` — a
    completed batch is not the only write this feature refuses, so the
    client keys on a stable code here too — and the Chinese sentence is
    the one the front end shows.
    """
    return Response(
        {
            "error": (
                "The case record already carries execution results and cannot be "
                "removed from the batch; re-run the case in a new batch."
            ),
            "code": RUN_CASE_REMOVAL_AFTER_EXECUTION,
            "errors": [
                error_entry(
                    RUN_CASE_REMOVAL_AFTER_EXECUTION,
                    "",
                    _RUN_CASE_REMOVAL_AFTER_EXECUTION_ZH,
                )
            ],
        },
        status=status.HTTP_409_CONFLICT,
    )


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
                # The serializer of this surface is the app's (one contract,
                # not two), so the re-test link it hands out has to be
                # joined here as well — otherwise every linked row costs one
                # query.
                "retest_of",
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
        ).select_related(
            "executed_by",
            # Same serializer, same join as the app surface (see the list
            # endpoint above).
            "retest_of",
            "created_by",
            "created_by__avatar_asset__workspace",
        )

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

        if "run_date" in body and not same_date(body.get("run_date"), run.run_date):
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
        if not project_admin(request, slug, project_id):
            raise PermissionDenied("Only a project admin can delete a test run.")
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
        # add is all or nothing. The same id twice in one payload is the
        # same refusal as a case that is already in the batch — without
        # this, the first copy would be inserted before the second one
        # fails.
        cases = []
        selected_case_ids = set()
        for case_id in case_ids:
            case = (
                TestCase.objects.filter(
                    pk=case_id, project_id=project_id, workspace__slug=slug
                )
                .select_related("module")
                .prefetch_related("steps")
                .first()
            )
            if case is None:
                return Response(
                    {
                        "error": "A test case does not belong to this project.",
                        "code": "case_not_in_project",
                        "errors": [error_entry("case_not_in_project", "test_case_ids")],
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if (
                case.pk in selected_case_ids
                or TestRunCase.objects.filter(test_run=run, test_case=case).exists()
            ):
                return Response(
                    {
                        "error": "The test case is already in this batch.",
                        "code": DUPLICATE_CASE_NUMBER,
                        "errors": [error_entry(DUPLICATE_CASE_NUMBER, "test_case_ids")],
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            selected_case_ids.add(case.pk)
            cases.append(case)

        # The authoring rules run a second time here, on the state of the
        # case as it is at this moment. They are *not* duplicated code:
        # :func:`check_authoring` is the one implementation, called from
        # the write surface (create / update / replace the step table) and
        # from here. Both call sites are needed and neither replaces the
        # other. The write surface guards the cases written after the
        # rules existed; this call guards the cases written before them,
        # or written through a path that bypasses the serializer. Without
        # it such a case enters the batch and only fails at ``finalize``,
        # where nobody can act on an unfinished step table any more —
        # STD-SW-003 §5.6 says such a case is not written yet and must
        # not enter an execution batch at all.
        authoring_reasons: list[str] = []
        for case in cases:
            header = {
                "title": case.title,
                "purpose": case.purpose,
                "object_overview": case.object_overview,
                "priority": case.priority,
                "preconditions": case.preconditions,
                "environment": case.environment,
            }
            steps = [
                {"action": step.action, "expected_result": step.expected_result}
                for step in case.steps.all()
            ]
            # The case number decorates the reason (``<number>|…``) the
            # same way a finalize entry is decorated, so every entry names
            # the offending case and, when there is one, the step.
            authoring_reasons.extend(
                f"{case.number}|{reason}"
                for reason in check_authoring(
                    case.module.code, header, steps, number=case.number
                )
            )

        if authoring_reasons:
            # 400, not 409: the batch itself is fine and its state does not
            # contradict the payload — the *cases* the client selected are
            # not executable yet, so the payload is not acceptable until
            # they are corrected.
            return Response(
                {
                    "error": "The test case is not valid.",
                    "code": "invalid_test_case",
                    "errors": describe_reasons(authoring_reasons),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

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


class TestRunCaseDetailEndpoint(ProjectScopedAuthzMixin, UpdateOnExistingRowMixin, BaseAPIView):
    """The outcome of one case inside a batch.

    ``DELETE`` takes the case out of the batch — the undo of the add —
    and answers 204, as long as the batch recorded nothing about it yet.
    A record whose execution has begun is refused with 409: a verdict on
    a step or a conclusion of the case is written once and is never
    retracted, and removing the record would hand out a fresh one — with
    fresh step records — inside the same batch.
    """

    resource_type = "test_run"

    permission_classes = [ProjectEntityPermission]

    # The write acts on the case record itself.
    authz_resource_model = TestRunCase
    authz_resource_lookup = "run_case_id"

    # The removal is judged as an ``update`` of the batch's composition,
    # not as a ``delete`` of a resource: taking a case out of a batch
    # edits the batch, and the principal that may add a case has to be
    # able to undo the add. Judged as ``delete`` the matrix would demand
    # the delete scope and an admin-tier grant, so the undo would be
    # refused where the add was allowed. Removing the *case* itself is a
    # different write and keeps the action of the method.
    #
    # The answers of that gate: a GUEST (role 5) is refused with 403 by
    # :class:`ProjectEntityPermission`; a service principal without the
    # ``update test_run`` scope is refused with 403 by the scope step.
    authz_update_methods = ("DELETE",)

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

    def delete(self, request, slug, project_id, test_run_id, run_case_id):
        run_case = self._run_case(slug, project_id, test_run_id, run_case_id)
        if run_case is None:
            return Response({"error": "Test run case not found"}, status=status.HTTP_404_NOT_FOUND)

        if run_case.test_run.status == RUN_STATUS_COMPLETED:
            return run_conflict_response("A completed batch is frozen.")

        # What the batch recorded is final, and removal is not the way
        # around that: a removed record leaves a hole the same case can be
        # selected into again, and the fresh record carries fresh step
        # records — so a second, different verdict could be written inside
        # the *same* batch, which the write-once rule of the standard
        # forbids (a wrong verdict is corrected by voiding the record and
        # running the case again in a new batch). A record that was never
        # executed is a wrong selection and may still go: its step records
        # all carry the placeholder verdict ``unset``. The measured value
        # on its own is not a verdict and does not freeze the record.
        if run_case.conclusion or any(
            record.conclusion not in ("", STEP_VERDICT_UNSET)
            for record in run_case.step_records.all()
        ):
            return removal_after_execution_response()

        # Soft delete: every read filters the removed row out, so the case
        # is out of the batch while the batch keeps what was recorded. The
        # partial unique index ignores a deleted row, so the same case may
        # be selected into the batch again; a second removal finds no live
        # row and answers 404 (a member-tier service principal stops one
        # step earlier, at the ownership step, because the row it would
        # own is soft-deleted — still never a 500). The case row and the
        # rows of its other batches are not touched — the deleted record
        # is the only link between this batch and the case.
        run_case.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


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


class TestRunSummaryEndpoint(BaseAPIView):
    """The summary of a batch (STD-SW-003 §6.2 rule 3, §6.3) — read-only.

    The four counts and the deviation list are computed by
    :func:`plane.db.testcase_summary.summarize_run` over the same
    dataclasses the validity rules read; the view loads the rows and hands
    the result on, it never counts anything itself. Nothing the client
    sends is an input: a count on the wire is a report of the stored
    record, not a value a caller can choose — a query parameter or a body
    carrying ``total`` is therefore ignored, not validated.

    The answer is a plain object of five numbers and a list, so there is
    no serializer to import for it: the keys of
    :func:`summarize_run` are the contract.
    """

    resource_type = "test_run"

    permission_classes = [ProjectEntityPermission]

    def get(self, request, slug, project_id, test_run_id):
        run = (
            TestRun.objects.filter(
                pk=test_run_id, project_id=project_id, workspace__slug=slug
            )
            .select_related("executed_by")
            .first()
        )
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
            .order_by("created_at")
        )

        return Response(
            summarize_run(build_run_data(run, list(run_cases))),
            status=status.HTTP_200_OK,
        )


class TestRunExportEndpoint(BaseAPIView):
    """The raw data of one batch as one versioned JSON document — read-only.

    The document is the one the app surface answers with, and it is built
    by the same code: :func:`plane.app.views.test_run.export.export_document`
    and :class:`plane.app.views.test_run.export.AssetUrlSigner` are imported
    here, not re-derived, so the two surfaces cannot drift. Only the layer
    in front of those builders differs — the authorization below, and the
    v1 URL — while the loader mirrors that of ``/summary/`` so the summary
    block of the document is counted over the same rows.

    A consumer of this endpoint is the pbot CLI, which talks only to v1,
    so the readers are the readers of the v1 batch reads: a project member
    (PROJECT level, at least GUEST) or a service principal holding the
    ``read test_run`` scope. A batch of another project, or one that does
    not exist, is a 404; the endpoint is a GET, so a write is a 405.
    """

    resource_type = "test_run"

    permission_classes = [ProjectEntityPermission]

    # The app surface declares this flag for its permission layer; on v1
    # the service-principal gate above (``resource_type``) is the one that
    # admits a scoped principal, and the flag carries the same intent here.
    allow_service_principal = True

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


class TestRunAttachmentEndpoint(ProjectScopedAuthzMixin, UpdateOnExistingRowMixin, BaseAPIView):
    """The files of one case record of a batch (its evidence).

    The upload itself goes through the v1 asset endpoint
    (``POST /api/v1/workspaces/<slug>/assets/``), which a service
    principal may use; this endpoint binds an uploaded file to the
    record, lists the files of the record and unbinds one of them.

    The unbind removes the file from the record, not the record itself,
    so the authz chain judges it as an ``update`` of the record — what
    the app surface asks for (ADMIN or MEMBER, ``update`` scope).
    """

    resource_type = "test_run"

    permission_classes = [ProjectEntityPermission]

    # The write acts on the case record the file hangs on.
    authz_resource_model = TestRunCase
    authz_resource_lookup = "run_case_id"
    authz_update_methods = ("POST", "DELETE")

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
    """The files of one step record of a batch case (its measured value).

    The unbind removes the file from the step, not the step itself, so
    the authz chain judges it as an ``update`` of the record — what the
    app surface asks for (ADMIN or MEMBER, ``update`` scope).
    """

    resource_type = "test_run"

    permission_classes = [ProjectEntityPermission]

    # The write acts on the case record the step belongs to.
    authz_resource_model = TestRunCase
    authz_resource_lookup = "run_case_id"
    authz_update_methods = ("POST", "DELETE")

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
