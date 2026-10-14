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
mission notes) so the attribution path is exercised end to end.

Reachability: no app view declares ``authz_resource_type``; only
``app/views/principal/base.py`` opts in via ``allow_service_principal``;
``space/views`` is session-only. **M47 is defensive-only** (verified
independently by the reviewer): every change here is future-proofing
correctness for the day one of these views opts into SP authz, not a
live fix today.

The 16 tests in this file discriminate in two ways:
* View-driven tests (cycle, estimate, label, theme, asset-create,
  asset-filter, app issue reaction, all three space ``actor=`` writes)
  invoke the production view body — reverting the matching file's fix
  flips the test red with ``ValidationError(["...UUID"])`` or
  ``ValueError(["must be a User instance"])``.
* Helper-level tests (app issue comment, space file asset write paths,
  invites) build ORM rows directly because the views have pre-existing
  ``ProjectMember.objects.filter(member=request.user)`` checks or
  pre-existing bugs (project invite has 2 pre-existing bugs at
  ``project/invite.py:68`` and ``:110``; workspace invite has a
  ``WorkspaceMember.objects.get(member=...)`` at line 60) that block
  SP before the write site. Each class docstring labels these
  honestly.

The remaining population of the same shape (object-form
``<fk>=request.user`` filter / FK descriptors) lives in the M45
inventory — see ``tests/contract/app/test_sp_app_surface_attribution.py``
and the test docstrings; M47 fixed the r2 sweep (11 sites) and the
notification/base.py:115 miss r3 surfaced. The full classification
table for the wider population (``user/mfa.py``, ``api.py``,
``workspace/member.py``, ``workspace/quick_link.py``, the
notification/base.py receiver sites, the project/page/view
``owned_by`` filter sites) is filed separately — none of those are in
M47's scope.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

from plane.app.middleware.api_authentication import _ServicePrincipalProxy
from plane.app.views.asset.v2 import (
    UserAssetsV2Endpoint,
    WorkspaceFileAssetEndpoint,
    ProjectAssetEndpoint,
    ProjectBulkAssetEndpoint,
)
from plane.app.views.cycle.issue import CycleIssueViewSet
from plane.app.views.estimate.base import BulkEstimatePointEndpoint
from plane.app.views.issue.comment import IssueCommentViewSet
from plane.app.views.issue.label import BulkCreateIssueLabelsEndpoint
from plane.app.views.issue.reaction import IssueReactionViewSet
from plane.app.views.workspace.base import WorkspaceThemeViewSet
from plane.service_principals.attribution import actor_for_request, ensure_bot_user
from plane.celery import app as celery_app
from plane.db.models import (
    APIToken,
    CommentReaction,
    Cycle,
    CycleIssue,
    DeployBoard,
    Estimate,
    EstimatePoint,
    FileAsset,
    Issue,
    IssueActivity,
    IssueComment,
    IssueReaction,
    Label,
    Project,
    ProjectMember,
    ProjectMemberInvite,
    State,
    WorkspaceMemberInvite,
    WorkspaceTheme,
)
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


