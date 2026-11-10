# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Views of the test-case feature (STD-SW-003): the module registry, the
cases, their step table and their links to work items.

The authorization goes through the existing layer: every method is
decorated with :func:`plane.app.permissions.allow_permission`, declares
the ``resource_type`` of the feature (``test_case``) and hands the row
the write acts on to the engine, so the per-resource ownership step
applies — a member-tier service principal may only write the rows its
own bot user created.

Every write goes through ``instance.save()`` / ``serializer.save()``: the
model hooks (the write-once conclusion, the same-project guards, the
duplicate-case check, the batch freeze) live there and a queryset write
would step over them.
"""

# Django imports
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Prefetch, Q
from django.utils.dateparse import parse_datetime

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers.test_case import (
    TestCaseIssueSerializer,
    TestCaseModuleSerializer,
    TestCaseSerializer,
    TestCaseStepSerializer,
    TestCaseWriteSerializer,
)
from plane.app.testcase_validity import (
    REASON_MESSAGES_ZH,
    check_authoring,
    describe_reasons,
    reason_code_of,
)
from plane.core.authz import Action as AuthzAction
from plane.core.authz import ResourceType
from plane.db.models import (
    Issue,
    Project,
    TestCase,
    TestCaseIssue,
    TestCaseModule,
    TestCaseStep,
)
from plane.db.testcase_numbering import is_valid_module_code
from plane.service_principals.attribution import actor_id_for_request

from ..base import BaseAPIView, BaseViewSet


#: Reason codes the app layer adds on top of the vocabulary of
#: :mod:`plane.db.testcase_validity`. They are endpoint-level problems
#: the rules do not know about.
APP_REASON_MESSAGES_ZH = {
    "conflict": "这条记录在你编辑期间被改过了，请重新载入后再保存。",
    "module_code_taken": "该模块码已在本项目登记。",
    "module_code_format": "模块码必须是 2 至 6 位大写字母。",
    "module_description_missing": "模块说明不能为空。",
    "module_not_in_project": "该模块不属于本项目。",
    "issue_not_in_project": "该工作项不属于本项目。",
    "issue_link_exists": "该工作项已经关联到这个用例。",
    "case_not_in_project": "该用例不属于本项目。",
    "invalid_updated_at": "updated_at 不是合法的时间戳。",
    "duplicate_test_case": "同一个用例不能重复加入批次。",
    "test_case_ids_missing": "请至少选择一个用例。",
    "asset_not_found": "附件不存在。",
    "asset_already_bound": "该附件已经绑定到其它记录。",
    "asset_not_bindable": "该附件是其它用途的文件（封面、标识等），不能绑定到测试记录上。",
    "asset_project_mismatch": "该附件属于其它项目，不能绑定到本项目的记录上。",
    "run_version_fixed": "一个批次只对应一个被测版本，补测请新建批次。",
    "run_date_fixed": "批次的执行日期在创建后不能修改。",
}


def error_entry(code: str, path: str = "", message: str = "") -> dict:
    """One entry of the ``errors`` list of a 400 body.

    The sentence comes from the vocabulary of the rules
    (:data:`REASON_MESSAGES_ZH`) or, for the codes the app layer adds on
    top of it, from :data:`APP_REASON_MESSAGES_ZH`. Both are consulted:
    an entry without a sentence would leave the front end with nothing
    to show next to the offending field.
    """
    return {
        "code": code,
        "message": message
        or REASON_MESSAGES_ZH.get(code)
        or APP_REASON_MESSAGES_ZH.get(code, ""),
        "path": path,
        "field": path,
    }


def serializer_error_response(serializer, error: str = "The payload is not valid.") -> Response:
    """Wrap the refusal of a serializer in the envelope of this feature.

    A field the serializer refuses (an unknown priority, a date that is
    not a date) is answered in the same shape as every other error of
    the feature, so the front end and the CLI never have to parse a
    second shape. The message of such an entry is the **detail of the
    serializer** — it is not a localised sentence; the code and the
    field are what a client keys on.
    """
    errors = []
    raw_errors = serializer.errors
    if isinstance(raw_errors, dict):
        items = raw_errors.items()
    else:
        items = [("", raw_errors)]

    for field, messages in items:
        if not isinstance(messages, (list, tuple)):
            messages = [messages]
        for message in messages:
            errors.append(
                {
                    "code": "invalid_payload",
                    "message": str(message),
                    "path": field,
                    "field": field,
                }
            )

    return Response(
        {"error": error, "code": "invalid_payload", "errors": errors},
        status=status.HTTP_400_BAD_REQUEST,
    )


def validation_error_response(exc, error: str = "The payload is not valid.") -> Response:
    """Map a Django ``ValidationError`` onto the 400 body of this feature.

    The model layer prefixes its messages with the stable reason code
    (``"conclusion_is_final: …"``); the code is what the front end keys
    on, the Chinese sentence is what it shows.
    """
    errors = []
    message_dict = getattr(exc, "message_dict", None)
    if message_dict:
        for field, messages in message_dict.items():
            for message in messages:
                code = reason_code_of(message) or ""
                errors.append(
                    {
                        "code": code,
                        "message": REASON_MESSAGES_ZH.get(code, message),
                        "detail": message,
                        "path": field,
                        "field": field,
                    }
                )
    else:
        for message in getattr(exc, "messages", [str(exc)]):
            code = reason_code_of(message) or ""
            errors.append(
                {
                    "code": code,
                    "message": REASON_MESSAGES_ZH.get(code, message),
                    "detail": message,
                    "path": "",
                    "field": "",
                }
            )
    return Response(
        {"error": error, "code": "invalid_payload", "errors": errors},
        status=status.HTTP_400_BAD_REQUEST,
    )


def conflict_response(current: dict) -> Response:
    """The 409 body of an optimistic-concurrency conflict."""
    return Response(
        {
            "error": "The record was changed by somebody else. Reload it and try again.",
            "code": "conflict",
            "errors": [error_entry("conflict")],
            "current": current,
        },
        status=status.HTTP_409_CONFLICT,
    )


def stale_record_response(request, record, current: dict):
    """The 409 response when the client's ``updated_at`` no longer matches.

    ``None`` when the client sent no ``updated_at`` (the write is then a
    plain last-write-wins) or when the value still matches.
    """
    client_value = request.data.get("updated_at")
    if not client_value:
        return None
    parsed = parse_datetime(str(client_value))
    if parsed is None:
        return Response(
            {
                "error": "updated_at is not a valid timestamp.",
                "code": "invalid_updated_at",
                "errors": [error_entry("invalid_updated_at", "updated_at")],
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    if parsed != record.updated_at:
        return conflict_response(current)
    return None


def project_or_404(slug, project_id):
    """The project of the URL, or ``None``."""
    return Project.objects.filter(pk=project_id, workspace__slug=slug).first()


class TestCaseModuleEndpoint(BaseAPIView):
    """The module registry of a project.

    A module code stands for one group of functions and is registered at
    the head of the project's case set; a case may only use a code that
    is registered here.
    """

    allow_service_principal = True

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST],
        level="PROJECT",
        resource_type=ResourceType.TEST_CASE,
    )
    def get(self, request, slug, project_id):
        modules = TestCaseModule.objects.filter(
            project_id=project_id, workspace__slug=slug
        ).order_by("code")
        return Response(
            TestCaseModuleSerializer(modules, many=True).data,
            status=status.HTTP_200_OK,
        )

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER],
        level="PROJECT",
        resource_type=ResourceType.TEST_CASE,
    )
    def post(self, request, slug, project_id):
        project = project_or_404(slug, project_id)
        if project is None:
            return Response({"error": "Project not found"}, status=status.HTTP_404_NOT_FOUND)

        serializer = TestCaseModuleSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return serializer_error_response(serializer)

        code = serializer.validated_data.get("code", "")
        description = serializer.validated_data.get("description", "").strip()

        if not is_valid_module_code(code):
            return Response(
                {
                    "error": "The module code is not valid.",
                    "code": "module_code_format",
                    "errors": [error_entry("module_code_format", "code")],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not description:
            return Response(
                {
                    "error": "The module description is required.",
                    "code": "module_description_missing",
                    "errors": [error_entry("module_description_missing", "description")],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if TestCaseModule.objects.filter(project=project, code=code).exists():
            return Response(
                {
                    "error": "The module code is already used in this project.",
                    "code": "module_code_taken",
                    "errors": [error_entry("module_code_taken", "code")],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            module = serializer.save(project=project, workspace=project.workspace)
        except DjangoValidationError as exc:
            return validation_error_response(exc, "The module could not be saved.")

        return Response(
            TestCaseModuleSerializer(module).data,
            status=status.HTTP_201_CREATED,
        )


class TestCaseViewSet(BaseViewSet):
    """The cases of a project.

    Reading a case needs a project member, writing one needs a member
    of the project, removing one needs an admin. A service principal
    goes through the same layer; a member-tier one may only write the
    cases it created itself.
    """

    model = TestCase
    allow_service_principal = True

    def get_queryset(self):
        return (
            TestCase.objects.filter(
                project_id=self.kwargs.get("project_id"),
                workspace__slug=self.kwargs.get("slug"),
            )
            .select_related("module", "created_by", "created_by__avatar_asset__workspace")
            .prefetch_related(
                "steps",
                Prefetch(
                    "issue_links",
                    queryset=TestCaseIssue.objects.select_related("issue", "issue__project"),
                ),
            )
        )

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST],
        level="PROJECT",
        resource_type=ResourceType.TEST_CASE,
    )
    def list(self, request, slug, project_id):
        queryset = self.get_queryset()

        module = request.GET.get("module")
        if module:
            queryset = queryset.filter(
                Q(module_id=module) if _is_uuid(module) else Q(module__code=module.upper())
            )

        priority = request.GET.get("priority")
        if priority:
            queryset = queryset.filter(priority=priority.upper())

        issue = request.GET.get("issue")
        if issue:
            queryset = queryset.filter(issue_links__issue_id=issue)

        status_filter = request.GET.get("status", "active")
        if status_filter and status_filter != "all":
            queryset = queryset.filter(status=status_filter)

        search = request.GET.get("search")
        if search:
            queryset = queryset.filter(Q(number__icontains=search) | Q(title__icontains=search))

        queryset = queryset.distinct()

        return self.paginate(
            request=request,
            queryset=queryset,
            order_by="-created_at",
            on_results=lambda cases: TestCaseSerializer(cases, many=True).data,
        )

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER],
        level="PROJECT",
        resource_type=ResourceType.TEST_CASE,
    )
    def create(self, request, slug, project_id):
        project = project_or_404(slug, project_id)
        if project is None:
            return Response({"error": "Project not found"}, status=status.HTTP_404_NOT_FOUND)

        serializer = TestCaseWriteSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return serializer_error_response(serializer)

        module = serializer.validated_data.get("module")
        if module is None or module.project_id != project.id:
            return Response(
                {
                    "error": "The module must belong to the same project as the test case.",
                    "code": "module_not_in_project",
                    "errors": [error_entry("module_not_in_project", "module")],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        steps = serializer.validated_data.pop("steps", []) or []
        header = {
            "title": serializer.validated_data.get("title", ""),
            "purpose": serializer.validated_data.get("purpose", ""),
            "object_overview": serializer.validated_data.get("object_overview", ""),
            "priority": serializer.validated_data.get("priority", ""),
            "preconditions": serializer.validated_data.get("preconditions", ""),
            "environment": serializer.validated_data.get("environment", ""),
        }

        # The server checks the mandatory items itself; the front end is
        # not part of the contract.
        reasons = check_authoring(module.code, header, steps)
        if reasons:
            return Response(
                {
                    "error": "The test case is not valid.",
                    "code": "invalid_test_case",
                    "errors": describe_reasons(reasons),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            case = serializer.save(project=project, workspace=project.workspace)
        except DjangoValidationError as exc:
            return validation_error_response(exc, "The test case could not be saved.")

        replace_steps(case, steps, project, actor_id_for_request(request))

        return Response(TestCaseSerializer(case).data, status=status.HTTP_201_CREATED)

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST],
        level="PROJECT",
        resource_type=ResourceType.TEST_CASE,
    )
    def retrieve(self, request, slug, project_id, pk):
        case = self.get_queryset().filter(pk=pk).first()
        if case is None:
            return Response({"error": "Test case not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(TestCaseSerializer(case).data, status=status.HTTP_200_OK)

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER],
        level="PROJECT",
        resource_type=ResourceType.TEST_CASE,
        action=AuthzAction.UPDATE,
        resource_model=TestCase,
        resource_lookup="pk",
    )
    def partial_update(self, request, slug, project_id, pk):
        case = self.get_queryset().filter(pk=pk).first()
        if case is None:
            return Response({"error": "Test case not found"}, status=status.HTTP_404_NOT_FOUND)

        stale = stale_record_response(request, case, TestCaseSerializer(case).data)
        if stale is not None:
            return stale

        serializer = TestCaseWriteSerializer(
            case, data=request.data, partial=True, context={"request": request}
        )
        if not serializer.is_valid():
            return serializer_error_response(serializer)

        module = serializer.validated_data.get("module", case.module)
        if module.project_id != case.project_id:
            return Response(
                {
                    "error": "The module must belong to the same project as the test case.",
                    "code": "module_not_in_project",
                    "errors": [error_entry("module_not_in_project", "module")],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        steps = serializer.validated_data.pop("steps", None)

        header = {
            "title": serializer.validated_data.get("title", case.title),
            "purpose": serializer.validated_data.get("purpose", case.purpose),
            "object_overview": serializer.validated_data.get("object_overview", case.object_overview),
            "priority": serializer.validated_data.get("priority", case.priority),
            "preconditions": serializer.validated_data.get("preconditions", case.preconditions),
            "environment": serializer.validated_data.get("environment", case.environment),
        }
        check_steps = steps if steps is not None else [
            {"action": step.action, "expected_result": step.expected_result}
            for step in case.steps.all()
        ]

        reasons = check_authoring(module.code, header, check_steps, number=case.number)
        if reasons:
            return Response(
                {
                    "error": "The test case is not valid.",
                    "code": "invalid_test_case",
                    "errors": describe_reasons(reasons),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            serializer.save()
        except DjangoValidationError as exc:
            return validation_error_response(exc, "The test case could not be saved.")

        if steps is not None:
            replace_steps(case, steps, case.project, actor_id_for_request(request))

        case = self.get_queryset().filter(pk=pk).first()
        return Response(TestCaseSerializer(case).data, status=status.HTTP_200_OK)

    @allow_permission(
        [ROLE.ADMIN],
        level="PROJECT",
        resource_type=ResourceType.TEST_CASE,
        action=AuthzAction.DELETE,
        resource_model=TestCase,
        resource_lookup="pk",
    )
    def destroy(self, request, slug, project_id, pk):
        case = self.get_queryset().filter(pk=pk).first()
        if case is None:
            return Response({"error": "Test case not found"}, status=status.HTTP_404_NOT_FOUND)
        case.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class TestCaseStepEndpoint(BaseAPIView):
    """The step table of one case.

    ``PUT`` replaces the whole table — the client sends the list it
    wants to have. The server checks that the table has at least one
    step and that every step carries an action and an expected result.
    """

    allow_service_principal = True

    def _case(self, slug, project_id, test_case_id):
        return TestCase.objects.filter(
            pk=test_case_id,
            project_id=project_id,
            workspace__slug=slug,
        ).first()

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST],
        level="PROJECT",
        resource_type=ResourceType.TEST_CASE,
    )
    def get(self, request, slug, project_id, test_case_id):
        case = self._case(slug, project_id, test_case_id)
        if case is None:
            return Response({"error": "Test case not found"}, status=status.HTTP_404_NOT_FOUND)
        steps = case.steps.all().order_by("sequence_id")
        return Response(
            TestCaseStepSerializer(steps, many=True).data,
            status=status.HTTP_200_OK,
        )

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER],
        level="PROJECT",
        resource_type=ResourceType.TEST_CASE,
        action=AuthzAction.UPDATE,
        resource_model=TestCase,
        resource_lookup="test_case_id",
    )
    def put(self, request, slug, project_id, test_case_id):
        case = self._case(slug, project_id, test_case_id)
        if case is None:
            return Response({"error": "Test case not found"}, status=status.HTTP_404_NOT_FOUND)

        steps = request.data.get("steps")
        if steps is None:
            steps = []

        header = {
            "title": case.title,
            "purpose": case.purpose,
            "object_overview": case.object_overview,
            "priority": case.priority,
            "preconditions": case.preconditions,
            "environment": case.environment,
        }
        reasons = check_authoring(case.module.code, header, steps, number=case.number)
        if reasons:
            return Response(
                {
                    "error": "The step table is not valid.",
                    "code": "invalid_test_case",
                    "errors": describe_reasons(reasons),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        replace_steps(case, steps, case.project, actor_id_for_request(request))
        case.refresh_from_db(fields=["updated_at"])

        # The step table belongs to the case, so the replacement moves the
        # ``updated_at`` of the case. The new value travels with the
        # answer: the client keeps it for the next write, otherwise it
        # would lock itself out with a stale version.
        return Response(
            {
                "updated_at": case.updated_at,
                "steps": TestCaseStepSerializer(
                    case.steps.all().order_by("sequence_id"), many=True
                ).data,
            },
            status=status.HTTP_200_OK,
        )


class TestCaseIssueEndpoint(BaseAPIView):
    """The links between a case and the work items it belongs to."""

    allow_service_principal = True

    def _case(self, slug, project_id, test_case_id):
        return TestCase.objects.filter(
            pk=test_case_id,
            project_id=project_id,
            workspace__slug=slug,
        ).first()

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST],
        level="PROJECT",
        resource_type=ResourceType.TEST_CASE,
    )
    def get(self, request, slug, project_id, test_case_id):
        case = self._case(slug, project_id, test_case_id)
        if case is None:
            return Response({"error": "Test case not found"}, status=status.HTTP_404_NOT_FOUND)
        links = TestCaseIssue.objects.filter(test_case=case).select_related(
            "issue", "issue__project"
        )
        return Response(
            TestCaseIssueSerializer(links, many=True).data,
            status=status.HTTP_200_OK,
        )

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER],
        level="PROJECT",
        resource_type=ResourceType.TEST_CASE,
        action=AuthzAction.UPDATE,
        resource_model=TestCase,
        resource_lookup="test_case_id",
    )
    def post(self, request, slug, project_id, test_case_id):
        case = self._case(slug, project_id, test_case_id)
        if case is None:
            return Response({"error": "Test case not found"}, status=status.HTTP_404_NOT_FOUND)

        issue_id = request.data.get("issue_id")
        if not issue_id:
            return Response(
                {
                    "error": "issue_id is required.",
                    "code": "issue_not_in_project",
                    "errors": [error_entry("issue_not_in_project", "issue_id")],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        issue = Issue.objects.filter(
            pk=issue_id, project_id=project_id, workspace__slug=slug
        ).first()
        if issue is None:
            return Response(
                {
                    "error": "The work item does not belong to this project.",
                    "code": "issue_not_in_project",
                    "errors": [error_entry("issue_not_in_project", "issue_id")],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if TestCaseIssue.all_objects.filter(test_case=case, issue=issue).exists():
            return Response(
                {
                    "error": "The work item is already linked to this test case.",
                    "code": "issue_link_exists",
                    "errors": [error_entry("issue_link_exists", "issue_id")],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            link = TestCaseIssue.objects.create(
                test_case=case,
                issue=issue,
                project=case.project,
                workspace=case.workspace,
                created_by_id=actor_id_for_request(request),
            )
        except DjangoValidationError as exc:
            return validation_error_response(exc, "The link could not be created.")

        return Response(
            TestCaseIssueSerializer(link).data,
            status=status.HTTP_201_CREATED,
        )


class TestCaseIssueDetailEndpoint(BaseAPIView):
    """One link between a case and a work item."""

    allow_service_principal = True

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER],
        level="PROJECT",
        resource_type=ResourceType.TEST_CASE,
        action=AuthzAction.UPDATE,
        resource_model=TestCase,
        resource_lookup="test_case_id",
    )
    def delete(self, request, slug, project_id, test_case_id, issue_id):
        case = TestCase.objects.filter(
            pk=test_case_id,
            project_id=project_id,
            workspace__slug=slug,
        ).first()
        if case is None:
            return Response({"error": "Test case not found"}, status=status.HTTP_404_NOT_FOUND)

        link = TestCaseIssue.all_objects.filter(test_case=case, issue_id=issue_id).first()
        if link is None:
            return Response({"error": "Link not found"}, status=status.HTTP_404_NOT_FOUND)

        # Hard delete: the unique constraint on ``(test_case, issue)``
        # does not ignore the soft-deleted rows, so a soft delete would
        # keep the link reserved and the work item could never be linked
        # again.
        link.delete(soft=False)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _is_uuid(value: str) -> bool:
    """True when the value looks like a UUID."""
    from uuid import UUID

    try:
        UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def replace_steps(case, steps, project, actor_id=None):
    """Replace the step table of a case with the list the client sent.

    The step numbers are assigned by the server (1, 2, 3 …). The old
    rows are removed **hard**: the unique constraint on
    ``(test_case, sequence_id)`` does not ignore the soft-deleted rows,
    so a soft delete would keep the numbers reserved and the replacement
    could not be written at all.

    The row of the case is touched as well (``updated_at`` only): the
    step table belongs to the case, so a client that holds an older
    ``updated_at`` has to learn that somebody else replaced the table —
    otherwise the optimistic lock of the case header would not see the
    replacement and the stale write would win.
    """
    TestCaseStep.all_objects.filter(test_case=case).delete()
    for index, step in enumerate(steps, start=1):
        TestCaseStep.objects.create(
            test_case=case,
            project=project,
            workspace=project.workspace,
            sequence_id=index,
            action=step.get("action", ""),
            expected_result=step.get("expected_result", ""),
            created_by_id=actor_id,
        )
    # ``updated_at`` is ``auto_now``: the write moves it to now.
    case.save(update_fields=["updated_at", "updated_by"])
