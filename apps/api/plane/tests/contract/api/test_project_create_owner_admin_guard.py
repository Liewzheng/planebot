# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: guard the v1 project-create ``role=20`` membership row
against service-principal requests (mission M49 / PLANE-110).

What the guard does. The v1 create handler used to do::

    ProjectMember.objects.create(
        project_id=serializer.instance.id, member=request.user, role=20
    )

unconditionally. For a human request ``request.user`` is the caller —
that's the right call. For a service-principal request ``request.user``
is the SP's **owner** (see ``APIKeyAuthentication._authenticate_service_token``),
so the unguarded line would silently expand a human's privileges because
the bot did work, with no record anywhere of how that membership got
there. The alternative (``member=actor_for_request(request)``) is also
wrong: that returns the SP's shadow bot user, which M39 forbids from
holding any ``ProjectMember`` row.

The guard lives in
``plane.api.views.project.add_creator_as_admin_if_human`` — kept as a
module-level helper so it is unit-testable on its own.

Permission-model reality. ``PROJECT`` is in ``WORKSPACE_LEVEL_RESOURCES``
and ``ALLOWED_WORKSPACE_LEVEL_ACTIONS`` is only ``{"read"}``, so the
engine's Q4 guard denies SP project writes at
``AIScopeEnforcementMixin._enforce_sp_permission`` before the view body
runs. The helper is therefore unreachable for an SP today. The guard
exists in defence of the day a future refactor (M31-style: a resource
type drifts out of the workspace-level set) makes the branch live
again.

Coverage honesty. Because Q4 fires before the view body, an SP cannot
reach ``add_creator_as_admin_if_human`` end-to-end without bypassing
the engine. The SP tests below drive the helper **directly** — pure
unit-level coverage, no HTTP layer, no authentication, no engine path.
The human control test goes through the real HTTP path so we know the
unguarded branch still behaves byte-for-byte for humans.

Companion test. M51 r1 folds in the missing attribution pin: an
SP-driven ``Project.objects.create`` must stamp ``created_by_id`` with
the SP's bot user, never the owner. That test
(``TestSPProjectAttribution`` below) drives the ORM directly with the
SP proxy bound to ``crum`` — same pattern ``test_sp_app_surface_attribution.py``
uses — and asserts the bot is credited. Together with the helper pin
above, the two tests form the *attribution* (created_by_id) and
*authorization* (role=20 row) halves of an SP-driven project-create.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.api.views.project import add_creator_as_admin_if_human
from plane.db.models import Project, ProjectMember, State
from plane.db.models.api import APIToken
from plane.service_principals.constants import PrincipalType
from plane.service_principals.models import ServicePrincipal


SP_NAME = "proj-creator-bot"


def _make_sp(workspace, owner, name=SP_NAME):
    """An SP with a service token, no scopes and no grants.

    The helper does not consult scopes or grants — what matters here is
    that ``sp_from_request`` can recognise the request as SP-driven, and
    that requires a live ``APIToken`` with ``principal_type=SERVICE``.
    """
    sp = ServicePrincipal.objects.create(workspace=workspace, owner=owner, name=name)
    APIToken.objects.create(
        user=owner,
        label=f"svc:{name}",
        token=f"plane_svc_token_{name}",
        user_type=1,
        is_service=True,
        principal_type=PrincipalType.SERVICE,
        service_principal=sp,
        workspace=workspace,
    )
    return sp


