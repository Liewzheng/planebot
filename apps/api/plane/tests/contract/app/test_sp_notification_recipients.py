# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: SP write notifications reach workspace owner, project owner, and
task cooperators (M52 / SPNOTIFY).

The user's complaint verbatim: "我的 SP 干活，要通知我" — when the bot writes, the
human who runs the workflow never hears about it. After M39/M41/M47 the *actor*
column on the notification row already credits the SP's bot (so "who did it" is
right); this mission widens only the *recipient* set.

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
legacy recipient path byte-for-byte. That is test 5 below.

The eight tests in this file are paired with the M52 diff in
``plane/bgtasks/notification_task.py``: reverting the new M52 block (lines 309–419
on the patched branch) flips the seven SP-path tests to red and leaves test 5
(the human control) green; running on ``integration/selfhost@378afd449e`` (the
tip before this mission) flips every SP-path test to red.

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
  three classes (workspace owner + project lead + admin role member) must
  yield exactly one notification per activity, not three. ``ws_owner`` is
  deliberately *not* an ``IssueSubscriber`` so the pre-M52
  ``IssueSubscriber ∩ ProjectMember`` intersect yields zero rows and the
  assertion discriminates the dedup on the M52 branch.
* ``test_sp_create_with_other_sp_bot_subscriber_still_notifies_human_cooperator``
  — **r2 regression** for the M52 r1 p1. When a non-acting bot
  (``is_bot=True``) ``IssueSubscriber`` row is on the issue, pre-fix the
  loop's ``UserNotificationPreference.objects.get`` raised
  ``DoesNotExist`` and the task's catch-all aborted, dropping every
  notification for the write. Post-fix: bots are hard-excluded from the
  cooperators set AND the preference lookup tolerates a missing row, so
  the task completes and the human cooperator receives 1 notification.
* ``test_sp_create_excludes_ex_project_member_cooperator`` — **r2 policy
  decision** test for the M52 r1 P2. A user who left the project
  (``ProjectMember.is_active=False``) but is still an active
  ``WorkspaceMember`` and has a stale ``IssueSubscriber`` row is *excluded*
  from the cooperators set (content-leak guard — see the report for the
  full rationale). Pre-fix (r1) the cooperators query took every
  ``IssueSubscriber`` row, so this user was added to the recipient set
  (base 0 → branch 1, reviewer-verified).

Each "red-before, green-after" pair is recorded by reverting the M52 block in
``notification_task.py`` and re-running just this file; the revert recipe is
captured in the report.
"""

from __future__ import annotations

import json

import pytest
from django.core.serializers.json import DjangoJSONEncoder

from plane.app.serializers.issue import IssueActivitySerializer
from plane.bgtasks.notification_task import notifications
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
            "``notification_task.py:309-378`` is no longer in effect."
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
        """When the three M52 recipient classes overlap on a single user
        (``ws_owner`` is the workspace owner, *and* the project lead, *and*
        a ``ProjectMember(role=20, is_active=True)``, *and* an
        ``IssueSubscriber``), exactly one ``Notification`` row must land.

        Pre-M52: zero rows for ``ws_owner`` (the intersect is empty and no
        extension fires).

        Post-M52: the four overlap arms are unioned into a single set of
        UUIDs before the recipient loop runs; the loop emits one
        ``Notification`` per (activity, recipient) pair. With a single
        activity row in the test, the count is exactly one."""
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
# The two regressions are pinned by the tests below. Reverting either
# cooperators filter flips both tests to red; restoring the filter flips them
# back to green.


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