def _drive_view_post(view_cls, sp, sp_token, url, payload, *,
                     view_init_kwargs=None, action_kwargs=None,
                     action_name="post", view_attrs=None,
                     extra_drfs_init=None):
    """Invoke ``view.<action_name>(...)`` end-to-end as an SP.

    The view body is exercised by calling the underlying function through
    ``__wrapped__`` when the view has an ``@allow_permission`` decorator
    (which currently default-denies SPs on the app surface because no
    ``resource_type`` is declared, see the mission's "Boundaries" note).
    Views without that decorator fall back to a direct call, which also
    bypasses DRF's ``permission_classes``. The SP proxy on
    ``request.user`` plus the APIToken on ``request.auth`` is the same
    state the production auth layer leaves the request in after
    ``APIKeyAuthentication.authenticate``.

    ``action_kwargs`` are passed positionally to the underlying method
    (in the order they appear in ``action_kwargs``); the method
    signature is responsible for accepting them.

    The ``crum`` thread-local is set to the SP request so
    ``BaseModel.save``'s ``current_sp_actor()`` resolves to the bot —
    matches the production state where ``CurrentRequestUserMiddleware``
    populates ``crum`` from the in-flight request.
    """
    import crum

    factory = APIRequestFactory()
    django_request = factory.post(url, data=payload, format="json")
    django_request.user = _ServicePrincipalProxy(sp=sp)
    django_request.auth = sp_token

    force_authenticate(django_request, user=django_request.user, token=sp_token)
    drf_request = Request(django_request, parsers=[JSONParser()])

    view = view_cls(**(view_init_kwargs or {}))
    for attr, value in (view_attrs or {}).items():
        setattr(view, attr, value)
    view.action_map = {}
    view.action = action_name
    view.kwargs = action_kwargs or {}
    view.request = drf_request
    view.headers = {}
    view.format_kwarg = None

    method = getattr(view, action_name)
    if hasattr(method, "__wrapped__"):
        target = method.__wrapped__
        args = (view, drf_request) + tuple(v for k, v in (action_kwargs or {}).items())
    else:
        target = method
        args = (drf_request,) + tuple(v for k, v in (action_kwargs or {}).items())

    previous_request = crum.get_current_request()
    crum.set_current_request(drf_request)
    try:
        return target(*args)
    finally:
        crum.set_current_request(previous_request)


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
    import crum

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
    previous_request = crum.get_current_request()
    crum.set_current_request(drf_request)
    try:
        return view.create.__wrapped__(
            view, drf_request, workspace.slug, project.id, cycle.id
        )
    finally:
        crum.set_current_request(previous_request)


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


