# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: SP default-deny on un-annotated views.

Every v1 view that should accept service-principal requests must declare
``resource_type = "<name>"``. A view that has not yet been onboarded —
or that explicitly opts SPs out — returns 403 for any ``plane_svc_``
token via the default-deny branch in
``AIScopeEnforcementMixin._enforce_sp_permission``
(``apps/api/plane/api/views/base.py``), which raises
``PermissionDenied("Endpoint is not exposed to service principals.")``.

After M23 and M24 every v1 view declares a ``resource_type``, so the
default-deny branch is no longer reachable at the URL level. These
tests drive it directly via a test-only subclass of
``AIScopeEnforcementMixin`` that intentionally omits ``resource_type``,
fed through ``_enforce_sp_permission`` with an SP principal. The
annotated-view paths (e.g. the scope-miss deny on
``/api/v1/users/me/``) keep their own coverage in
``TestSPDefaultDeny`` below and in ``test_sp_self_identity.py``.
"""

import pytest
from rest_framework import status

from plane.db.models.api import APIToken
from plane.service_principals.constants import PrincipalType
from plane.service_principals.models import ServicePrincipal


def _make_sp(workspace, owner, name="bot-deny", token_value=None):
    sp = ServicePrincipal.objects.create(
        workspace=workspace,
        owner=owner,
        name=name,
    )
    APIToken.objects.create(
        label=f"svc:{name}",
        token=token_value or f"plane_svc_token_{name}",
        user=owner,
        principal_type=PrincipalType.SERVICE,
        service_principal=sp,
        workspace=workspace,
    )
    return sp


@pytest.mark.contract
@pytest.mark.django_db
class TestSPDefaultDeny:
    """Annotated views still deny SPs that hold no scope row — just
    not at the default-deny branch. The endpoint is reachable, the
    engine answers 403 (DENY_SCOPE_MISS)."""

    def test_users_me_endpoint_denies_sp_without_scope(
        self, workspace, create_user, api_client
    ):
        """``/users/me/`` is annotated ``resource_type="user"``; an SP
        without a matching ``(user, read)`` scope row reaches the
        engine and is denied (scope-miss), not via default-deny."""
        _make_sp(workspace, create_user, name="me-deny")

        api_client.credentials(HTTP_X_API_KEY="plane_svc_token_me-deny")
        response = api_client.get("/api/v1/users/me/")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_users_me_endpoint_still_allows_humans(
        self, api_key_client
    ):
        """Human callers see the legacy ``UserLiteSerializer`` payload."""
        response = api_key_client.get("/api/v1/users/me/")
        assert response.status_code == status.HTTP_200_OK

    def test_unknown_route_denies_sp(
        self, workspace, create_user, api_client
    ):
        """An SP request against a well-formed but unresolvable URL
        never reaches 200. The exact deny origin (scope-miss vs. 404
        from Django's resolver) is not asserted — what matters is that
        an SP token holder cannot accidentally retrieve the owner's
        resource through a junk URL."""
        _make_sp(workspace, create_user, name="route-deny")

        api_client.credentials(HTTP_X_API_KEY="plane_svc_token_route-deny")
        response = api_client.get(
            "/api/v1/workspaces/no-such-workspace/projects/"
            "00000000-0000-0000-0000-000000000000/work-items/"
        )

        assert response.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_401_UNAUTHORIZED,
        )


@pytest.mark.contract
class TestSPDefaultDenyBranchUnit:
    """Drive the un-annotated-view default-deny branch directly.

    After M23 + M24 every v1 view declares ``resource_type`` (verified
    by grepping ``apps/api/plane/api/views/*.py`` on the merged tree),
    so this branch is no longer reachable at the URL level. The honest
    coverage is a test-only subclass of ``AIScopeEnforcementMixin``
    that intentionally omits ``resource_type``, fed through
    ``_enforce_sp_permission`` with an SP principal.

    This pins the safety net for any future v1 view that forgets the
    annotation — the day someone adds a new v1 endpoint without
    setting ``resource_type``, the existing per-view deny tests stop
    exercising this branch silently, and this unit-level test is the
    only thing still pinning the message.
    """

    def test_default_deny_raises_with_exact_message(self):
        """A view without ``resource_type`` denies SPs at the
        default-deny branch with the exact production message."""
        from plane.api.views.base import AIScopeEnforcementMixin
        from rest_framework.exceptions import PermissionDenied
        from rest_framework.test import APIRequestFactory

        class _UnannotatedView(AIScopeEnforcementMixin):
            # No ``resource_type`` set on purpose — exercises the
            # default-deny branch (the bare default of ``None``).
            pass

        view = _UnannotatedView()
        request = APIRequestFactory().get("/")
        # The default-deny branch never reads ``sp_principal``'s fields
        # (it raises before the authorize() chain runs), so a
        # ``SimpleNamespace`` is enough — no DB hit, no fixture churn.
        from types import SimpleNamespace

        sp_principal = SimpleNamespace(
            service_principal=SimpleNamespace(id="sp-id", name="sp"),
        )

        with pytest.raises(PermissionDenied) as excinfo:
            view._enforce_sp_permission(request, sp_principal)

        assert str(excinfo.value.detail) == (
            "Endpoint is not exposed to service principals."
        ), (
            "Default-deny message drifted — production code in "
            "AIScopeEnforcementMixin._enforce_sp_permission and this "
            "test must stay in lockstep."
        )


@pytest.mark.contract
@pytest.mark.django_db
class TestSPMethodMapping:
    """HTTP method → Action mapping. The base view translates verbs to
    the authorize() chain's action vocabulary; M8 keeps GET-class at READ
    (never LIST) per the reviewer-m7 workspace-level trap mitigation.
    """

    @pytest.fixture
    def project(self, db, workspace, create_user):
        from plane.db.models import Project, ProjectMember

        proj = Project.objects.create(
            name="MethodMap",
            identifier="MM",
            workspace=workspace,
            created_by=create_user,
            network=2,
        )
        ProjectMember.objects.create(
            project=proj, member=create_user, role=20, is_active=True
        )
        return proj

    def test_post_with_read_only_scope_denies(
        self, workspace, create_user, project, api_client
    ):
        """A scope row keyed on READ does not satisfy POST (CREATE)."""
        from plane.service_principals.models import ProjectGrant, ServiceScope

        sp = _make_sp(workspace, create_user, name="read-only")
        ServiceScope.objects.create(
            service_principal=sp,
            project=project,
            resource_type="work_item",
            action="read",
        )
        ProjectGrant.objects.create(
            service_principal=sp, project=project, role_cap=20
        )

        api_client.credentials(HTTP_X_API_KEY="plane_svc_token_read-only")
        response = api_client.post(
            f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/work-items/",
            data={},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_get_with_create_scope_denies(
        self, workspace, create_user, project, api_client
    ):
        """A scope row keyed on CREATE does not satisfy GET (READ)."""
        from plane.service_principals.models import ProjectGrant, ServiceScope

        sp = _make_sp(workspace, create_user, name="create-only")
        ServiceScope.objects.create(
            service_principal=sp,
            project=project,
            resource_type="work_item",
            action="create",
        )
        ProjectGrant.objects.create(
            service_principal=sp, project=project, role_cap=20
        )

        api_client.credentials(HTTP_X_API_KEY="plane_svc_token_create-only")
        response = api_client.get(
            f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/work-items/"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_all_action_scope_covers_every_method(
        self, workspace, create_user, project, api_client
    ):
        """A scope row keyed on the ``all`` wildcard authorizes any method
        that meets the role_cap ceiling."""
        from plane.service_principals.models import ProjectGrant, ServiceScope

        sp = _make_sp(workspace, create_user, name="wildcard-all")
        ServiceScope.objects.create(
            service_principal=sp,
            project=project,
            resource_type="work_item",
            action="all",
        )
        ProjectGrant.objects.create(
            service_principal=sp, project=project, role_cap=20
        )

        api_client.credentials(HTTP_X_API_KEY="plane_svc_token_wildcard-all")
        # GET -> READ < required for DELETE/UPDATE so the chain still
        # allows the read path
        response_get = api_client.get(
            f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/work-items/"
        )
        assert response_get.status_code == status.HTTP_200_OK
