# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: SP write notifications reach workspace owner, project owner, and
task cooperators (M52 / SPNOTIFY, with M53 follow-up for the cooperators exclude
shape and the missing-preference tolerance).

The user's complaint verbatim: "我的 SP 干活，要通知我" — when the bot writes, the
human who runs the workflow never hears about it. After M39/M41/M47 the *actor*
column on the notification row already credits the SP's bot (so "who did it" is
right); M52 widens only the *recipient* set.

Per the user, three classes must be notified on every SP write, even when the
recipient is not a ``ProjectMember`` and has not subscribed to the issue:

  1. workspace owner — ``Workspace.owner`` (single FK).
  2. project owner   — union of ``Project.project_lead`` and
                       ``ProjectMember(role=20, is_active=True)``. The schema
                       lets either field name "the project owner" and silently
                       taking only one would drop half the cases.
  3. task cooperator — every ``IssueSubscriber`` row for the issue (the original
                       code intersected with ``ProjectMember``, which silently
                       dropped non-member subscribers).

The widening is gated on the actor being a bot — human-actor writes keep the
legacy recipient path byte-for-byte. That is the human-path control test below.

The thirteen tests in this file are paired with the M52/M53 diff in
``plane/bgtasks/notification_task.py``. Each clause is pinned by one or
two tests (counted below); the M53 additions cover the bug-classes the
original test set missed, **plus** the M53 r2 row-counted second
delta (soft-delete propagation, P3-2) and the SQL NULL trap fix
(P2-1). The revert recipe is captured in the report (all numbers
measured at M53 r2):

  - reverting *both* M52 cooperators-filter exclusions (the bot guard
    and the project-membership guard together) flips **three** tests
    red (the two ``TestSpNotifyCooperatorFilterRegression`` tests and
    ``test_cooperators_queryset_excludes_only_project_local_inactive``).
    "Six" — the count in the prior version of this header — is the
    number of reds when *the whole M52 block* is disabled (``_spnotify_
    extend=False``), which is a different revert.
  - reverting *just* the project-membership guard flips **two** tests
    red: ``test_sp_create_excludes_ex_project_member_cooperator`` AND
    ``test_cooperators_queryset_excludes_only_project_local_inactive``
    (both pin the same ORM-level clause).
  - reverting *just* the bot guard flips **one** test red:
    ``test_sp_create_with_other_sp_bot_subscriber_still_notifies_human_
    cooperator`` (the ``DoesNotExist`` cascade hits its single
    discriminator subscriber).
  - reverting *just* the missing-preference tolerance flips **one**
    test red:
    ``test_sp_create_tolerates_missing_notification_preference``
    (every recipient silently loses its notification).
  - reverting the M53 helper back to the r2 ``.exclude(...)`` shape
    (or any other ``NOT (EXISTS(a) AND EXISTS(b))`` form that is not
    project-scoped on the ``is_active=False`` arm) flips **one** test
    red: ``test_cooperators_queryset_excludes_only_project_local_
    inactive``; the user-facing test
    ``test_sp_create_with_inactive_membership_in_other_project_still_
    notifies_cooperator`` stays green through legacy, which is the
    reviewer's "harmless in effect" finding.
  - reverting the M53 r2 ``member__isnull=False`` NULL guard flips
    **one** test red:
    ``test_cooperators_queryset_survives_null_member_row``.

Discriminator mechanics per test:

* ``test_sp_create_notifies_workspace_owner`` — drive ``notifications(...)``
  with ``actor_id=bot.pk`` against a workspace whose owner has *no* project
  membership and *no* ``IssueSubscriber`` row. The pre-M52 code emits zero
  ``Notification`` rows for that user; the M52 code emits one.
* ``test_sp_create_notifies_project_owner_via_project_lead`` — same, but the
  recipient is the project lead, not a project member, and has no
  ``IssueSubscriber`` row.
* ``test_sp_create_notifies_project_owner_via_admin_role`` — same, but the
  recipient is a ``ProjectMember(role=20, is_active=True)`` and is *not* the
  project lead and is *not* an ``IssueSubscriber``. Catches the half of the
  union that ``Project.project_lead`` alone would miss.
* ``test_sp_create_still_notifies_existing_subscriber`` — pre-existing
  ``IssueSubscriber`` rows continue to receive notifications after the M52
  widening. Pre-M52 this dropped non-member subscribers at the
  ``IssueSubscriber ∩ ProjectMember`` intersect; here we deliberately pick a
  subscriber who is *not* a project member, which is the user's "task
  cooperator" class.
* ``test_human_create_does_not_extend_recipients`` — control. A
  session-authenticated write must keep the legacy recipient set. Pre-M52
  this test passes (no extras); post-M52 it still passes because the
  widening is gated on ``actor.is_bot``.
* ``test_sp_create_does_not_duplicate_when_overlap`` — overlap between the
  four coverage classes (workspace owner + project lead + admin role
  member + IssueSubscriber) yields exactly one ``Notification``. The test
  covers the *dedup*; see the per-test docstring for the precise
  pre/post-M52 breakdown (the prior claim "Pre-M52: zero rows for
  ``ws_owner``" was wrong — the recipient set has one row on both
  sides, via the legacy IssueSubscriber ∩ ProjectMember intersect).
* ``test_sp_create_with_other_sp_bot_subscriber_still_notifies_human_cooperator``
  — **r2 regression** for the M52 r1 p1. When a non-acting bot
  (``is_bot=True``) ``IssueSubscriber`` row is on the issue, pre-fix the
  loop's ``UserNotificationPreference.objects.get`` raised
  ``DoesNotExist`` and the task's catch-all aborted, dropping every
  notification for the write. Post-fix: bots are hard-excluded from the
  cooperators set (this test pins that arm — reverting just *that*
  exclusion flips it red), and the preference lookup tolerates a missing
  row (pinned by
  ``test_sp_create_tolerates_missing_notification_preference``).
* ``test_sp_create_excludes_ex_project_member_cooperator`` — **r2 policy
  decision** test for the M52 r1 P2. A user who left the project
  (``ProjectMember.is_active=False``) but is still an active
  ``WorkspaceMember`` and has a stale ``IssueSubscriber`` row is *excluded*
  from the cooperators set (content-leak guard — see the report for the
  full rationale). Pre-fix (r1) the cooperators query took every
  ``IssueSubscriber`` row, so this user was added to the recipient set
  (base 0 → branch 1, reviewer-verified).
* ``test_cooperators_queryset_excludes_only_project_local_inactive`` —
  **M53 red-before/green-after** for the cooperators-exclude SQL shape.
  Drives the production helper ``_spnotify_cooperator_ids_for`` (and the
  queryset form ``_spnotify_cooperator_ids_qs``) directly. Pre-M53 the
  helper compiled to ``NOT (EXISTS(inactive-anywhere) AND
  EXISTS(row-in-this-project))`` (the r2 `.exclude(A, B)` form); the
  ``is_active`` arm is not project-scoped, so a user active in *this*
  project with an inactive row in *another* project gets wrongly dropped
  from the cooperators queryset. Post-M53 the exclude is rewritten as
  ``.exclude(subscriber__in=ProjectMember.objects.filter(
  project_id=THIS, is_active=False).values("member_id"))``, a single
  project-scoped subquery. The test asserts both the helper output and
  the compiled SQL shape; the user-facing discrimination path is
  ``test_sp_create_with_inactive_membership_in_other_project_still_
  notifies_cooperator``.
