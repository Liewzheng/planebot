# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract test: the shared ``action=`` of ``allow_permission`` may not
downgrade a write (M75 r1, P2-2).

``allow_permission`` gained an optional ``action`` so that an endpoint
whose verb does not describe its write can say what the write really is
(``POST .../test-runs/<id>/cases/`` replaces the case set of a batch —
an update of the batch, not a create). The helper is shared with three
follow-up missions, so the declaration has to be safe by construction:

* the engine applies the per-resource ownership step to ``update`` and
  ``delete`` only — a write judged as a read would skip it;
* the role floor of ``read`` is GUEST against MEMBER for the writes — a
  write judged as a read would slip past that too.

A service principal that patches a case a human created must therefore
still be refused, whatever the endpoint declares.
"""

from __future__ import annotations

import pytest
from rest_framework import status
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, APIClient

from plane.app.permissions import ROLE, allow_permission
from plane.app.views.base import BaseAPIView
from plane.core.authz import Action as AuthzAction
from plane.core.authz import ResourceType
from plane.db.models import (
    APIToken,
    Project,
    ProjectMember,
    TestCase,
    TestCaseModule,
)
from plane.service_principals.constants import PrincipalType
from plane.service_principals.models import (
    ProjectGrant,
    ServicePrincipal,
    ServiceScope,
)


class _DowngradeView(BaseAPIView):
    """A view that declares a weaker action than its verb says."""

    allow_service_principal = True

    @allow_permission(
        [ROLE.ADMIN, ROLE.MEMBER],
        level="PROJECT",
        resource_type=ResourceType.TEST_CASE,
        action=AuthzAction.READ,
        resource_model=TestCase,
        resource_lookup="pk",
    )
    def patch(self, request, slug, project_id, pk):
        return Response({"downgraded": True})



@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="Action Guard Project",
        identifier="AGP",
        workspace=workspace,
        created_by=create_user,
        network=2,
    )
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    return project


@pytest.fixture
def module(db, project):
    return TestCaseModule.objects.create(
        project=project,
        workspace=project.workspace,
        code="CLI",
        description="The command line interface of pbot",
    )


@pytest.fixture
def sp(db, workspace, create_user):
    return ServicePrincipal.objects.create(
        workspace=workspace,
        owner=create_user,
        name="action-guard-bot",
    )


@pytest.fixture
def sp_token(db, sp):
    return APIToken.objects.create(
        user=sp.owner,
        label="svc:action-guard-bot",
        user_type=1,
        is_service=True,
        principal_type=PrincipalType.SERVICE,
        service_principal=sp,
        workspace=sp.workspace,
        token="plane_svc_action_guard_bot",
    )


@pytest.fixture
def sp_granted(db, sp, project):
    for action in ("read", "create", "update"):
        ServiceScope.objects.create(
            service_principal=sp,
            project=project,
            resource_type="test_case",
            action=action,
        )
    ProjectGrant.objects.create(
        service_principal=sp,
        project=project,
        role_cap=15,
        is_active=True,
    )
    return sp


@pytest.mark.contract
@pytest.mark.django_db
class TestActionGuard:
    def _drive(self, workspace, project, token, pk):
        factory = APIRequestFactory()
        request = factory.patch(
            f"/api/workspaces/{workspace.slug}/projects/{project.id}/test-cases/{pk}/",
            {},
            format="json",
            HTTP_X_API_KEY=token.token,
        )
        return _DowngradeView.as_view()(request, slug=workspace.slug, project_id=project.id, pk=pk)

    def test_a_write_that_declares_a_read_is_refused(
        self, workspace, project, module, sp_token, sp_granted, create_user
    ):
        """The declaration may not weaken the write."""
        case = TestCase.objects.create(
            project=project,
            module=module,
            title="A case a human wrote",
            purpose="p",
            object_overview="o",
            priority="P1",
            preconditions="c",
            environment="e",
            created_by=create_user,
        )
        case.save(disable_auto_set_user=True)

        response = self._drive(workspace, project, sp_token, case.id)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_the_same_view_is_reachable_with_the_human_path(
        self, workspace, project, module, create_user
    ):
        """The guard is about the authz action, not about the view."""
        client = APIClient()
        client.force_authenticate(user=create_user)
        case = TestCase.objects.create(
            project=project,
            module=module,
            title="A case a human wrote",
            purpose="p",
            object_overview="o",
            priority="P1",
            preconditions="c",
            environment="e",
            created_by=create_user,
        )
        case.save(disable_auto_set_user=True)

        factory = APIRequestFactory()
        request = factory.patch(
            f"/api/workspaces/{workspace.slug}/projects/{project.id}/test-cases/{case.id}/",
            {},
            format="json",
        )
        request.user = create_user
        request._force_auth_user = create_user
        response = _DowngradeView.as_view()(
            request, slug=workspace.slug, project_id=project.id, pk=case.id
        )
        assert response.status_code == status.HTTP_200_OK