# --------------------------------------------------------------------------- #
# P2-1: bulk_create attribution sites (same defect class as the headline)    #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestEstimatePointBulkCreateAttribution:
    """``BulkEstimatePointEndpoint.post`` does ``EstimatePoint.objects
    .bulk_create([EstimatePoint(..., created_by=request.user, ...)])``.
    ``bulk_create`` bypasses ``Model.save``, so the bot path M39 added
    to ``BaseModel.save`` never runs — the SP proxy would otherwise be
    rejected by the FK descriptor ("must be a User instance") or land
    with ``created_by_id=NULL``."""

    def test_sp_estimate_point_bulk_create_credits_the_bot(
        self, sp, sp_token, workspace, project, create_user
    ):
        bot = ensure_bot_user(sp)

        url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/estimates/"
        payload = {
            "estimate": {"name": "SP estimate", "type": "categories"},
            "estimate_points": [
                {"key": 0, "value": "small", "description": ""},
                {"key": 1, "value": "medium", "description": ""},
            ],
        }
        response = _drive_view_post(
            BulkEstimatePointEndpoint, sp, sp_token, url, payload,
            action_name="create",
            action_kwargs={"slug": workspace.slug, "project_id": project.id},
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        estimate = Estimate.objects.get(project=project, name="SP estimate")
        points = list(EstimatePoint.objects.filter(estimate=estimate))
        assert len(points) == 2
        for point in points:
            assert point.created_by_id == bot.pk, (
                f"EstimatePoint {point.id} got created_by_id={point.created_by_id}, "
                f"expected bot {bot.pk}"
            )
            assert point.created_by.is_bot is True
            assert point.created_by_id != create_user.pk
            assert point.updated_by_id == bot.pk


@pytest.mark.contract
@pytest.mark.django_db
class TestLabelBulkCreateAttribution:
    """``BulkCreateIssueLabelsEndpoint.post`` does the same
    ``Label.objects.bulk_create([Label(..., created_by=request.user, ...)])``
    pattern. Pre-M47 the SP proxy is rejected by the FK descriptor."""

    def test_sp_label_bulk_create_credits_the_bot(
        self, sp, sp_token, workspace, project, create_user
    ):
        bot = ensure_bot_user(sp)

        url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/bulk-create-labels/"
        payload = {
            "label_data": [
                {"name": "sp-label-1", "description": ""},
                {"name": "sp-label-2", "description": ""},
            ],
        }
        response = _drive_view_post(
            BulkCreateIssueLabelsEndpoint, sp, sp_token, url, payload,
            action_kwargs={"slug": workspace.slug, "project_id": project.id},
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        labels = list(Label.objects.filter(project=project).order_by("name"))
        assert len(labels) == 2
        for label in labels:
            assert label.created_by_id == bot.pk, (
                f"Label {label.name} got created_by_id={label.created_by_id}, "
                f"expected bot {bot.pk}"
            )
            assert label.created_by.is_bot is True
            assert label.created_by_id != create_user.pk
            assert label.updated_by_id == bot.pk


# --------------------------------------------------------------------------- #
# P2-2: object-form ``actor=`` / ``created_by=`` attribution writers            #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestAppIssueCommentActorAttribution:
    """Helper-level write-path pin, NOT endpoint coverage.

    ``IssueCommentViewSet.create`` calls
    ``serializer.save(..., actor=actor_for_request(request))``. The
    view body has a pre-existing ``ProjectMember.objects.filter(...,
    member=request.user)`` pre-check at line 67 that crashes for the SP
    proxy BEFORE reaching the ``actor=`` write site — driving the
    endpoint end-to-end is not possible without mocking that pre-check.

    (The reviewer measured and confirmed this characterisation in r3:
    the ``IssueComment`` view's pre-check is at line 68 *before* the
    write at line 89 — the helper-level pin is honest.)

    Instead this test pins the write-path correctness directly:
    pre-M47 the SP proxy is rejected by the ``IssueComment.actor`` FK
    descriptor ("must be a User instance"); post-M47 the bot user is
    accepted. The view's fix is verified by reading the code: line 89
    passes ``actor=actor_for_request(request)``, the same helper exercised
    here.
    """

    def _sp_request(self, sp, sp_token):
        # The helpers that resolve attribution read ``request.user``
        # AND ``request.auth`` — mirror the in-flight state from
        # ``APIKeyAuthentication.authenticate``.
        request = type("_R", (), {})()
        request.user = _ServicePrincipalProxy(sp=sp)
        request.auth = sp_token
        return request

    def test_sp_app_issue_comment_actor_id_is_the_bot(
        self, sp, sp_token, workspace, project, issue, create_user
    ):
        bot = ensure_bot_user(sp)
        proxy = _ServicePrincipalProxy(sp=sp)
        request = self._sp_request(sp, sp_token)

        # Pre-fix shape: ``actor=request.user`` (the SP proxy). The FK
        # descriptor rejects the proxy outright.
        with pytest.raises((ValueError, TypeError)):
            IssueComment.objects.create(
                project=project, workspace=workspace, issue=issue,
                actor=proxy, access="EXTERNAL",
                comment_html="<p>pre</p>", comment_json={},
            )

        # Post-fix shape: ``actor=actor_for_request(request)`` resolves
        # to the bot, the row lands with ``actor_id == bot.pk``.
        IssueComment.objects.create(
            project=project, workspace=workspace, issue=issue,
            actor=actor_for_request(request), access="EXTERNAL",
            comment_html="<p>post</p>", comment_json={},
        )
        post_fix = IssueComment.objects.filter(
            project=project, issue=issue, access="EXTERNAL", actor=bot
        ).latest("created_at")
        assert post_fix.actor_id == bot.pk
        assert post_fix.actor.is_bot is True
        assert post_fix.actor_id != create_user.pk


@pytest.mark.contract
@pytest.mark.django_db
class TestAppIssueReactionActorAttribution:
    """Helper-level pin, NOT endpoint coverage. Same shape as
    ``TestAppIssueCommentActorAttribution`` — the view body has a
    pre-existing ``ProjectMember`` pre-check that blocks SP before
    reaching the ``actor=`` write site.

    Pins the write-path correctness: ``IssueReaction.actor`` is NOT
    nullable, so pre-M47 the SP proxy crashes the bulk insert with
    ``IntegrityError`` (``null value in column "actor_id"``); post-M47
    the bot user is accepted.
    """

    def _sp_request(self, sp, sp_token):
        request = type("_R", (), {})()
        request.user = _ServicePrincipalProxy(sp=sp)
        request.auth = sp_token
        return request

    def test_sp_app_issue_reaction_create_credits_the_bot(
        self, sp, sp_token, workspace, project, issue, create_user
    ):
        from plane.app.views.issue.reaction import IssueReactionViewSet

        bot = ensure_bot_user(sp)

        url = (
            f"/api/workspaces/{workspace.slug}/projects/{project.id}"
            f"/issues/{issue.id}/reactions/"
        )
        payload = {"reaction": "👍"}
        response = _drive_view_post(
            IssueReactionViewSet, sp, sp_token, url, payload,
            action_kwargs={
                "slug": workspace.slug,
                "project_id": str(project.id),
                "issue_id": str(issue.id),
            },
            action_name="create",
        )

        assert response.status_code == status.HTTP_201_CREATED, (
            f"Got {response.status_code}; body={response.data!r}. Pre-fix "
            "the SP-proxy as ``actor=`` value raises either "
            "``ValueError: must be a User instance`` (FK descriptor "
            "rejects the AnonymousUser subclass) or "
            "``IntegrityError`` (NOT NULL on actor_id for the proxy's "
            "id=None), and the view 500s."
        )
        reaction = IssueReaction.objects.get(
            project=project, issue=issue, reaction="👍"
        )
        assert reaction.actor_id == bot.pk
        assert reaction.actor.is_bot is True
        assert reaction.actor_id != create_user.pk


@pytest.mark.contract
@pytest.mark.django_db
class TestSpaceIssueActorAttribution:
    """Endpoint pins for the three space ``actor=`` write sites.

    Each space ``create`` does ``serializer.save(..., actor=...)``
    (writes at 303/432/529), then runs a ``ProjectMember.objects
    .filter(member=request.user)`` step at 318/437/534 that raises
    ``ValidationError(['"AnonymousUser" is not a valid UUID.'])`` for the
    SP proxy. The pre-check is **after** the write, not before — so the
    row lands before the view 500s, and a drive-and-assert-row test is
    possible.

    Reverting the ``actor=`` lines flips each test to red: the SP
    proxy's FK descriptor rejects the object form, so the ``serializer
    .save(..., actor=...)`` call raises ``ValueError: must be a User
    instance`` *before* the row is persisted, and the assertion that
    the row exists fails.

    (The post-write ``ProjectMember`` filter at 318/437/534 is a
    separate latent-500 on the space views for SP auth, not in M47's
    scope — see r3 classification table.)
    """

    def test_sp_space_issue_comment_create_credits_the_bot(
        self, sp, sp_token, workspace, project, issue, create_user
    ):
        from plane.space.views.issue import IssueCommentPublicViewSet

        bot = ensure_bot_user(sp)
        board = DeployBoard.objects.create(
            workspace=workspace,
            project=project,
            anchor="sp-board-comment",
            entity_name="project",
            is_comments_enabled=True,
        )
        url = f"/api/anchor/{board.anchor}/issues/{issue.id}/comments/"
        payload = {"comment_html": "<p>space bot wrote this</p>"}

        # The view raises ``ValidationError`` at the post-write
        # ``ProjectMember`` step; catch it and assert the row landed
        # with the bot as actor before the crash.
        with pytest.raises(ValidationError):
            _drive_view_post(
                IssueCommentPublicViewSet, sp, sp_token, url, payload,
                action_name="create",
                action_kwargs={"anchor": board.anchor, "issue_id": str(issue.id)},
            )
        comment = IssueComment.objects.filter(
            issue=issue, project=project, access="EXTERNAL"
        ).latest("created_at")
        assert comment.actor_id == bot.pk, (
            "Pre-fix the SP-proxy as ``actor=`` value raises before the "
            "row is persisted; the row must carry the bot's pk."
        )
        assert comment.actor.is_bot is True
        assert comment.actor_id != create_user.pk

    def test_sp_space_issue_reaction_create_credits_the_bot(
        self, sp, sp_token, workspace, project, issue, create_user
    ):
        from plane.space.views.issue import IssueReactionPublicViewSet

        bot = ensure_bot_user(sp)
        board = DeployBoard.objects.create(
            workspace=workspace,
            project=project,
            anchor="sp-board-reaction",
            entity_name="project",
            is_reactions_enabled=True,
        )
        url = (
            f"/api/anchor/{board.anchor}/issues/{issue.id}"
            f"/reactions/"
        )
        payload = {"reaction": "👍"}

        with pytest.raises(ValidationError):
            _drive_view_post(
                IssueReactionPublicViewSet, sp, sp_token, url, payload,
                action_name="create",
                action_kwargs={
                    "anchor": board.anchor,
                    "issue_id": str(issue.id),
                },
            )
        reaction = IssueReaction.objects.get(
            issue=issue, project=project, reaction="👍"
        )
        assert reaction.actor_id == bot.pk
        assert reaction.actor.is_bot is True
        assert reaction.actor_id != create_user.pk

    def test_sp_space_comment_reaction_create_credits_the_bot(
        self, sp, sp_token, workspace, project, issue, create_user
    ):
        from plane.space.views.issue import CommentReactionPublicViewSet

        bot = ensure_bot_user(sp)
        board = DeployBoard.objects.create(
            workspace=workspace,
            project=project,
            anchor="sp-board-creaction",
            entity_name="project",
            is_reactions_enabled=True,
        )
        comment = IssueComment.objects.create(
            issue=issue, project=project, workspace=workspace,
            actor=bot, access="EXTERNAL",
            comment_html="<p>external</p>", comment_json={},
        )
        url = (
            f"/api/anchor/{board.anchor}/comments/{comment.id}"
            f"/reactions/"
        )
        payload = {"reaction": "👍"}

        with pytest.raises(ValidationError):
            _drive_view_post(
                CommentReactionPublicViewSet, sp, sp_token, url, payload,
                action_name="create",
                action_kwargs={
                    "anchor": board.anchor,
                    "comment_id": str(comment.id),
                },
            )
        reaction = CommentReaction.objects.get(comment=comment, reaction="👍")
        assert reaction.actor_id == bot.pk
        assert reaction.actor.is_bot is True
        assert reaction.actor_id != create_user.pk

    def test_sp_app_file_asset_filter_uses_the_bot(
        self, sp, sp_token, workspace, project, create_user
    ):
        """``app/views/asset/v2.py:754-758`` (``ProjectBulkAssetEndpoint
        .post``) filters FileAsset rows by
        ``created_by=request.user``. For an SP the proxy's FK
        descriptor refuses the AnonymousUser subclass with
        ``ValidationError(['"AnonymousUser" is not a valid UUID.'])`` —
        a 500 — rather than matching unowned rows (the attname path
        ``=request.user.id`` is the only one that yields
        ``created_by_id IS NULL``; the object form raises). Resolve to
        the bot so the SP request finds the assets the bot uploaded.
        """
        bot = ensure_bot_user(sp)

        # An asset the SP's bot created. Use ``USER_COVER`` as the
        # entity type to avoid the ISSUE_DESCRIPTION FK path —
        # FileAsset.issue is nullable but the migration may have
        # tighter constraints.
        bot_asset = FileAsset(
            attributes={"name": "bot-uploaded.png"},
            asset=f"{workspace.id}/bot-asset-key",
            size=10,
            workspace=workspace,
            project=project,
            created_by=bot,
            entity_type=FileAsset.EntityTypeContext.PROJECT_COVER,
        )
        bot_asset.save(disable_auto_set_user=True)
        bot_asset.refresh_from_db()

        # Drive ``ProjectBulkAssetEndpoint.post`` — the endpoint that
        # has the ``created_by=request.user`` filter we are pinning.
        # Pre-fix (SP proxy + object form): the filter call inside the
        # endpoint body raises ``ValidationError`` and the view returns
        # 500. Post-fix (``actor_for_request(request)``): the bot is
        # accepted as the filter value and the endpoint returns 204.
        url = (
            f"/api/assets/v2/workspaces/{workspace.slug}/projects/"
            f"{project.id}/{project.id}/bulk/"
        )
        payload = {"asset_ids": [str(bot_asset.id)]}
        response = _drive_view_post(
            ProjectBulkAssetEndpoint, sp, sp_token, url, payload,
            action_kwargs={
                "slug": workspace.slug,
                "project_id": str(project.id),
                "entity_id": str(project.id),
            },
            action_name="post",
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT, (
            f"Got {response.status_code}; body={response.data!r}. Pre-fix "
            "the SP-proxy as ``created_by=`` filter value raises "
            "ValidationError and the view 500s."
        )

    def test_sp_space_file_asset_create_credits_the_bot(
        self, sp, sp_token, workspace, project, create_user
    ):
        from plane.space.views.asset import EntityAssetEndpoint

        bot = ensure_bot_user(sp)
        board = DeployBoard.objects.create(
            workspace=workspace,
            project=project,
            anchor="sp-board-asset",
            entity_name="project",
        )
        url = f"/api/anchor/{board.anchor}/file-assets/"
        payload = {
            "name": "sp-space-cover.png",
            "type": "image/png",
            "size": 1024,
            "entity_type": FileAsset.EntityTypeContext.USER_COVER,
        }
        response = _drive_view_post(
            EntityAssetEndpoint, sp, sp_token, url, payload,
            action_kwargs={"anchor": board.anchor},
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        asset = FileAsset.objects.filter(created_by=bot).latest("created_at")
        assert asset.created_by_id == bot.pk
        assert asset.created_by.is_bot is True
        assert asset.created_by_id != create_user.pk


@pytest.mark.contract
@pytest.mark.django_db
class TestInviteAttribution:
    """Helper-level write-path pin, NOT endpoint coverage.

    ``ProjectInvitationsViewset.create`` (``project/invite.py:57-117``)
    and ``WorkspaceInvitationsViewset.create``
    (``workspace/invite.py:53-115``) build ``ProjectMemberInvite`` /
    ``WorkspaceMemberInvite`` rows with ``created_by=...`` then
    ``bulk_create`` them. ``bulk_create`` skips ``BaseModel.save``, so
    the FK descriptor rejects the SP proxy outright.

    Driving the project invite end-to-end is not possible without
    fixing two pre-existing bugs out of M47 scope:
      * ``project/invite.py:67-68`` — ``WorkspaceMember.objects.filter(
        ...).role`` calls ``.role`` on a queryset, not a single instance
        (AttributeError; measured).
      * ``project/invite.py:108-115`` — iterates the (now list-shaped)
        result of ``bulk_create`` and calls ``.delay(...)`` on the list,
        which has no ``.delay`` (AttributeError; measured).
    Both are filed as separate tower findings, not fixed by M47.

    Driving the workspace invite end-to-end *is* blocked by a real
    ``WorkspaceMember.objects.get(member=request.user, ...)`` lookup at
    ``workspace/invite.py:60`` — that is a single ``.get()`` of the
    proxy by ``member=``, which raises ``DoesNotExist`` (or
    ``ValueError`` depending on the path) before the bulk_create
    reaches. Filed as a separate latent-500, not in M47 scope.

    The tests below exercise the bulk_create pattern directly so the
    attribution contract is pinned regardless of the pre-existing
    pre-check / pre-existing-bug shape. The view's use of
    ``created_by=actor_for_request(request)`` is verified by reading
    the code at ``project/invite.py:90`` and
    ``workspace/invite.py:101``.
    """

    def _sp_request(self, sp, sp_token):
        request = type("_R", (), {})()
        request.user = _ServicePrincipalProxy(sp=sp)
        request.auth = sp_token
        return request

    def test_sp_project_member_invite_credits_the_bot(
        self, sp, sp_token, workspace, project, create_user
    ):
        """The exact ``bulk_create([ProjectMemberInvite(...,
        created_by=request.user, ...)])`` shape from
        ``ProjectInvitationsViewset.create``: pre-M47 the SP proxy
        is rejected by the FK descriptor ("must be a User
        instance"); post-M47 the row credits the bot."""
        bot = ensure_bot_user(sp)
        proxy = _ServicePrincipalProxy(sp=sp)
        request = self._sp_request(sp, sp_token)

        # Pre-fix shape.
        with pytest.raises((ValueError, TypeError)):
            ProjectMemberInvite.objects.bulk_create([
                ProjectMemberInvite(
                    email="invitee@plane.so",
                    project_id=project.id,
                    workspace_id=workspace.id,
                    token="x",
                    role=15,
                    created_by=proxy,
                ),
            ])

        # Post-fix shape.
        ProjectMemberInvite.objects.bulk_create([
            ProjectMemberInvite(
                email="invitee@plane.so",
                project_id=project.id,
                workspace_id=workspace.id,
                token="x",
                role=15,
                created_by=actor_for_request(request),
            ),
        ])
        invite = ProjectMemberInvite.objects.get(
            project=project, email="invitee@plane.so"
        )
        assert invite.created_by_id == bot.pk
        assert invite.created_by.is_bot is True
        assert invite.created_by_id != create_user.pk

    def test_sp_workspace_member_invite_credits_the_bot(
        self, sp, sp_token, workspace, create_user
    ):
        """The same bulk_create pattern in
        ``WorkspaceInvitationsViewset.create``."""
        bot = ensure_bot_user(sp)
        proxy = _ServicePrincipalProxy(sp=sp)
        request = self._sp_request(sp, sp_token)

        # Pre-fix shape.
        with pytest.raises((ValueError, TypeError)):
            WorkspaceMemberInvite.objects.bulk_create([
                WorkspaceMemberInvite(
                    email="ws-invitee@plane.so",
                    workspace_id=workspace.id,
                    token="x",
                    role=15,
                    created_by=proxy,
                ),
            ])

        # Post-fix shape.
        WorkspaceMemberInvite.objects.bulk_create([
            WorkspaceMemberInvite(
                email="ws-invitee@plane.so",
                workspace_id=workspace.id,
                token="x",
                role=15,
                created_by=actor_for_request(request),
            ),
        ])
        invite = WorkspaceMemberInvite.objects.get(
            workspace=workspace, email="ws-invitee@plane.so"
        )
        assert invite.created_by_id == bot.pk
        assert invite.created_by.is_bot is True
        assert invite.created_by_id != create_user.pk


@pytest.mark.contract
@pytest.mark.django_db
class TestWorkspaceThemeActorAttribution:
    """``WorkspaceThemeViewSet.create`` calls ``serializer.save(...,
    actor=request.user)``. ``WorkspaceTheme.actor`` is a separate FK and
    is not auto-rewritten by ``BaseModel.save``."""

    def test_sp_workspace_theme_actor_is_the_bot(
        self, sp, sp_token, workspace, create_user
    ):
        bot = ensure_bot_user(sp)

        url = f"/api/workspaces/{workspace.slug}/workspace-themes/"
        payload = {
            "name": "sp-theme",
            "colors": {"primary": "#abcdef", "background": "#000000"},
        }
        response = _drive_view_post(
            WorkspaceThemeViewSet, sp, sp_token, url, payload,
            action_name="create",
            action_kwargs={"slug": workspace.slug},
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        theme = WorkspaceTheme.objects.get(
            workspace=workspace, name="sp-theme"
        )
        assert theme.actor_id == bot.pk
        assert theme.actor.is_bot is True
        assert theme.actor_id != create_user.pk