* ``test_sp_create_with_inactive_membership_in_other_project_still_
  notifies_cooperator`` — end-to-end mirror of the discriminator above.
  Passes on both r2 and M53 forms (the legacy path catches the user
  end-to-end — the reviewer's "harmless in effect" verdict) but pins
  the user-facing contract: an active member of *this* project with
  an inactive row in *another* must receive notifications.
* ``test_sp_create_tolerates_missing_notification_preference`` — **M53
  regression guard** for the missing-preference tolerance. Reverting
  *just* the tolerance (keeping the bot guard intact) flips this test
  red while leaving the other ten tests green — so this is the only
  test that pins the load-bearing role of the unsaved fallback
  ``UserNotificationPreference`` instance.

Each red-before/green-after pair is recorded by reverting only the named
clause in ``notification_task.py`` and re-running just this file; the
revert recipe is captured in the report.
"""

from __future__ import annotations

import json

import pytest
from django.core.serializers.json import DjangoJSONEncoder

from plane.app.serializers.issue import IssueActivitySerializer
from plane.bgtasks.notification_task import notifications
from plane.bgtasks.notification_task import (
    _spnotify_cooperator_ids_for,
    _spnotify_cooperator_ids_qs,
)
from plane.celery import app as celery_app
from plane.db.models import (
    Issue,
    IssueActivity,
    IssueSubscriber,
    Notification,
    Project,
    ProjectMember,
    State,
    User,
    UserNotificationPreference,
    Workspace,
    WorkspaceMember,
)


# --------------------------------------------------------------------------- #
# Shared fixtures                                                             #
# --------------------------------------------------------------------------- #


@pytest.fixture
def celery_eager():
    """Run celery tasks synchronously so the ``Notification`` rows land in the
    DB before the assertion reads them. Same shape as the fixtures in
    ``test_sp_app_surface_attribution.py`` and
    ``test_issue_assignee_label_validation.py``."""
    original_eager = celery_app.conf.task_always_eager
    original_propagates = celery_app.conf.task_eager_propagates
    celery_app.conf.task_always_eager = True
    # Surface any exception raised inside an eager task so the test can
    # discriminate a silent task failure (the notification task wraps its
    # body in ``except Exception: print(...)`` and would otherwise return
    # empty without telling us why).
    celery_app.conf.task_eager_propagates = True
    yield
    celery_app.conf.task_always_eager = original_eager
    celery_app.conf.task_eager_propagates = original_propagates


@pytest.fixture
def project(db, workspace, create_user):
    """A project owned by ``create_user`` (who is the workspace owner too —
    overridden in the per-test fixtures where a separate recipient is needed)."""
    project = Project.objects.create(
        name="Notify Project",
        identifier="NOT",
        workspace=workspace,
        created_by=create_user,
        network=2,
    )
    ProjectMember.objects.create(
        project=project, member=create_user, role=20, is_active=True
    )
    State.objects.create(
        name="Backlog",
        project=project,
        workspace=workspace,
        group="backlog",
        default=True,
    )
    return project


@pytest.fixture
def state(db, project, workspace):
    return State.objects.create(
        name="Todo",
        project=project,
        workspace=workspace,
        group="backlog",
        default=True,
    )


@pytest.fixture
def bot(db):
    """A bot ``User`` row that stands in for the SP's shadow bot user. The M39
    attribution layer provisions these automatically via ``ensure_bot_user``;
    here we mint one by hand so the test does not depend on the SP fixture
    chain (the notification task only reads ``actor.is_bot``)."""
    return User.objects.create(
        email="sp-bot@plane.so",
        username="sp_bot_user",
        first_name="SP",
        last_name="Bot",
        is_bot=True,
        bot_type="SERVICE_PRINCIPAL",
    )


@pytest.fixture
def ws_owner(db):
    """A standalone human who owns a fresh workspace — used as the "workspace
    owner is *not* the SP owner" probe. Distinct from ``create_user`` so the
    M52 widening can be observed end to end (the project has only one human
    member and that member is the SP's owner, ``create_user``)."""
    return User.objects.create(
        email="ws-owner@plane.so",
        username="ws_owner_human",
        first_name="Workspace",
        last_name="Owner",
    )


@pytest.fixture
def ws_owned(ws_owner):
    """A workspace owned by ``ws_owner`` (not by ``create_user``). All M52 SP-path
    tests use this so the workspace owner is *not* a member of the project and
    *not* an ``IssueSubscriber`` — the only path that delivers them a
    notification is the M52 widening."""
    workspace = Workspace.objects.create(
        name="SP Workspace",
        slug="sp-workspace",
        owner=ws_owner,
    )
    WorkspaceMember.objects.create(
        workspace=workspace, member=ws_owner, role=20
    )
    return workspace


@pytest.fixture
def project_in_ws(db, ws_owned, create_user):
    """A project inside ``ws_owned``. ``create_user`` (the SP's owner in the
    fixtures) is the project admin so the SP write path is allowed."""
    project = Project.objects.create(
        name="SP Project",
        identifier="SPP",
        workspace=ws_owned,
        created_by=create_user,
        network=2,
    )
    ProjectMember.objects.create(
        project=project, member=create_user, role=20, is_active=True
    )
    State.objects.create(
        name="Backlog",
        project=project,
        workspace=ws_owned,
        group="backlog",
        default=True,
    )
    return project


@pytest.fixture
def project_lead_user(db):
    """A standalone human with no project membership and no IssueSubscriber
    row — they will be set as ``Project.project_lead`` to test the
    project-lead half of the union."""
    return User.objects.create(
        email="project-lead@plane.so",
        username="project_lead_human",
        first_name="Project",
        last_name="Lead",
    )


@pytest.fixture
def project_admin_user(db):
    """A standalone human with ``ProjectMember(role=20)`` but no
    ``Project.project_lead`` and no ``IssueSubscriber`` row — they exercise the
    admin-role half of the project-owner union."""
    return User.objects.create(
        email="project-admin@plane.so",
        username="project_admin_human",
        first_name="Project",
        last_name="Admin",
    )


@pytest.fixture
def cooperator_user(db):
    """A standalone human who is an ``IssueSubscriber`` for the test issue but
    is *not* a project member. The pre-M52 code dropped this row at the
    ``IssueSubscriber ∩ ProjectMember`` intersect; the M52 widening picks it
    up explicitly."""
    return User.objects.create(
        email="cooperator@plane.so",
        username="cooperator_human",
        first_name="Task",
        last_name="Cooperator",
    )


def _make_issue(project, workspace, creator):
    """An ``Issue`` whose ``created_by`` is ``creator``. For SP tests
    ``creator`` is the bot; for human tests ``creator`` is the human."""
    return Issue.objects.create(
        name="Notify test issue",
        project=project,
        workspace=workspace,
        created_by=creator,
    )


def _issue_activity_dicts(issue, project, workspace, actor_id):
    """A single ``IssueActivity`` row for the ``issue.activity.created`` event,
    serialised exactly the way ``issue_activities_task.issue_activity`` does it
    before calling ``notifications.delay``. ``notifications`` deserialises the
    string back into the list-of-dicts shape its loop expects."""
    activity = IssueActivity.objects.create(
        issue=issue,
        project=project,
        workspace=workspace,
        comment="created the issue",
        verb="created",
        actor_id=actor_id,
    )
    activity.created_at = issue.created_at
    activity.save(update_fields=["created_at"])
    return IssueActivitySerializer([activity], many=True).data


def _drive_notifications(*, type, issue_id, project_id, actor_id, subscriber,
                         issue_activities_created, requested_data="{}",
                         current_instance=None):
    """Call the celery task synchronously. Mirrors the call site at
    ``bgtasks/issue_activities_task.py:1596`` (``notifications.delay(...)``).
    The ``celery_eager`` fixture sets ``task_always_eager=True``, so ``.delay``
    runs in-process."""
    notifications.delay(
        type=type,
        issue_id=str(issue_id),
        project_id=str(project_id),
        actor_id=str(actor_id),
        subscriber=subscriber,
        issue_activities_created=json.dumps(
            issue_activities_created, cls=DjangoJSONEncoder
        ),
        requested_data=requested_data,
        current_instance=current_instance,
    )


def _notification_receivers(issue_id):
    """Distinct human recipients for a given issue's notification rows."""
    return set(
        Notification.objects.filter(
            entity_name="issue",
            entity_identifier=issue_id,
        ).values_list("receiver_id", flat=True)
    )


# --------------------------------------------------------------------------- #
# SP path: workspace owner                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestSpNotifyWorkspaceOwner:
    def test_sp_create_notifies_workspace_owner(
        self, ws_owned, project_in_ws, bot, ws_owner, celery_eager
    ):
        """The workspace owner (``ws_owner``, who is *not* a project member and
        has no ``IssueSubscriber`` row) must receive a notification when the SP
        bot creates the work item.

        Pre-M52: ``notifications``'s recipient query intersects
        ``IssueSubscriber`` with ``ProjectMember``; with no rows, the
        intersection is empty and the workspace owner gets nothing. The
        assertion reads zero rows → red.

        Post-M52: the workspace owner is added to ``issue_subscribers`` via
        the M52 widening block; the recipient loop creates one
        ``Notification`` for them with ``triggered_by_id=bot.pk`` (the actor
        is still the bot — only the recipient set widens)."""
        issue = _make_issue(project_in_ws, ws_owned, bot)
        activities = _issue_activity_dicts(issue, project_in_ws, ws_owned, bot.pk)

        _drive_notifications(
            type="issue.activity.created",
            issue_id=issue.id,
            project_id=project_in_ws.id,
            actor_id=bot.pk,
            subscriber=True,
            issue_activities_created=activities,
        )

        receivers = _notification_receivers(issue.id)
        assert ws_owner.pk in receivers, (
            "Pre-M52 the workspace owner is dropped at the "
            "``IssueSubscriber ∩ ProjectMember`` intersect (zero rows, no "
            "extension). Post-M52 the workspace owner is added to the "
            "recipient set explicitly. If this assertion fails on the "
            "M52 branch the widening block at "
            "``notification_task.py:_spnotify_cooperator_ids_qs`` "
            "is no longer in effect."
        )
        # ``triggered_by`` is the bot — actor semantics unchanged.
        ws_owner_notifs = Notification.objects.filter(
            entity_name="issue",
            entity_identifier=issue.id,
            receiver_id=ws_owner.pk,
        )
        assert ws_owner_notifs.count() == 1
        assert ws_owner_notifs.first().triggered_by_id == bot.pk
        assert ws_owner_notifs.first().sender == "in_app:issue_activities:subscribed"


# --------------------------------------------------------------------------- #
# SP path: project owner (union of project_lead and admin role)              #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestSpNotifyProjectOwner:
    def test_sp_create_notifies_project_owner_via_project_lead(
        self, ws_owned, project_in_ws, project_lead_user, bot, celery_eager
    ):
        """The ``Project.project_lead`` (set to ``project_lead_user``) is *not*
        a project member and has no ``IssueSubscriber`` row. They must still
        be notified.

        This is the first half of the project-owner union. Pre-M52 the
        project lead was never in the recipient set; post-M52 they are
        included."""
        project_in_ws.project_lead = project_lead_user
        project_in_ws.save(update_fields=["project_lead"])
        issue = _make_issue(project_in_ws, ws_owned, bot)
        activities = _issue_activity_dicts(issue, project_in_ws, ws_owned, bot.pk)

        _drive_notifications(
            type="issue.activity.created",
            issue_id=issue.id,
            project_id=project_in_ws.id,
            actor_id=bot.pk,
            subscriber=True,
            issue_activities_created=activities,
        )

        receivers = _notification_receivers(issue.id)
        assert project_lead_user.pk in receivers, (
            "Project.project_lead is the project-owner half of the M52 union "
            "— silently dropping it would mean a project lead never hears "
            "about bot activity."
        )

    def test_sp_create_notifies_project_owner_via_admin_role(
        self, ws_owned, project_in_ws, project_admin_user, bot, celery_eager
    ):
        """A ``ProjectMember(role=20, is_active=True)`` who is *not* the
        project lead and has no ``IssueSubscriber`` row must still be
        notified.

        This is the second half of the project-owner union. Catches the
        failure mode where the M52 fix only looks at ``Project.project_lead``
        and forgets the admin-role tier — which is what every other
        permission class in the codebase keys on."""
        ProjectMember.objects.create(
            project=project_in_ws, member=project_admin_user, role=20, is_active=True
        )
        issue = _make_issue(project_in_ws, ws_owned, bot)
        activities = _issue_activity_dicts(issue, project_in_ws, ws_owned, bot.pk)

        _drive_notifications(
            type="issue.activity.created",
            issue_id=issue.id,
            project_id=project_in_ws.id,
            actor_id=bot.pk,
            subscriber=True,
            issue_activities_created=activities,
        )

        receivers = _notification_receivers(issue.id)
        assert project_admin_user.pk in receivers, (
            "ProjectMember(role=20, is_active=True) is the project-owner "
            "half of the M52 union that ``Project.project_lead`` alone "
            "would miss."
        )


# --------------------------------------------------------------------------- #
# SP path: existing subscriber (task cooperator)                              #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestSpNotifyExistingSubscriber:
    def test_sp_create_still_notifies_existing_subscriber(
        self, ws_owned, project_in_ws, cooperator_user, bot, celery_eager
    ):
        """A pre-existing ``IssueSubscriber`` row for a user who is *not* a
        project member must still receive a notification.

        Pre-M52 this case worked by accident (the user's existence in
        ``IssueSubscriber`` AND ``ProjectMember`` was never required — but
        the original intersect at line 284 dropped non-member subscribers).
        Post-M52 the explicit ``_spnotify_cooperator_ids`` query picks every
        ``IssueSubscriber`` row up regardless of project membership, so the
        non-member subscriber still gets notified."""
        issue = _make_issue(project_in_ws, ws_owned, bot)
        IssueSubscriber.objects.create(
            project=project_in_ws,
            issue=issue,
            subscriber=cooperator_user,
        )
        activities = _issue_activity_dicts(issue, project_in_ws, ws_owned, bot.pk)

        _drive_notifications(
            type="issue.activity.created",
            issue_id=issue.id,
            project_id=project_in_ws.id,
            actor_id=bot.pk,
            subscriber=True,
            issue_activities_created=activities,
        )

        receivers = _notification_receivers(issue.id)
        assert cooperator_user.pk in receivers, (
            "A pre-existing IssueSubscriber row must continue to receive "
            "notifications — the M52 widening must not drop this class."
        )
        cooperator_notifs = Notification.objects.filter(
            entity_name="issue",
            entity_identifier=issue.id,
            receiver_id=cooperator_user.pk,
        )
        assert cooperator_notifs.count() == 1


# --------------------------------------------------------------------------- #
# Human control: legacy recipient path byte-for-byte                          #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestHumanPathUnchanged:
    def test_human_create_does_not_extend_recipients(
        self, ws_owned, project_in_ws, bot, ws_owner, create_user, celery_eager
    ):
        """Control: a session-authenticated human write must keep the legacy
        recipient set. The workspace owner (``ws_owner``) is not a project
        member and has no ``IssueSubscriber`` row; under the legacy logic
        they do not receive a subscription-activity notification.

        Pre-M52: zero rows for ``ws_owner`` (the intersect is empty). The
        assertion passes.

        Post-M52: the widening block is gated on ``actor.is_bot``; the
        human actor (``create_user``) does not satisfy the predicate, so
        the widening block is skipped and the legacy path runs unchanged.
        The assertion still passes.

        If the gating is removed, the assertion flips to red because the
        workspace owner would now receive a notification they did not
        receive before — the human path is no longer byte-for-byte."""
        issue = _make_issue(project_in_ws, ws_owned, bot)
        activities = _issue_activity_dicts(issue, project_in_ws, ws_owned, bot.pk)

        # Drive with ``actor_id=create_user.pk`` (the human) so the widening
        # block must short-circuit on ``actor.is_bot`` and leave the legacy
        # recipient set unchanged.
        _drive_notifications(
            type="issue.activity.created",
            issue_id=issue.id,
            project_id=project_in_ws.id,
            actor_id=create_user.pk,
            subscriber=True,
            issue_activities_created=activities,
        )

        receivers = _notification_receivers(issue.id)
        assert ws_owner.pk not in receivers, (
            "Human-actor writes must keep the legacy recipient set. If the "
            "M52 widening block ran unconditionally the workspace owner "
            "would receive a notification that the pre-M52 code did not "
            "produce — this control test exists to catch exactly that."
        )


# --------------------------------------------------------------------------- #
# No duplicate notifications                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestSpNotifyNoDuplicates:
    def test_sp_create_does_not_duplicate_when_overlap(
        self, ws_owned, project_in_ws, bot, ws_owner, create_user, celery_eager
    ):
        """When the four M52 coverage classes overlap on a single user
        (``ws_owner`` is the workspace owner, *and* the project lead, *and*
        a ``ProjectMember(role=20, is_active=True)``, *and* an
        ``IssueSubscriber``), exactly one ``Notification`` row must land.

        Pre-M52: **one** row for ``ws_owner``. The legacy intersect
        ``IssueSubscriber ∩ ProjectMember`` already fires — ``ws_owner``
        is an active ``ProjectMember`` (role 20) for ``project_in_ws``
        and an ``IssueSubscriber``, so pre-M52 the recipient set has
        ``{ws_owner}`` and the loop creates one ``Notification``. Note:
        the earlier docstring claimed "Pre-M52: zero rows for ws_owner",
        which is false — the reviewer verified this directly (and the
        test passes on the pre-M52 base). The pre-M52 code did *not*
        extend to the workspace owner / project-lead arms; that gap is
        covered by ``test_sp_create_notifies_workspace_owner`` and the
        two project-owner tests, not by this one.

        Post-M52: the union of the four overlap arms collapses to a
        single UUID before the recipient loop runs; the loop emits one
        ``Notification`` per (activity, recipient) pair. With a single
        activity row in the test, the count is exactly one.

        What this test pins: the *dedup*. If a future change emits one
        ``Notification`` per arm (four per activity, here), this
        assertion flips to red. The single-row outcome is what the
        union-set guarantee delivers; per-class arms are covered by
        the corresponding per-class tests above.
        """
        ws_owner.refresh_from_db()
        # Make ``ws_owner`` satisfy every class at once: workspace owner
        # (already, from ``ws_owned``), project lead, admin ProjectMember,
        # and IssueSubscriber for the issue.
        project_in_ws.project_lead = ws_owner
        project_in_ws.save(update_fields=["project_lead"])
        ProjectMember.objects.create(
            project=project_in_ws, member=ws_owner, role=20, is_active=True
        )

        issue = _make_issue(project_in_ws, ws_owned, bot)
        IssueSubscriber.objects.create(
            project=project_in_ws,
            issue=issue,
            subscriber=ws_owner,
        )
        activities = _issue_activity_dicts(issue, project_in_ws, ws_owned, bot.pk)

        _drive_notifications(
            type="issue.activity.created",
            issue_id=issue.id,
            project_id=project_in_ws.id,
            actor_id=bot.pk,
            subscriber=True,
            issue_activities_created=activities,
        )

        ws_owner_notifs = Notification.objects.filter(
            entity_name="issue",
            entity_identifier=issue.id,
            receiver_id=ws_owner.pk,
        )
        assert ws_owner_notifs.count() == 1, (
            "Four overlapping classes must union into one UUID before the "
            "recipient loop runs — if the M52 widening block emits one "
            "Notification per arm, the loop double-counts and this "
            "assertion flips to red."
        )
        assert ws_owner_notifs.first().triggered_by_id == bot.pk
        # ``ws_owner`` is *not* the issue creator (the bot is), so the
        # sender falls through to ``subscribed`` even though ws_owner is the
        # project lead + admin ProjectMember — those roles do not flip the
        # sender label, only the creator / assignee check does.
        assert ws_owner_notifs.first().sender == "in_app:issue_activities:subscribed"


# --------------------------------------------------------------------------- #
# Cooperators-filter regression: the p1 from M52 r1                           #
# --------------------------------------------------------------------------- #
#
# M52 r1 found two failure modes that surfaced only after the widening:
#
#   1. ``IssueSubscriber`` rows can belong to *another* SP's shadow bot user.
#      Bots have no ``UserNotificationPreference`` row
#      (``db/models/user.py:299-301``: ``if created and not instance.is_bot``),
#      so the loop's unguarded ``.get(user_id=subscriber)`` raised
#      ``DoesNotExist``, the task's catch-all at ``notification_task.py:667``
#      aborted silently, and every notification for the write was dropped —
#      including the workspace owner and any human cooperators. Probed base
#      → branch: 1 human notification on base, 0 on the branch.
#
#   2. A human who left the project (ProjectMember.is_active=False) but kept
#      a stale IssueSubscriber row was added by the widening (the legacy
#      ``IssueSubscriber ∩ ProjectMember`` intersect had filtered them out).
#      The notification body has issue content, and the notifications list
#      API filters only by workspace+receiver, so an ex-project-member with
#      workspace access could read content from a project they were removed
#      from. Conscious policy decision: cooperators with an *inactive*
#      ProjectMember row for this project are dropped (content-leak guard);
#      cooperators with no ProjectMember row at all are kept (mission rule:
#      "even if not a ProjectMember"). See the report for the full rationale.
#
# The two regressions are pinned by the tests below, *separately*: each
# clause of the cooperators exclude is its own discriminator. The M53
# ORM-level tests (in ``TestSpNotifyCooperatorScopeIsProjectLocal`` and
# ``TestSpNotifyCooperatorNullGuard``) also touch the project-
# membership guard, so that clause is now pinned by *two* tests, not
# one — corrected at M53 r2.
#
#   * ``test_sp_create_with_other_sp_bot_subscriber_still_notifies_human_
#     cooperator`` pins the bot guard (``subscriber__is_bot=False``).
#     Reverting *only* the bot guard flips this test red with
#     ``DoesNotExist`` in the log; reverting *only* the
#     project-membership guard does not flip it.
#
#   * ``test_sp_create_excludes_ex_project_member_cooperator`` AND
#     ``test_cooperators_queryset_excludes_only_project_local_inactive``
#     both pin the project-membership guard
#     (``.exclude(member_project__project_id=...,
#     member_project__is_active=False)``/M53 helper). Reverting *only*
#     that exclusion flips **both** tests red; reverting *only* the
#     bot guard does not.
#
#   * ``test_cooperators_queryset_survives_null_member_row`` pins the
#     M53 r2 ``member__isnull=False`` NULL guard. Reverting *only* that
#     predicate flips this test red and leaves the others green.
#
# The M53 r2 reviewer's note 3 corrected the earlier claim that
# "Reverting either cooperators filter flips both tests to red" — that
# was wrong. Each test pins one clause; the project-membership guard
# happens to be pinned twice now because the M53 ORM discriminator and
# the M52 ex-member regression cover the same clause at different
# observability levels. The M53 tolerance test pins the missing-
# preference fallback in a separate class.


@pytest.fixture
def other_sp_bot(db):
    """A second shadow bot user, as if it belonged to a different SP. The
    M39 invariant says SP bots are never members, but the SP write path
    itself subscribes the acting bot as ``IssueSubscriber`` at
    ``notification_task.py:295-297`` — and that path runs for every SP on
    every write, so an IssueSubscriber row belonging to *another* SP's bot
    is in scope on this branch.

    The M52 r1 reviewer reproduced the regression by adding a row like this
    to the IssueSubscriber table; pre-fix it crashed the whole notification
    batch with ``UserNotificationPreference matching query does not
    exist.``"""
    return User.objects.create(
        email="other-sp-bot@plane.so",
        username="other_sp_bot_user",
        first_name="Other",
        last_name="Bot",
        is_bot=True,
        bot_type="SERVICE_PRINCIPAL",
    )


@pytest.fixture
def ex_project_member(db, ws_owned):
    """A human who is an active ``WorkspaceMember`` of ``ws_owned`` and was
    a ``ProjectMember`` of the test project but had ``is_active`` flipped to
    ``False`` by the standard removal endpoint at
    ``app/views/project/member.py:319-320``. They retain a stale
    ``IssueSubscriber`` row for the test issue.

    Per the M52 r1 P2 policy decision (see the report), this user is
    *excluded* from the cooperators set — even though they satisfy the
    workspace-owner / workspace-member half of the criteria, they have no
    read access to the project they left, so the notification body would
    leak issue content they cannot open."""
    user = User.objects.create(
        email="ex-member@plane.so",
        username="ex_member_human",
        first_name="Ex",
        last_name="Member",
    )
    WorkspaceMember.objects.create(
        workspace=ws_owned, member=user, role=20, is_active=True
    )
    return user


@pytest.mark.contract
@pytest.mark.django_db
class TestSpNotifyCooperatorFilterRegression:
    """Regression suite for the M52 r1 p1 — the cooperators arm admitted
    bot subscribers and let the unguarded preference lookup abort the
    whole notification batch. The two tests below are the discriminators:
    reverting the cooperators filter (or the preference-lookup tolerance)
    flips them to red on the r1 branch."""

    def test_sp_create_with_other_sp_bot_subscriber_still_notifies_human_cooperator(
        self,
        ws_owned,
        project_in_ws,
        bot,
        cooperator_user,
        other_sp_bot,
        celery_eager,
    ):
        """Pre-fix: ``other_sp_bot`` (a non-acting bot user) is an
        ``IssueSubscriber`` for the issue; the cooperators query picks it
        up; the loop's ``UserNotificationPreference.objects.get(user_id=
        subscriber)`` raises ``DoesNotExist`` because bots have no pref
        row; the task's catch-all aborts before ``bulk_create``. Every
        recipient — including the human cooperator and the workspace
        owner — silently gets 0 notifications.

        Post-fix: the cooperators filter excludes bots
        (``subscriber__is_bot=False``) and the preference lookup tolerates
        a missing row. The task completes and the human cooperator
        receives 1 notification. The bot itself stays out of the recipient
        set.

        Reversing either fix flips this test red. Pre-fix evidence was a
        human-PM-and-subscriber going from 1 → 0 on the r1 branch with
        ``DoesNotExist`` in the log."""
        issue = _make_issue(project_in_ws, ws_owned, bot)
        # Pre-existing human subscriber (the regression target).
        IssueSubscriber.objects.create(
            project=project_in_ws,
            issue=issue,
            subscriber=cooperator_user,
        )
        # The other SP's bot — the pre-fix crash trigger.
        IssueSubscriber.objects.create(
            project=project_in_ws,
            issue=issue,
            subscriber=other_sp_bot,
        )
        activities = _issue_activity_dicts(issue, project_in_ws, ws_owned, bot.pk)

        _drive_notifications(
            type="issue.activity.created",
            issue_id=issue.id,
            project_id=project_in_ws.id,
            actor_id=bot.pk,
            subscriber=True,
            issue_activities_created=activities,
        )

        receivers = _notification_receivers(issue.id)
        # The bot must not be a recipient — the cooperators filter
        # excludes it. (Bots can never authenticate and have no
        # ``UserNotificationPreference``, so they are not legitimate
        # notification endpoints.)
        assert other_sp_bot.pk not in receivers, (
            "Bots are never legitimate notification recipients — they "
            "have no UserNotificationPreference, cannot authenticate, "
            "and cannot act on the notification. The cooperators filter "
            "must hard-exclude them."
        )
        # The human cooperator must still receive their notification —
        # this is the actual regression. Pre-fix, the bot's missing
        # preference raised DoesNotExist, the task's catch-all aborted,
        # and the human silently got 0.
        assert cooperator_user.pk in receivers, (
            "Pre-fix the bot's missing UserNotificationPreference raised "
            "DoesNotExist, the task's catch-all aborted before "
            "Notification.objects.bulk_create, and the human cooperator "
            "silently got 0 notifications instead of 1. Reverting the "
            "cooperators filter or the preference-lookup tolerance "
            "flips this assertion to red."
        )

    def test_sp_create_excludes_ex_project_member_cooperator(
        self, ws_owned, project_in_ws, bot, ex_project_member, celery_eager
    ):
        """P2 conscious policy: a user who left the project (their
        ``ProjectMember.is_active`` is ``False``) but is still an active
        ``WorkspaceMember`` and has a stale ``IssueSubscriber`` row is
        *excluded* from the cooperators set.

        Rationale: the notification body carries issue name + identifier
        + state, the notifications list API filters only by
        ``workspace__slug`` + ``receiver_id``
        (``app/views/notification/base.py:39-46``), so a still-in-workspace
        ex-member can fetch content from a project they were removed from.
        We accept the narrower exception: a cooperator with no
        ``ProjectMember`` row at all (never a member) is still notified
        — that is the mission rule ("even if not a ProjectMember").

        Pre-fix (r1): the cooperators query took every IssueSubscriber
        row, so ``ex_project_member`` was added to the recipient set
        (base 0 → branch 1). The reviewer verified this directly.

        Post-fix (r2): ``.exclude(subscriber__member_project__project_id=
        project_id, subscriber__member_project__is_active=False)``
        drops the ex-member while leaving never-members alone. The
        workspace owner and project lead + admin arms of the union are
        unaffected (those are active project members by definition).
        """
        # The ex-project-member was once a project member but is now
        # deactivated; they retain a stale IssueSubscriber row.
        ProjectMember.objects.create(
            project=project_in_ws,
            member=ex_project_member,
            role=20,
            is_active=False,
        )
        issue = _make_issue(project_in_ws, ws_owned, bot)
        IssueSubscriber.objects.create(
            project=project_in_ws,
            issue=issue,
            subscriber=ex_project_member,
        )
        activities = _issue_activity_dicts(issue, project_in_ws, ws_owned, bot.pk)

        _drive_notifications(
            type="issue.activity.created",
            issue_id=issue.id,
            project_id=project_in_ws.id,
            actor_id=bot.pk,
            subscriber=True,
            issue_activities_created=activities,
        )

        receivers = _notification_receivers(issue.id)
        assert ex_project_member.pk not in receivers, (
            "Ex-project-members (ProjectMember.is_active=False) are "
            "excluded from the cooperators set to avoid leaking issue "
            "content to users who no longer have project read access. "
            "Reverting the .exclude(...) flips this to red."
        )


# --------------------------------------------------------------------------- #
# Cooperators-filter discriminant: project-scope of the inactive check        #
# --------------------------------------------------------------------------- #
#
# M53 follow-up: the r2 reviewer noted that
# ``.exclude(subscriber__member_project__project_id=project_id,
#            subscriber__member_project__is_active=False)``
# compiles to ``NOT (EXISTS(inactive-anywhere) AND EXISTS(row-in-this-project))``
# because Django splits a multi-valued ``.exclude`` into two existential
# branches that get AND-ed under De Morgan. The ``is_active=False`` arm is
# therefore **not** project-scoped — a user who is active in *this* project
# but has an inactive ``ProjectMember`` row in *some other* project is
# wrongly dropped from the cooperators queryset.
#
# The end-to-end notification task masks this defect: when the user is an
# *active* member of the current project, the legacy recipient path
# (``IssueSubscriber ∩ ProjectMember(project_id=THIS, is_active=True)``)
# catches them anyway. The r2 reviewer noted this as "harmless in effect".
# The cross-project inactive history is only observable at the ORM level
# — i.e. when you evaluate the cooperators queryset directly, the r2 form
# wrongly excludes the user; the M53 subquery form does not.
#
# The two tests below cover both observability levels:
#   1. ``test_cooperators_queryset_excludes_only_project_local_inactive``
#      drives the cooperators queryset directly with the same ORM pattern
#      the M52 block uses, against the r2 form and the M53 form, and
#      asserts the project-scoped semantics hold (red-before/green-after
#      on the queryset result alone — independent of legacy).
#   2. ``test_sp_create_with_inactive_membership_in_other_project_still_
#      notifies_cooperator`` drives the full notification task and asserts
#      the user is notified end-to-end. Because legacy catches this user
#      the test passes on either form (the "harmless in effect" verdict);
#      but if the legacy path is ever weakened, this test is the safety
#      net that pins the user-facing behaviour.


@pytest.fixture
def other_project(db, ws_owned):
    """A second project in the same workspace, used to host a deceptive
    ``ProjectMember(is_active=False)`` row for the discriminant test."""
    other = Project.objects.create(
        name="Other Project",
        identifier="OTH",
        workspace=ws_owned,
        created_by=User.objects.create(
            email="other-creator@plane.so",
            username="other_creator",
            first_name="Other",
            last_name="Creator",
        ),
        network=2,
    )
    State.objects.create(
        name="Backlog",
        project=other,
        workspace=ws_owned,
        group="backlog",
        default=True,
    )
    return other


@pytest.fixture
def cross_project_user(db, ws_owned, project_in_ws, other_project):
    """A human who is *active* in ``project_in_ws`` and *inactive* in
    ``other_project``. The two rows share the workspace; the existence of
    the OTHER inactive row is what proves the project-scoping of the
    cooperators exclude (see the discriminant test below)."""
    user = User.objects.create(
        email="cross-project@plane.so",
        username="cross_project_human",
        first_name="Cross",
        last_name="Project",
    )
    WorkspaceMember.objects.create(
        workspace=ws_owned, member=user, role=15, is_active=True
    )
    # Active in THIS project (role 15 = viewer, NOT 20, NOT project_lead).
    ProjectMember.objects.create(
        project=project_in_ws, member=user, role=15, is_active=True
    )
    # INACTIVE in ANOTHER project — the "deceptive" row that exposes the
    # r2 defect.
    ProjectMember.objects.create(
        project=other_project, member=user, role=15, is_active=False
    )
    return user


@pytest.mark.contract
@pytest.mark.django_db
class TestSpNotifyCooperatorScopeIsProjectLocal:
    def test_cooperators_queryset_excludes_only_project_local_inactive(
        self,
        ws_owned,
        project_in_ws,
        other_project,
        bot,
        cross_project_user,
    ):
        """Direct-ORM probe of the production helper
        ``_spnotify_cooperator_ids_for(project_id, issue_id)``.

        This is the red-before / green-after test the M53 mission
        requires. The end-to-end notification task includes a legacy
        recipient path
        (``IssueSubscriber ∩ ProjectMember(project_id=THIS,
        is_active=True)``) that catches an active member of *this*
        project even when the cooperators queryset wrongly drops them
        — so the user-facing test alone cannot discriminate r2 from
        M53 forms (the reviewer's "harmless in effect" verdict). This
        test calls the production helper directly so the r2 form
        failure (user wrongly dropped) flips the assertion red even
        with no legacy path involved.

        Subtest 1 — the production helper (``_spnotify_cooperator_ids_
        for``) — must include the user.

        Subtest 2 — compile SQL: must be a single ``NOT IN (SELECT
        ... WHERE project_id=THIS AND NOT is_active)`` shape with a
        ``project_id = <THIS>`` predicate inside the subquery (not
        ``EXISTS(inactive-anywhere) AND EXISTS(row-in-this)``).

        Subtest 3 — boundary: a subscriber with NO ``ProjectMember``
        row at all in the current project stays in the helper output
        (mission rule "even if not a ProjectMember"). This guards the
        narrowing against an over-restrictive reimplementation that
        re-imposes project-membership as a precondition.

        Reverting the helper back to ``.exclude(member_project__project_id
        =..., member_project__is_active=False)`` flips subtest 1 to
        red (helper returns empty), and subtest 2 to red (helper SQL
        compiles to ``NOT (EXISTS(inactive-anywhere) AND
        EXISTS(row-in-this-project))``).
        """
        issue = _make_issue(project_in_ws, ws_owned, bot)

        # Subscriber 1: cross-project case (active in THIS, inactive in
        # OTHER) — the r2 form wrongly drops them at ORM level.
        IssueSubscriber.objects.create(
            project=project_in_ws,
            issue=issue,
            subscriber=cross_project_user,
        )

        # Subscriber 2: never-member case (no ProjectMember row for THIS
        # OR OTHER). Must stay in the helper output — boundary guard.
        never_member = User.objects.create(
            email="never-member@plane.so",
            username="never_member_human",
            first_name="Never",
            last_name="Member",
        )
        WorkspaceMember.objects.create(
            workspace=ws_owned, member=never_member, role=15, is_active=True
        )
        IssueSubscriber.objects.create(
            project=project_in_ws,
            issue=issue,
            subscriber=never_member,
        )

        # ---- Subtest 1: helper output. ----
        cooperator_ids = _spnotify_cooperator_ids_for(
            project_id=project_in_ws.id, issue_id=issue.id
        )
        assert cross_project_user.pk in cooperator_ids, (
            "Production cooperators helper must keep a subscriber who is "
            "active in THIS project even if they have an inactive row in "
            "another project. Reverting M53's helper to the r2 form "
            "(``.exclude(member_project__project_id=..., "
            "member_project__is_active=False)``) flips this red."
        )
        assert never_member.pk in cooperator_ids, (
            "Subscriber with no ProjectMember row at all in THIS project "
            "must stay in the helper output — mission rule 'even if not "
            "a ProjectMember'. Reverting M53's helper to the r2 form "
            "should not flip this to red (B=FALSE → AND=FALSE → "
            "NOT=TRUE → kept), but a stricter reimplementation could."
        )

        # ---- Subtest 2: SQL shape. ----
        qs = _spnotify_cooperator_ids_qs(
            project_id=project_in_ws.id, issue_id=issue.id
        )
        helper_sql = str(qs.query)
        assert "NOT" in helper_sql.upper() and "IN (" in helper_sql.upper(), (
            "M53-form helper must compile to a ``NOT (...) IN (subquery)`` "
            "shape (a positive subquery negated). The r2 form compiles to "
            "an ``EXISTS(...) AND EXISTS(...)`` pair instead. Got:\n"
            f"{helper_sql}"
        )
        # The subquery's `project_id` predicate must scope to THIS.
        # ``project_id = <uuid-of-this-project>`` appears inside the
        # SELECT U0 ... WHERE.
        assert "project_id" in helper_sql, (
            f"Helper SQL must include a ``project_id`` predicate inside "
            f"the subquery:\n{helper_sql}"
        )
        # The r2 form's distinctive signature is "EXISTS(...) AND
        # EXISTS(...)"; reject it explicitly.
        assert "EXISTS" not in helper_sql.upper(), (
            "Helper SQL must NOT compile to the r2 form's "
            "``NOT (EXISTS(inactive-anywhere) AND EXISTS(row-in-this))`` "
            "shape — that leaks cross-project inactive history into the "
            "exclude (M53 P3 note 1). Got:\n"
            f"{helper_sql}"
        )

    def test_sp_create_with_inactive_membership_in_other_project_still_notifies_cooperator(
        self,
        ws_owned,
        project_in_ws,
        other_project,
        bot,
        cross_project_user,
        celery_eager,
    ):
        """End-to-end probe: a subscriber who is ``active`` in
        ``project_in_ws`` but holds an ``inactive`` ``ProjectMember`` row
        in a *different* project (``other_project``) must still receive a
        Notification when the SP bot writes to ``project_in_ws``.

        The user-facing test that pins the cooperative recipient
        contract under the mission rules. The full notification task
        routes each subscriber through both the legacy recipient path
        (``IssueSubscriber ∩ ProjectMember(project_id=THIS, is_active=
        True)``) and the M53 cooperators arm; the legacy arm catches
        this user regardless of which cooperators query form is in
        effect, so this test passes on both r2 and M53 forms (the
        reviewer's "harmless in effect" verdict). The companion
        ``test_cooperators_queryset_excludes_only_project_local_inactive``
        does the red-before/green-after duty at the ORM level.

        What this test **does not** discriminate (measured at M53 r2):
        removing the cooperators ``.exclude(...)`` entirely does **not**
        flip this test red — it only **adds** subscribers to the
        cooperators set, and this test asserts a recipient is *present*
        (not absent). The ORM discriminator is what pins the helper
        output; this end-to-end test pins the user-facing contract for
        future regressions of the legacy path.
        """
        issue = _make_issue(project_in_ws, ws_owned, bot)
        IssueSubscriber.objects.create(
            project=project_in_ws,
            issue=issue,
            subscriber=cross_project_user,
        )
        activities = _issue_activity_dicts(issue, project_in_ws, ws_owned, bot.pk)

        _drive_notifications(
            type="issue.activity.created",
            issue_id=issue.id,
            project_id=project_in_ws.id,
            actor_id=bot.pk,
            subscriber=True,
            issue_activities_created=activities,
        )

        receivers = _notification_receivers(issue.id)
        assert cross_project_user.pk in receivers, (
            "An IssueSubscriber who is an *active* ProjectMember of THIS "
            "project must be notified, even if they hold an inactive "
            "ProjectMember row in *another* project. The end-to-end "
            "task's legacy recipient path covers this case today "
            "(\"harmless in effect\"); if the legacy path is ever "
            "weakened, this test guards the contract."
        )


# --------------------------------------------------------------------------- #
# Cooperators-filter NULL-member guard (M53 r2 P2 fix)                          #
# --------------------------------------------------------------------------- #
#
# The r2 ``.exclude(member_project__project_id=...,
# member_project__is_active=False)`` form was rebuilt in M53 as a
# project-scoped subquery of the shape ``NOT (subscriber_id IN (SELECT
# member_id FROM project_members WHERE project_id=THIS AND NOT
# is_active))``. The reviewer pointed out a SQL NULL trap: ``ProjectMember.
# member`` is ``null=True``, the DB accepts a row with ``member=NULL``,
# and ``x NOT IN (…, NULL)`` evaluates to NULL (not TRUE) in SQL. A
# single NULL-member row in the subquery makes the predicate NULL for
# *every* subscriber — the cooperators arm is silently emptied.
#
# The fix (``member__isnull=False`` on the inner filter) excludes the
# NULL-member row from the subquery's result set, restoring the
# expected NOT-IN semantics. The r2 EXISTS form was immune to this
# because NULL never equals anything, so the M53 helper's null-guard
# is what keeps the project-scope fix from trading one wrongness for
# another.


@pytest.fixture
def normal_cooperator_subscriber(db, ws_owned, project_in_ws):
    """A plain subscriber User row — no project membership, no bot
    flag. The per-test call creates the ``IssueSubscriber`` row
    (because ``IssueSubscriber.issue_id`` is non-null). Used by the
    NULL-trap test as the regression target: when the production
    helper accidentally returns the empty set, *this* subscriber is
    the one who would have been notified but wasn't."""
    return User.objects.create(
        email="normal-cooperator@plane.so",
        username="normal_cooperator_human",
        first_name="Normal",
        last_name="Cooperator",
    )


@pytest.mark.contract
@pytest.mark.django_db
class TestSpNotifyCooperatorNullGuard:
    def test_cooperators_queryset_survives_null_member_row(
        self, ws_owned, project_in_ws, bot, normal_cooperator_subscriber
    ):
        """Plant a ``ProjectMember(project_id=THIS, is_active=False,
        member=None)`` row and assert the cooperators helper still
        returns the normal subscriber.

        Without the M53 r2 null guard, SQL semantics make every
        subscriber disappear from the cooperators arm (one NULL row
        in the subquery → ``x NOT IN (…, NULL)`` → NULL for every
        subscriber → entire arm empties). With ``member__isnull=
        False`` the NULL row is filtered out at the subquery, so the
        helper output is the regular subscriber set minus the
        (none-of-them-exists) inactive members, and the subscriber is
        included.

        This test was added at M53 r2 after the reviewer caught the
        NULL trap; it pins the load-bearing role of the
        ``member__isnull=False`` predicate in the production helper.
        Reverting just the null guard (keeping the rest of the M53
        helper intact) flips this test red.
        """
        issue = _make_issue(project_in_ws, ws_owned, bot)

        # The normal subscriber is the regression target — they must
        # survive the planted NULL-member row.
        IssueSubscriber.objects.create(
            project=project_in_ws,
            issue=issue,
            subscriber=normal_cooperator_subscriber,
        )

        # Plant the poison row: a ProjectMember in THIS project with
        # is_active=False and member=None. The column is null=True so
        # this is a valid row; ``member=None`` is the trigger.
        ProjectMember.objects.create(
            project=project_in_ws,
            member=None,
            role=15,
            is_active=False,
        )

        # Sanity: the row was created.
        null_rows = ProjectMember.objects.filter(
            project_id=project_in_ws.id,
            member__isnull=True,
        )
        assert null_rows.count() == 1, (
            f"Plant step must produce exactly one NULL-member row; "
            f"got {null_rows.count()}."
        )

        # The cooperators helper MUST still return the subscriber.
        cooperator_ids = _spnotify_cooperator_ids_for(
            project_id=project_in_ws.id, issue_id=issue.id
        )
        assert normal_cooperator_subscriber.pk in cooperator_ids, (
            "Production cooperators helper returned an empty set (or "
            "dropped the subscriber) when a NULL-member ProjectMember "
            "row exists for this project. This is the SQL NULL trap: "
            "``x NOT IN (…, NULL)`` evaluates to NULL and the entire "
            "cooperators arm empties. The M53 r2 null guard "
            "(``member__isnull=False`` on the inner filter) is the "
            "fix — reverting it flips this assertion to red."
        )


# --------------------------------------------------------------------------- #
# Cooperators-filter soft-delete propagation (M53 r2 second delta)            #
# --------------------------------------------------------------------------- #
#
# The r2 ``.exclude(member_project__project_id=...,
# member_project__is_active=False)`` form relied on a reverse-FK
# join through ``IssueSubscriber.subscriber.member_project``. That
# join is built from the related manager's default queryset, which
# soft-deletes via ``deleted_at IS NULL`` on ``ProjectMember`` — but
# only on the FK *from* the subscriber. The r2 SQL itself compiles
# to bare ``EXISTS(SELECT 1 FROM "project_members" U2 WHERE
# (NOT U2."is_active" AND U2."member_id" = …))`` — **no**
# ``deleted_at`` predicate anywhere. A subscriber whose only
# THIS-project ``ProjectMember`` row is soft-deleted (still in the
# DB, ``deleted_at`` set, ``is_active=False``) would have been
# treated as "inactive in this project" by r2's exclude and dropped
# from the cooperators arm. Their notification would never arrive.
#
# The M53 helper wraps the inner lookup in a single project-scoped
# ``ProjectMember.objects.filter(...)`` subquery. That lookup **does**
# carry the related model's default soft-delete-aware manager — and
# the manager adds ``deleted_at IS NULL`` automatically (visible in
# the compiled SQL ``U0."deleted_at" IS NULL AND NOT
# U0."is_active" AND U0."project_id" = …``). So a soft-deleted row
# is **not** seen as "inactive in this project" by the M53 helper:
# it is invisible to the exclude entirely, and the subscriber
# stays in the cooperators set.
#
# This is the M53 r2 second semantic delta (correctness improvement,
# not behavioural neutrality). It aligns with the mission rule
# "cooperators with no ProjectMember row at all are still notified":
# a soft-deleted row is, from the cooperators' perspective,
# equivalent to no row at all.


@pytest.fixture
def soft_deleted_only_member(db, ws_owned, project_in_ws):
    """A human subscriber whose ONLY ProjectMember row in this project
    was soft-deleted (the row is still in the DB with ``deleted_at``
    set, but the default manager hides it). No active
    ``ProjectMember`` row exists for this project — so legacy
    ``IssueSubscriber ∩ active_ProjectMember`` cannot catch them;
    M53's exclusion-aware cooperators arm is the only path that can
    deliver the notification.

    Per the M53 r2 reviewer's verification: r2 form gives this user
    zero notifications (the EXCLUDE sees the inactive row, drops
    them, no other arm catches them); M53 form gives them a
    notification. The direction matches the mission rule."""
    user = User.objects.create(
        email="soft-deleted-only@plane.so",
        username="soft_deleted_only_human",
        first_name="Soft",
        last_name="Deleted",
    )
    WorkspaceMember.objects.create(
        workspace=ws_owned, member=user, role=15, is_active=True
    )
    # A ProjectMember row for THIS project. Soft-delete it BEFORE the
    # first save completes the row, but keep is_active as written by
    # the test so we know the row physically exists.
    ProjectMember.objects.create(
        project=project_in_ws,
        member=user,
        role=15,
        is_active=False,
    )
    # Now soft-delete (set deleted_at on the row) — the row is still
    # in the table, but the default queryset filters it out.
    ProjectMember.all_objects.filter(
        project=project_in_ws, member=user
    ).update(deleted_at="2025-01-01 00:00:00+00:00")
    return user


@pytest.mark.contract
@pytest.mark.django_db
class TestSpNotifyCooperatorSoftDeletePropagation:
    def test_cooperators_helper_includes_subscriber_with_only_soft_deleted_pm_row(
        self, ws_owned, project_in_ws, bot, soft_deleted_only_member, celery_eager
    ):
        """A subscriber whose only THIS-project ``ProjectMember`` row
        is soft-deleted must remain in the cooperators set.

        Pre-M53 (r2 ``.exclude(...)``): the compiled SQL has **no**
        ``deleted_at`` predicate; the EXCLUDE sees the row, treats
        it as "inactive in this project", and drops the subscriber
        from the cooperators arm. Combined with no legacy catch
        (no active row), the user gets zero notifications.

        Post-M53 (project-scoped subquery): the inner ``ProjectMember
        .objects.filter(...)`` carries the default manager's
        ``deleted_at IS NULL`` automatically; the soft-deleted row
        is invisible to the exclude, the subscriber stays in the
        cooperators set, the notification is delivered.

        The M53 r2 reviewer constructed the r2 scenario and
        confirmed both directions empirically (deleted_at set, no
        notification; deleted_at cleared, notification arrives).
        This test pins the M53-side delivery for the same case —
        reverting M53 (back to the r2 ``.exclude(...)`` shape)
        flips this test red, while restoring M53 flips it green.
        """
        issue = _make_issue(project_in_ws, ws_owned, bot)
        IssueSubscriber.objects.create(
            project=project_in_ws,
            issue=issue,
            subscriber=soft_deleted_only_member,
        )

        # Sanity: the row exists (uses all_objects because the
        # default manager hides soft-deleted rows), and the default
        # manager hides it from any other lookup.
        all_pm_rows = ProjectMember.all_objects.filter(
            project=project_in_ws, member=soft_deleted_only_member
        )
        assert all_pm_rows.count() == 1, (
            "Fixture pre-condition: a ProjectMember row for this user "
            "in this project must physically exist (soft-deleted)."
        )
        # Default manager hides it — that's what triggers the
        # soft-delete propagation.
        visible_rows = ProjectMember.objects.filter(
            project=project_in_ws, member=soft_deleted_only_member
        )
        assert visible_rows.count() == 0, (
            "Fixture pre-condition: the default ProjectMember manager "
            "must hide the soft-deleted row."
        )

        # Direct ORM-level assertion first.
        cooperator_ids = _spnotify_cooperator_ids_for(
            project_id=project_in_ws.id, issue_id=issue.id
        )
        assert soft_deleted_only_member.pk in cooperator_ids, (
            "Production cooperators helper must keep a subscriber "
            "whose only THIS-project ProjectMember row is "
            "soft-deleted. The M53 subquery respects the soft-delete "
            "manager (``U0.deleted_at IS NULL`` in the compiled SQL), "
            "the r2 ``.exclude(...)`` form did not — reverting M53 "
            "flips this assertion to red."
        )

        # End-to-end mirror: drive the notification task and assert
        # the user received a Notification. The reviewer constructed
        # the same case on r2 and observed zero deliveries; this
        # test pins the M53-side delivery.
        activities = _issue_activity_dicts(
            issue, project_in_ws, ws_owned, bot.pk
        )

        _drive_notifications(
            type="issue.activity.created",
            issue_id=issue.id,
            project_id=project_in_ws.id,
            actor_id=bot.pk,
            subscriber=True,
            issue_activities_created=activities,
        )

        receivers = _notification_receivers(issue.id)
        assert soft_deleted_only_member.pk in receivers, (
            "End-to-end: a subscriber with only a soft-deleted "
            "ProjectMember row in THIS project must receive a "
            "Notification. The M53 helper surfaces them via the "
            "cooperators arm; legacy ``IssueSubscriber ∩ "
            "active_ProjectMember`` cannot catch them (no active "
            "row exists). On r2 the cooperators arm dropped them "
            "and they received zero notifications."
        )


# --------------------------------------------------------------------------- #
# Cooperators-filter tolerance: missing UserNotificationPreference              #
# --------------------------------------------------------------------------- #
#
# M52 r1 fell over hard when an IssueSubscriber row belonged to a bot user
# (``subscriber__is_bot=False`` fixed the cooperators side), but there is a
# second failure class the existing tests do not pin: a *human* subscriber
# whose ``UserNotificationPreference`` row is missing or was never created.
# The ``create_user_notification`` post_save signal
# (``plane/db/models/user.py:298-312``) auto-creates one for every non-bot
# user, but the signal may not have fired (e.g. data imported outside the
# ORM, a row deleted out of band) — and the loop's unguarded
# ``.get(user_id=subscriber)`` would raise ``DoesNotExist`` and trip the
# task's catch-all to silently abort before ``bulk_create``.
#
# The M53 fix is a tolerance in the loop itself: an unsaved fallback
# ``UserNotificationPreference`` instance is constructed when the row is
# missing, so a single subscriber without a preference still receives
# their notification and other recipients are unaffected.
#
# R3 probe: reverting *just* the tolerance (keeping ``subscriber__is_bot
# =False`` intact) → 8 passed / 0 failed, so no existing test pins the
# tolerance. The class below does, both ways:
#   - by failing when the fallback is reverted
#     (``.get(user_id=subscriber)`` re-introduced)
#   - by failing when the fallback is replaced with a bare ``None`` skip
#     (no Notification for the no-pref subscriber).


@pytest.fixture
def no_preference_cooperator(db):
    """A human subscriber with no ``UserNotificationPreference`` row.

    ``User.objects.create`` fires the ``create_user_notification`` signal
    that would otherwise auto-create one
    (``plane/db/models/user.py:298-312``); we mint the user and then delete
    the auto-created row to reach the no-row state the tolerance must
    cover. Mirrors the "raw SQL造行" path noted in the mission: any
    ``User`` row not produced by ``User.objects.create`` (or with the
    preference deleted out of band) is in this state."""
    user = User.objects.create(
        email="nopref-cooperator@plane.so",
        username="no_pref_cooperator",
        first_name="No",
        last_name="Pref",
    )
    UserNotificationPreference.objects.filter(user=user).delete()
    return user


@pytest.mark.contract
@pytest.mark.django_db
class TestSpNotifyPreferenceTolerance:
    def test_sp_create_tolerates_missing_notification_preference(
        self,
        ws_owned,
        project_in_ws,
        bot,
        no_preference_cooperator,
        cooperator_user,
        celery_eager,
    ):
        """A subscriber who has no ``UserNotificationPreference`` row must
        (i) not crash the recipient loop, (ii) still receive a
        Notification for the SP write, and (iii) not affect other
        recipients' notifications.

        Pre-M53 tolerance absent: the loop's
        ``UserNotificationPreference.objects.get(user_id=subscriber)``
        raises ``DoesNotExist`` for ``no_preference_cooperator``, the
        task's catch-all at ``notification_task.py:667-669`` swallows it
        silently, and ``Notification.objects.bulk_create`` is never
        called for *any* recipient — including ``cooperator_user`` and
        ``ws_owner``. The task's silent failure hides this: it returns
        successfully, but with zero notifications produced for the write.

        Post-M53 tolerance: ``.filter(...).first()`` followed by an
        unsaved fallback ``UserNotificationPreference`` instance
        (``notification_task.py:436-447``) means the loop completes and
        every recipient — including the no-pref subscriber — gets one
        Notification row. The fallback is intentionally **unsaved**;
        subsequent writes still hit the fallback path for the same
        subscriber.

        Discrimination: reverting just the tolerance (keeping
        ``subscriber__is_bot=False`` intact) flips this test to red
        while leaving the other seven tests green — so this is the only
        test that pins the load-bearing role of the fallback.
        """
        issue = _make_issue(project_in_ws, ws_owned, bot)
        # The user whose preference row was deleted above — the
        # tolerance target.
        IssueSubscriber.objects.create(
            project=project_in_ws,
            issue=issue,
            subscriber=no_preference_cooperator,
        )
        # A second subscriber to prove the tolerance is per-subscriber
        # (other recipients' notifications are not collateral damage).
        IssueSubscriber.objects.create(
            project=project_in_ws,
            issue=issue,
            subscriber=cooperator_user,
        )

        nopref_pref_count_before = UserNotificationPreference.objects.filter(
            user=no_preference_cooperator
        ).count()
        assert nopref_pref_count_before == 0, (
            "Fixture pre-condition: no_preference_cooperator must have "
            "zero UserNotificationPreference rows. The fixture deletes "
            "the auto-created row immediately after User.objects.create; "
            "if that delete did not run this assertion guards against a "
            "silent regression."
        )

        activities = _issue_activity_dicts(issue, project_in_ws, ws_owned, bot.pk)

        _drive_notifications(
            type="issue.activity.created",
            issue_id=issue.id,
            project_id=project_in_ws.id,
            actor_id=bot.pk,
            subscriber=True,
            issue_activities_created=activities,
        )

        receivers = _notification_receivers(issue.id)

        # (i) the task did not abort: ``cooperator_user`` is in the
        # recipient set, proving the loop ran to completion past the
        # no-pref subscriber.
        assert cooperator_user.pk in receivers, (
            "Control: cooperator_user (who has a valid preference) must "
            "still receive a Notification. If this assertion fails the "
            "task's catch-all aborted the whole write on the no-pref "
            "subscriber's DoesNotExist — a silent drop the loop's "
            "tolerance is supposed to prevent. Reverting just the "
            "tolerance re-introduces this regression."
        )

        # (ii) the no-pref subscriber still receives a Notification.
        assert no_preference_cooperator.pk in receivers, (
            "A subscriber with no UserNotificationPreference row must "
            "still receive a Notification — the loop builds an unsaved "
            "fallback instance with the same defaults as the post_save "
            "signal (``notification_task.py:436-447``). Reverting the "
            "tolerance to ``.get(user_id=subscriber)`` flips this to "
            "red with DoesNotExist in the log and *zero* notifications "
            "produced overall."
        )

        # (iii) the fallback is unsaved — the row is not persisted for
        # the subscriber. If a future change accidentally persists it,
        # the next assertion guards the documented intent.
        assert UserNotificationPreference.objects.filter(
            user=no_preference_cooperator
        ).count() == nopref_pref_count_before, (
            "The tolerance's fallback UserNotificationPreference must "
            "remain unsaved. Persisting it silently would mask data "
            "integrity drift that the post_save signal was supposed to "
            "own; the row is recreated by the signal on the next save."
        )
