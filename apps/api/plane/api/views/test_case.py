# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The v1 surface of the test-case feature (STD-SW-003): the module
registry, the cases, their step table and their links to work items.

The **field names are the contract** of the feature. They are the fields
the app surface hands out, so the serializers are imported from there
(:mod:`plane.api.serializers.test_case`) and the answers of this surface
and of the app surface can be read by the same client.

The authorization goes through the existing chain
(:class:`plane.api.views.base.AIScopeEnforcementMixin`): every view
declares the ``resource_type`` of the feature (``test_case``) and hands
the row the write acts on to the engine, so the per-resource ownership
step applies — a member-tier service principal may only write the rows
its own bot user created.

Every write goes through ``instance.save()`` / ``serializer.save()``: the
model hooks (the immutable number, the same-project guards, the
duplicate-case check) live there and a queryset write would step over
them.
"""

# Django imports
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Prefetch, Q

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.api.serializers.test_case import (
    TestCaseIssueSerializer,
    TestCaseModuleSerializer,
    TestCaseSerializer,
    TestCaseStepSerializer,
    TestCaseWriteSerializer,
)
from plane.app.testcase_validity import check_authoring, describe_reasons
from plane.app.views.test_case.base import (
    error_entry,
    object_body,
    project_or_404,
    serializer_error_response,
    stale_record_response,
    validation_error_response,
)
from plane.core.authz import Action as AuthzAction
from plane.db.models import (
    Issue,
    TestCase,
    TestCaseIssue,
    TestCaseModule,
    TestCaseStep,
)
from plane.db.testcase_numbering import is_valid_module_code
from plane.service_principals.attribution import actor_id_for_request
from plane.utils.permissions.project import ProjectEntityPermission

from .base import BaseAPIView


def _is_uuid(value) -> bool:
    """True when the value looks like a UUID."""
    from uuid import UUID

    try:
        UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def project_admin(request, project_id) -> bool:
    """True when the caller may remove a row of the project.

    A human caller has to be an admin of the project. A service
    principal is judged by the authz chain instead: the ``delete``
    action of the matrix needs the ADMIN role cap (20), and the
    ownership step of that chain lifts the restriction of a member-tier
    grant. ``request.user`` of a service-principal request is the human
    the token is filed under, so the membership of that human says
    nothing about the authority of the principal.
    """
    if getattr(request, "_sp_principal", None) is not None:
        return True

    from plane.db.models import ProjectMember

    return ProjectMember.objects.filter(
        workspace__slug=request.workspace_slug,
        member=request.user,
        project_id=project_id,
        role=20,
        is_active=True,
    ).exists()


class ProjectScopedAuthzMixin:
    """Resolve the row the ownership step judges inside the project of the URL.

    The base mixin loads the row by its id alone. A row of another
    project would then answer the ownership step with *its* project, and
    the grant of the project in the URL would never be consulted. The
    lookup is scoped to the project and the workspace of the request, so
    the row the engine judges is the row the view would write.
    """

    def get_authz_resource(self, request, action):
        model = getattr(self, "authz_resource_model", None)
        if model is None:
            return None
        key = self.kwargs.get(getattr(self, "authz_resource_lookup", "pk"))
        if not key:
            return None
        return model.objects.filter(
            pk=key,
            project_id=self.kwargs.get("project_id"),
            workspace__slug=self.kwargs.get("slug"),
        ).first()


class UpdateOnExistingRowMixin:
    """A POST that acts on an existing row is an update, not a create.

    The engine derives the action from the HTTP method, and ``create``
    skips the per-resource ownership step — a POST that links a work item
    to a case, that adds a case to a batch, that attaches a file to a
    record or that closes a batch acts on a row that already exists, so
    it has to be judged as an update.
    """

    def get_authz_action(self, request, action):
        if request.method == "POST":
            return AuthzAction.UPDATE
        return action


def replace_steps(case, steps, project, actor_id=None):
    """Replace the step table of a case with the list the client sent.

    The step numbers are assigned by the server (1, 2, 3 …). The old
    rows are removed **hard**: the unique constraint on
    ``(test_case, sequence_id)`` does not ignore the soft-deleted rows,
    so a soft delete would keep the numbers reserved and the replacement
    could not be written at all.

    Every row is written with the attribution of the request — the bot of
    a service principal, the user of a human. The attribution is pinned
    through ``created_by_id``, the kwarg ``BaseModel.save`` honours: the
    write of an ORM helper does not know the actor of the request.
    """
    TestCaseStep.all_objects.filter(test_case=case).delete()
    for index, step in enumerate(steps, start=1):
        row = TestCaseStep(
            test_case=case,
            project=project,
            workspace=project.workspace,
            sequence_id=index,
            action=step.get("action", ""),
            expected_result=step.get("expected_result", ""),
        )
        row.save(created_by_id=actor_id)
    # ``updated_at`` is ``auto_now``: the write moves it to now.
    case.save(created_by_id=actor_id, update_fields=["updated_at", "updated_by"])


class TestCaseModuleEndpoint(BaseAPIView):
    """The module registry of a project.

    A module code stands for one group of functions and is registered at
    the head of the project's case set; a case may only use a code that
    is registered here.
    """

    resource_type = "test_case"

    permission_classes = [ProjectEntityPermission]

    def get(self, request, slug, project_id):
        modules = TestCaseModule.objects.filter(
            project_id=project_id, workspace__slug=slug
        ).order_by("code")
        return Response(
            TestCaseModuleSerializer(modules, many=True).data,
            status=status.HTTP_200_OK,
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


class TestCaseListCreateEndpoint(BaseAPIView):
    """The cases of a project.

    Reading a case needs a project member, writing one needs a member
    of the project. A service principal goes through the same chain; a
    member-tier one may only write the cases it created itself.
    """

    resource_type = "test_case"

    permission_classes = [ProjectEntityPermission]

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

    def get(self, request, slug, project_id):
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

    def post(self, request, slug, project_id):
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

        # The server checks the mandatory items itself; the client is not
        # part of the contract.
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


class TestCaseDetailEndpoint(ProjectScopedAuthzMixin, BaseAPIView):
    """One case of a project."""

    resource_type = "test_case"

    permission_classes = [ProjectEntityPermission]

    # The row a PATCH/DELETE owns: the ownership step of the authz chain
    # compares the row's creator with the bot of the service principal.
    authz_resource_model = TestCase

    def get_queryset(self):
        return TestCase.objects.filter(
            project_id=self.kwargs.get("project_id"),
            workspace__slug=self.kwargs.get("slug"),
        ).select_related("module", "created_by", "created_by__avatar_asset__workspace")

    def get(self, request, slug, project_id, pk):
        case = self.get_queryset().filter(pk=pk).first()
        if case is None:
            return Response({"error": "Test case not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(TestCaseSerializer(case).data, status=status.HTTP_200_OK)

    def patch(self, request, slug, project_id, pk):
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

    def delete(self, request, slug, project_id, pk):
        case = self.get_queryset().filter(pk=pk).first()
        if case is None:
            return Response({"error": "Test case not found"}, status=status.HTTP_404_NOT_FOUND)
        if not project_admin(request, project_id):
            return Response(
                {"error": "Only a project admin can delete a test case."},
                status=status.HTTP_403_FORBIDDEN,
            )
        case.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class TestCaseStepEndpoint(ProjectScopedAuthzMixin, BaseAPIView):
    """The step table of one case.

    ``PUT`` replaces the whole table — the client sends the list it
    wants to have. The server checks that the table has at least one
    step and that every step carries an action and an expected result.
    """

    resource_type = "test_case"

    permission_classes = [ProjectEntityPermission]

    # The write acts on the case the step table belongs to.
    authz_resource_model = TestCase
    authz_resource_lookup = "test_case_id"

    def _case(self, slug, project_id, test_case_id):
        return TestCase.objects.filter(
            pk=test_case_id,
            project_id=project_id,
            workspace__slug=slug,
        ).first()

    def get(self, request, slug, project_id, test_case_id):
        case = self._case(slug, project_id, test_case_id)
        if case is None:
            return Response({"error": "Test case not found"}, status=status.HTTP_404_NOT_FOUND)
        steps = case.steps.all().order_by("sequence_id")
        return Response(
            TestCaseStepSerializer(steps, many=True).data,
            status=status.HTTP_200_OK,
        )

    def put(self, request, slug, project_id, test_case_id):
        case = self._case(slug, project_id, test_case_id)
        if case is None:
            return Response({"error": "Test case not found"}, status=status.HTTP_404_NOT_FOUND)

        body = object_body(request)
        if isinstance(body, Response):
            return body

        steps = body.get("steps")
        if steps is None:
            steps = []

        # This endpoint takes the raw body (the step table is replaced as
        # a whole), so the shape is checked here: a table that is not a
        # list of objects is a refused payload, not a crash.
        if not isinstance(steps, list) or any(not isinstance(step, dict) for step in steps):
            return Response(
                {
                    "error": "The payload is not valid.",
                    "code": "invalid_payload",
                    "errors": [
                        error_entry(
                            "invalid_payload",
                            "steps",
                            "The step table must be a list of objects with an "
                            "'action' and an 'expected_result'.",
                        )
                    ],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

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


class TestCaseIssueEndpoint(ProjectScopedAuthzMixin, UpdateOnExistingRowMixin, BaseAPIView):
    """The links between a case and the work items it belongs to."""

    resource_type = "test_case"

    permission_classes = [ProjectEntityPermission]

    # The write acts on the case the link belongs to.
    authz_resource_model = TestCase
    authz_resource_lookup = "test_case_id"

    def _case(self, slug, project_id, test_case_id):
        return TestCase.objects.filter(
            pk=test_case_id,
            project_id=project_id,
            workspace__slug=slug,
        ).first()

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

    def post(self, request, slug, project_id, test_case_id):
        case = self._case(slug, project_id, test_case_id)
        if case is None:
            return Response({"error": "Test case not found"}, status=status.HTTP_404_NOT_FOUND)

        body = object_body(request)
        if isinstance(body, Response):
            return body

        issue_id = body.get("issue_id")
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

        link = TestCaseIssue(
            test_case=case,
            issue=issue,
            project=case.project,
            workspace=case.workspace,
        )
        try:
            link.save(created_by_id=actor_id_for_request(request))
        except DjangoValidationError as exc:
            return validation_error_response(exc, "The link could not be created.")

        return Response(
            TestCaseIssueSerializer(link).data,
            status=status.HTTP_201_CREATED,
        )


class TestCaseIssueDetailEndpoint(ProjectScopedAuthzMixin, BaseAPIView):
    """One link between a case and a work item."""

    resource_type = "test_case"

    permission_classes = [ProjectEntityPermission]

    # The write acts on the case the link belongs to.
    authz_resource_model = TestCase
    authz_resource_lookup = "test_case_id"

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