@pytest.mark.contract
@pytest.mark.django_db
class TestHumanPathUnchanged:
    """The human path must still register the creator as the project's
    admin — this is the explicit control test for the guard. The
    project list / membership-filtered read paths assume an active
    ``ProjectMember`` row for the caller, so breaking this would be a
    regression for every human who ever created a project through the
    API.
    """

    def test_human_creator_becomes_admin(
        self, api_key_client, workspace, create_user
    ):
        """Human creates a project → the calling human holds the
        ``role=20`` membership row, end-to-end through the HTTP path."""
        response = api_key_client.post(
            f"/api/v1/workspaces/{workspace.slug}/projects/",
            {"name": "Human Control", "identifier": "HC"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, (
            f"Got {response.status_code}: {response.data!r}"
        )

        project = Project.objects.get(id=response.data["id"])
        # Single, exact membership row for the human caller.
        creator_memberships = ProjectMember.objects.filter(
            project=project, member=create_user, role=20, is_active=True
        )
        assert creator_memberships.count() == 1
        # And the project row's ``created_by`` is the same human, as
        # ``BaseModel.save`` routes attribution.
        assert project.created_by_id == create_user.pk
        # Default workflow states are still created.
        assert State.objects.filter(project=project).count() == 5


@pytest.mark.contract
@pytest.mark.django_db
class TestCreatorAsAdminHelper:
    """Unit-level coverage of ``add_creator_as_admin_if_human``.

    The helper is called from the v1 create handler but lives at module
    level precisely so it can be exercised without going through the
    full HTTP path (the SP-driven full POST path crashes on the
    post-create re-fetch because ``visible_projects`` filters SP-not-
    granted projects out; see the module docstring). These tests
    construct the request object the helper expects and assert the
    resulting ``ProjectMember`` state.
    """

    def test_human_request_adds_caller_as_admin(
        self, workspace, create_user
    ):
        """The human path: ``request.user`` is the caller and becomes a
        ``role=20`` member of the new project."""
        project = Project.objects.create(
            name="Helper Human",
            identifier="HH",
            workspace=workspace,
            created_by=create_user,
        )

        # ``sp_from_request`` reads ``_sp_principal``, ``request.user``
        # and ``request.auth``. With none of those set to an SP marker
        # it resolves to ``None`` and the helper writes the membership
        # row. ``SimpleNamespace`` mirrors the shape without dragging
        # in a real DRF request, matching the pattern in
        # ``test_service_principal_attribution``.
        request = SimpleNamespace(user=create_user, auth=None)

        add_creator_as_admin_if_human(request, project)

        assert ProjectMember.objects.filter(
            project=project, member=create_user, role=20
        ).count() == 1

    def test_sp_request_skips_membership(
        self, workspace, create_user
    ):
        """The SP guard fires: an SP-driven request does NOT add the
        SP's owner as a project admin. This is the unit-level proof of
        mission M49's guard — it bypasses the engine Q4 gate on
        purpose, because that gate is what makes the branch
        end-to-end unreachable today.
        """
        sp = _make_sp(workspace, create_user, name=SP_NAME)
        bot_user = sp.ensure_bot_user()
        owner = sp.owner

        project = Project.objects.create(
            name="Helper SP",
            identifier="HS",
            workspace=workspace,
            created_by=owner,
        )

        # Mirror what ``APIKeyAuthentication._authenticate_service_token``
        # actually does on a real SP request — ``request.user`` is the
        # owner, ``request._sp_principal`` carries the principal, and
        # ``request.auth`` carries the SP token. ``sp_from_request``
        # resolves the SP via ``_sp_principal`` first and so returns
        # the SP without a database lookup.
        sp_principal = SimpleNamespace(service_principal=sp)
        request = SimpleNamespace(
            _sp_principal=sp_principal,
            user=owner,
            auth=f"plane_svc_token_{SP_NAME}",
        )

        add_creator_as_admin_if_human(request, project)

        # The guard's whole point: the SP's owner is *not* added as a
        # ``role=20`` ProjectMember of the project.
        assert not ProjectMember.objects.filter(
            project=project, member=owner, role=20
        ).exists(), (
            "Owner was added to the project as admin — the SP guard "
            "did not fire. ``request.user`` is the owner for SP "
            "requests, so the unguarded line would silently expand "
            "the owner's privileges."
        )

        # And M39's rule still holds: no ``ProjectMember`` row for the
        # bot user. If the guard had been written as ``member=
        # actor_for_request(request)`` instead of "skip when SP", the
        # bot row below would exist and the membership-filtered
        # project list would surface the bot's projects to the bot —
        # both forbidden.
        assert not ProjectMember.objects.filter(
            project=project, member=bot_user
        ).exists(), (
            "Bot user was added as a ProjectMember. M39 forbids bots "
            "from holding membership rows; the guard must skip, not "
            "swap the member to the bot."
        )

        # No ProjectMember rows at all.
        assert ProjectMember.objects.filter(project=project).count() == 0

    def test_helper_accepts_a_request_shaped_object(
        self, workspace, create_user
    ):
        """Pure-unit shape check: the helper does not require a fully
        wired DRF request. An object exposing ``.user`` is enough for
        the human path. Pins the function's contract against future
        refactors that pull in heavier request setup.
        """
        project = Project.objects.create(
            name="Helper Shape",
            identifier="HSP",
            workspace=workspace,
            created_by=create_user,
        )

        request = SimpleNamespace(user=create_user, auth=None)

        add_creator_as_admin_if_human(request, project)

        assert ProjectMember.objects.filter(
            project=project, member=create_user, role=20
        ).count() == 1


@pytest.mark.contract
@pytest.mark.django_db
class TestQ4GateStillDeniesSPProjectWrite:
    """Sanity: the Q4 gate still denies SP project writes today, so the
    helper the unit test above exercises is unreachable end-to-end.

    Coverage note: this test only asserts the status code and the
    absence of side effects. Removing the engine's workspace-level
    ``DENY_WORKSPACE_LEVEL_WRITE`` guard (the actual Q4 pin) would
    flip this test from ``403`` to a different ``403`` reason
    (``DENY_SCOPE_MISS``) — the status code alone does not catch
    that drift. The real Q4 classifier lives in
    ``tests/contract/test_authz_core.py::TestWorkspaceLevelResources``
    (the ``DENY_WORKSPACE_LEVEL_WRITE`` assertions on a workspace-wide
    ``project:create`` scope). Treat the test below as a smoke check,
    not as the Q4 guarantee.
    """

    def test_sp_create_project_denied_by_q4_gate(
        self, workspace, create_user
    ):
        _make_sp(workspace, create_user, name="q4-deny")

        sp_client = APIClient()
        sp_client.credentials(HTTP_X_API_KEY="plane_svc_token_q4-deny")

        response = sp_client.post(
            f"/api/v1/workspaces/{workspace.slug}/projects/",
            {"name": "Q4 Deny", "identifier": "QD"},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert Project.objects.count() == 0
        assert ProjectMember.objects.count() == 0


@pytest.mark.contract
@pytest.mark.django_db
class TestSPProjectAttribution:
    """An SP-driven ``Project.objects.create`` must stamp
    ``created_by_id`` with the SP's shadow bot user — never the human
    owner the service token is filed under.

    ``BaseModel.save`` reaches ``current_sp_actor()`` via the
    ``crum`` thread-local current request, so the assertion must run
    with the SP-shaped request bound to ``crum``. M49's guard (the
    helper test above) prevents a v1 SP-driven create from leaking an
    unwanted ``ProjectMember`` row, but nothing currently pins the
    *attribution* side: a regression that swapped the auto-set user
    to ``request.user`` (the owner) on a v1 SP-driven project-create
    would surface as a misattributed bot row in ``Project.created_by``
    and break every activity-feed / "what the SP did" surface that
    filters on the bot id — without any test failing. This class
    closes that hole at the layer the production code actually stamps.

    Q4 still denies an end-to-end v1 SP POST today, so the test drives
    the ORM write directly with the SP proxy on the request, the
    same pattern ``test_sp_app_surface_attribution.py`` uses.
    """

    def test_sp_driven_project_create_attributes_to_bot(
        self, workspace, create_user
    ):
        import crum
        from plane.app.middleware.api_authentication import _ServicePrincipalProxy

        sp = _make_sp(workspace, create_user, name="attrib-bot")
        bot_user = sp.ensure_bot_user()
        owner = sp.owner

        sp_principal = SimpleNamespace(service_principal=sp)
        sp_request = SimpleNamespace(
            _sp_principal=sp_principal,
            user=_ServicePrincipalProxy(sp=sp),
            auth=f"plane_svc_token_{SP_NAME}",
        )

        previous_request = crum.get_current_request()
        crum.set_current_request(sp_request)
        try:
            project = Project.objects.create(
                name="SP Attribution Project",
                identifier="SAP",
                workspace=workspace,
                # ``created_by`` intentionally omitted — ``BaseModel.save``
                # must read the SP proxy via ``current_sp_actor()`` and
                # stamp the bot. Hand-passing ``created_by=owner`` here
                # would defeat the test.
            )
        finally:
            crum.set_current_request(previous_request)

        # The attribution pin: bot, not owner.
        assert project.created_by_id == bot_user.id, (
            f"SP-driven Project.objects.create must set created_by_id to the "
            f"bot user; got {project.created_by_id} (owner id "
            f"{owner.pk}, bot id {bot_user.id})."
        )
        # Defensive: the owner is NOT credited.
        assert project.created_by_id != owner.pk, (
            "SP-driven project-create credited the SP's owner — the "
            "attribution path is broken (PLANE-90 / M47)."
        )
