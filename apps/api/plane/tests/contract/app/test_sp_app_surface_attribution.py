# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: SP write attribution on the app + space surface (M47 / PLANE-90).

M39 gave every service principal a shadow bot ``User`` and M41 wired the v1
write views to it. M47 sweeps the app surface (``app/views/**`` and the
public board surface ``space/views/**``) where the same row was still
stamped with the SP's human owner — or, worse, with ``NULL`` (because
``bulk_create`` bypasses ``BaseModel.save`` and ``request.user.id`` is
``None`` for the SP proxy).

The headline bug is
``apps/api/plane/app/views/cycle/issue.py:265-277``: the cycle-issue
``bulk_create`` set ``created_by_id=request.user.id`` directly, which an
SP request would land as ``NULL``. The cycle activity row's ``actor_id``
had the same problem — ``actor_id=str(self.request.user.id)`` is the
string ``"None"`` for an SP, and the celery task then persists the
activity row stamped with ``NULL``.

The tests below drive the cycle-issue view with an SP proxy user through
the production ``CycleIssueViewSet.create`` body — bypassing the
``@allow_permission`` decorator (which currently default-denies SPs on
``app/views/**`` because no ``resource_type`` is declared yet, see the
mission notes) so the attribution path is exercised end to end. Two
contract pins:

* ``CycleIssue.created_by_id`` is the SP's bot, never the owner.
* ``IssueActivity.actor_id`` for the cycle-activity row is the bot.

Both are red without M47 (cycle row stamped ``NULL``, activity stamped
``NULL``) and green with M47.
"""

from __future__ import annotations

import pytest
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

from plane.app.middleware.api_authentication import _ServicePrincipalProxy
from plane.app.views.cycle.issue import CycleIssueViewSet
from plane.celery import app as celery_app
from plane.db.models import (
    APIToken,
    Cycle,
    CycleIssue,
    Issue,
    IssueActivity,
    Project,
    ProjectMember,
    State,
)
from plane.service_principals.attribution import ensure_bot_user
from plane.service_principals.constants import PrincipalType
from plane.service_principals.models import ServicePrincipal


# --------------------------------------------------------------------------- #
# Fixtures / helpers                                                          #
# --------------------------------------------------------------------------- #


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="App Attribution Project",
        identifier="AAP",
        workspace=workspace,
        created_by=create_user,
        network=2,
    )
    ProjectMember.objects.create(
        project=project, member=create_user, role=20, is_active=True
    )
    return project


@pytest.fixture
def state(db, project, workspace, create_user):
    return State.objects.create(
        name="Todo",
        project=project,
        workspace=workspace,
        group="backlog",
        default=True,
    )


@pytest.fixture
def issue(db, project, workspace, state, create_user):
    return Issue.objects.create(
        name="App attribution issue",
        workspace=workspace,
        project=project,
        state=state,
        created_by=create_user,
    )


@pytest.fixture
def cycle(db, project, workspace, create_user):
    return Cycle.objects.create(
        name="App attribution cycle",
        project=project,
        workspace=workspace,
        owned_by=create_user,
    )


@pytest.fixture
def sp(db, workspace, create_user):
    return ServicePrincipal.objects.create(
        workspace=workspace, owner=create_user, name="app-attrib-bot"
    )


@pytest.fixture
def sp_token(db, sp, workspace, create_user):
    return APIToken.objects.create(
        user=create_user,
        label="svc:app-attrib-bot",
        token="plane_svc_app_attrib",
        user_type=1,
        is_service=True,
        principal_type=PrincipalType.SERVICE,
        service_principal=sp,
        workspace=workspace,
    )


@pytest.fixture
def celery_eager():
    """Run celery tasks synchronously so IssueActivity rows land in the DB
    before the assertion reads them. Same shape as the fixture in
    test_issue_assignee_label_validation.py."""
    original = celery_app.conf.task_always_eager
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = False
    yield
    celery_app.conf.task_always_eager = original


def _cycle_issue_url(workspace_slug, project_id, cycle_id):
    return (
        f"/api/workspaces/{workspace_slug}/projects/{project_id}"
        f"/cycles/{cycle_id}/cycle-issues/"
    )


def _drive_cycle_issue_create(sp, sp_token, workspace, project, cycle, issue):
    """Invoke ``CycleIssueViewSet.create`` end-to-end as an SP.

    The app surface's cycle-issue view has no ``resource_type`` declared
    on its ``@allow_permission``, so an SP request would be denied at the
    authz layer today (see the mission's "Boundaries" note). To exercise
    the attribution path — which is what the bug is about — we drive the
    view body directly through ``__wrapped__``, after authenticating the
    test request as the SP proxy so ``actor_for_request`` resolves to
    the bot user.

    ``force_authenticate`` is the test-side bypass for DRF's authentication
    classes; the SP proxy on ``request.user`` plus the APIToken on
    ``request.auth`` is exactly the state the production auth layer
    leaves the request in after ``APIKeyAuthentication.authenticate``.
    """
    factory = APIRequestFactory()
    django_request = factory.post(
        _cycle_issue_url(workspace.slug, project.id, cycle.id),
        data={"issues": [str(issue.id)]},
        format="json",
    )
    django_request.user = _ServicePrincipalProxy(sp=sp)
    django_request.auth = sp_token

    # ``force_authenticate`` is the test-side bypass for DRF auth — it
    # mirrors what ``APIKeyAuthentication`` would have set after a real
    # X-Api-Key handshake.
    force_authenticate(django_request, user=django_request.user, token=sp_token)

    # The view body reads ``request.data``, which only DRF's ``Request``
    # wrapper exposes. The Django ``WSGIRequest`` from the factory still
    # carries the parsed body via ``request.POST``, but the view's
    # serializer-style access only works through ``Request``. The
    # default DRF parser set rejects ``application/json`` when
    # ``parser_classes`` is unset, so wire ``JSONParser`` in explicitly.
    drf_request = Request(django_request, parsers=[JSONParser()])

    view = CycleIssueViewSet()
    view.action_map = {}
    view.action = "create"
    view.kwargs = {
        "slug": workspace.slug,
        "project_id": str(project.id),
        "cycle_id": str(cycle.id),
    }
    view.request = drf_request
    view.headers = {}
    view.format_kwarg = None

    # ``@allow_permission`` denies SPs without ``resource_type``. The
    # attribution contract is what we're pinning, so call the underlying
    # function through ``__wrapped__`` — the ``allow_permission``
    # decorator leaves the original bound method reachable via the
    # ``functools.wraps`` machinery.
    return view.create.__wrapped__(view, drf_request, workspace.slug, project.id, cycle.id)


# --------------------------------------------------------------------------- #
# Headline: cycle-issue bulk_create attribution                                #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestCycleIssueBulkCreateAttribution:
    """``CycleIssueViewSet.create`` stamps ``created_by_id`` /
    ``updated_by_id`` directly because ``bulk_create`` bypasses
    ``BaseModel.save``'s ``current_sp_actor`` hook — pre-M47 the SP path
    stamped ``NULL``, leaving the cycle-issue row with no creator."""

    def test_sp_cycle_issue_credits_the_bot_not_the_owner(
        self, sp, sp_token, workspace, project, issue, cycle, create_user, celery_eager
    ):
        bot = ensure_bot_user(sp)

        response = _drive_cycle_issue_create(
            sp, sp_token, workspace, project, cycle, issue
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data

        cycle_issue = CycleIssue.objects.get(cycle=cycle, issue=issue)
        # The SP wrote the row — the bot owns it, the human does not.
        assert cycle_issue.created_by_id == bot.pk
        assert cycle_issue.created_by.is_bot is True
        assert cycle_issue.created_by.bot_type == "SERVICE_PRINCIPAL"
        assert cycle_issue.created_by_id != create_user.pk
        # ``bulk_create`` skipped ``BaseModel.save``, so this row was the
        # exact site of the headline regression. The companion
        # ``updated_by`` column is set on the same call and must follow.
        assert cycle_issue.updated_by_id == bot.pk

    def test_sp_cycle_activity_actor_id_is_the_bot(
        self, sp, sp_token, workspace, project, issue, cycle, create_user, celery_eager
    ):
        """The audit row for the cycle issue is credited to the bot, not
        the owner. Pre-M47 the ``actor_id`` kwarg was
        ``str(self.request.user.id)`` — ``"None"`` for an SP — so the
        activity row's ``actor_id`` ended up ``NULL``."""
        bot = ensure_bot_user(sp)

        response = _drive_cycle_issue_create(
            sp, sp_token, workspace, project, cycle, issue
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data

        activity = (
            IssueActivity.objects.filter(
                issue=issue,
                project=project,
                verb="created",
                field="cycles",
            )
            .order_by("-created_at")
            .first()
        )
        assert activity is not None, (
            "Expected an IssueActivity row for the cycle-activity.created event"
        )
        assert activity.actor_id == bot.pk
        assert activity.actor_id != create_user.pk

    def test_human_cycle_issue_credits_the_human(
        self, workspace, project, issue, cycle, create_user, session_client, celery_eager
    ):
        """Control: the human-session path keeps crediting the human.

        Forces the cycle issue endpoint with a session-authenticated
        user and asserts the cycle row's ``created_by`` is the human —
        proves the M47 fix did not change human attribution semantics.
        Pre-M47 this also passed (because ``request.user.id`` is the
        human's id for a session); post-M47 it still passes (because
        ``actor_id_for_request`` returns the human unchanged)."""
        url = _cycle_issue_url(workspace.slug, project.id, cycle.id)
        response = session_client.post(url, {"issues": [str(issue.id)]}, format="json")

        assert response.status_code == status.HTTP_201_CREATED, response.data

        cycle_issue = CycleIssue.objects.get(cycle=cycle, issue=issue)
        assert cycle_issue.created_by_id == create_user.pk
        assert cycle_issue.created_by.is_bot is False

        activity = (
            IssueActivity.objects.filter(
                issue=issue,
                project=project,
                verb="created",
                field="cycles",
            )
            .order_by("-created_at")
            .first()
        )
        assert activity is not None
        assert activity.actor_id == create_user.pk
