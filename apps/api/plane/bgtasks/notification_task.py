# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import json
import uuid
from uuid import UUID


# Module imports
from plane.db.models import (
    IssueMention,
    IssueSubscriber,
    Project,
    User,
    IssueAssignee,
    Issue,
    State,
    EmailNotificationLog,
    Notification,
    IssueComment,
    IssueActivity,
    UserNotificationPreference,
    ProjectMember,
)
from django.db.models import Subquery

# Third Party imports
from celery import shared_task
from bs4 import BeautifulSoup


# =========== Issue Description Html Parsing and notification Functions ======================


def update_mentions_for_issue(issue, project, new_mentions, removed_mention):
    aggregated_issue_mentions = []
    for mention_id in new_mentions:
        aggregated_issue_mentions.append(
            IssueMention(
                mention_id=mention_id,
                issue=issue,
                project=project,
                workspace_id=project.workspace_id,
            )
        )

    IssueMention.objects.bulk_create(aggregated_issue_mentions, batch_size=100)
    IssueMention.objects.filter(issue=issue, mention__in=removed_mention).delete()


def get_new_mentions(requested_instance, current_instance):
    # requested_data is the newer instance of the current issue
    # current_instance is the older instance of the current issue, saved in the database

    # extract mentions from both the instance of data
    mentions_older = extract_mentions(current_instance)

    mentions_newer = extract_mentions(requested_instance)

    # Getting Set Difference from mentions_newer
    new_mentions = [mention for mention in mentions_newer if mention not in mentions_older]

    return new_mentions


# Get Removed Mention
def get_removed_mentions(requested_instance, current_instance):
    # requested_data is the newer instance of the current issue
    # current_instance is the older instance of the current issue, saved in the database

    # extract mentions from both the instance of data
    mentions_older = extract_mentions(current_instance)
    mentions_newer = extract_mentions(requested_instance)

    # Getting Set Difference from mentions_newer
    removed_mentions = [mention for mention in mentions_older if mention not in mentions_newer]

    return removed_mentions


# Adds mentions as subscribers
def extract_mentions_as_subscribers(project_id, issue_id, mentions):
    # mentions is an array of User IDs representing the FILTERED set of mentioned users

    bulk_mention_subscribers = []

    for mention_id in mentions:
        # If the particular mention has not already been subscribed to the issue, he must be sent the mentioned notification # noqa: E501
        if (
            not IssueSubscriber.objects.filter(
                issue_id=issue_id, subscriber_id=mention_id, project_id=project_id
            ).exists()
            and not IssueAssignee.objects.filter(
                project_id=project_id, issue_id=issue_id, assignee_id=mention_id
            ).exists()
            and not Issue.objects.filter(project_id=project_id, pk=issue_id, created_by_id=mention_id).exists()
            and ProjectMember.objects.filter(project_id=project_id, member_id=mention_id, is_active=True).exists()
        ):
            project = Project.objects.get(pk=project_id)

            bulk_mention_subscribers.append(
                IssueSubscriber(
                    workspace_id=project.workspace_id,
                    project_id=project_id,
                    issue_id=issue_id,
                    subscriber_id=mention_id,
                )
            )
    return bulk_mention_subscribers


# Parse Issue Description & extracts mentions
def extract_mentions(issue_instance):
    try:
        # issue_instance has to be a dictionary passed, containing the description_html and other set of activity data. # noqa: E501
        mentions = []
        # Convert string to dictionary
        data = json.loads(issue_instance)
        html = data.get("description_html")
        soup = BeautifulSoup(html, "html.parser")
        mention_tags = soup.find_all("mention-component", attrs={"entity_name": "user_mention"})

        mentions = [mention_tag["entity_identifier"] for mention_tag in mention_tags]

        return list(set(mentions))
    except Exception:
        return []


# =========== Comment Parsing and notification Functions ======================
def extract_comment_mentions(comment_value):
    try:
        mentions = []
        soup = BeautifulSoup(comment_value, "html.parser")
        mentions_tags = soup.find_all("mention-component", attrs={"entity_name": "user_mention"})
        for mention_tag in mentions_tags:
            mentions.append(mention_tag["entity_identifier"])
        return list(set(mentions))
    except Exception:
        return []


def get_new_comment_mentions(new_value, old_value):
    mentions_newer = extract_comment_mentions(new_value)
    if old_value is None:
        return mentions_newer

    mentions_older = extract_comment_mentions(old_value)
    # Getting Set Difference from mentions_newer
    new_mentions = [mention for mention in mentions_newer if mention not in mentions_older]

    return new_mentions


def create_mention_notification(project, notification_comment, issue, actor_id, mention_id, issue_id, activity):
    return Notification(
        workspace=project.workspace,
        sender="in_app:issue_activities:mentioned",
        triggered_by_id=actor_id,
        receiver_id=mention_id,
        entity_identifier=issue_id,
        entity_name="issue",
        project=project,
        message=notification_comment,
        data={
            "issue": {
                "id": str(issue_id),
                "name": str(issue.name),
                "identifier": str(issue.project.identifier),
                "sequence_id": issue.sequence_id,
                "state_name": issue.state.name,
                "state_group": issue.state.group,
            },
            "issue_activity": {
                "id": str(activity.get("id")),
                "verb": str(activity.get("verb")),
                "field": str(activity.get("field")),
                "actor": str(activity.get("actor_id")),
                "new_value": str(activity.get("new_value")),
                "old_value": str(activity.get("old_value")),
                "old_identifier": (str(activity.get("old_identifier")) if activity.get("old_identifier") else None),
                "new_identifier": (str(activity.get("new_identifier")) if activity.get("new_identifier") else None),
            },
        },
    )


def _spnotify_cooperator_ids_qs(project_id, issue_id):
    """The M52 cooperators-arm queryset, lifted out of the task so the
    contract is testable at the ORM level (the M53 discriminant
    ``TestSpNotifyCooperatorScopeIsProjectLocal`` drives this in
    isolation, not through the full notification path).

    Returns the unevaluated ``IssueSubscriber`` queryset carrying both
    exclusions. The caller wraps it in ``set(...values_list(...))`` to
    materialize. Tests can inspect ``str(qs.query)`` to verify the SQL
    shape — the production correctness contract.

    See the block comment at :309-419 for the full rationale; the two
    exclusions here are:

      1. ``subscriber__is_bot=False`` joins ``users`` (NOT
         ``workspace_members`` — see the caller-side NOTE), so it
         excludes bot subscribers from the recipient set (bots have no
         ``UserNotificationPreference``).

      2. The exclude below is *project-scoped*: a subscriber is dropped
         only if they have a ``ProjectMember(project_id=THIS,
         is_active=False, member_id IS NOT NULL, deleted_at IS NULL)``
         row. A subscriber active in this project with stale inactive
         rows in some *other* project is NOT dropped. A subscriber with
         no ``ProjectMember`` row at all is also unaffected — by
         mission rule "even if not a ProjectMember" they remain a
         valid cooperator. The ``member__isnull=False`` predicate is
         the M53 r2 fix: ``ProjectMember.member`` is ``null=True`` and
         SQL ``NOT IN (…, NULL)`` evaluates to NULL (not TRUE), so a
         single NULL-member row in the subquery would otherwise null
         out the entire cooperators arm. The r2 ``EXISTS`` form was
         immune to this because NULL never equals.

    Implemented as a positive ``NOT IN`` subquery rather than the r2
    form ``.exclude(member_project__project_id=...,
    member_project__is_active=False)``. The r2 form would compile to
    ``NOT (EXISTS(inactive-anywhere) AND EXISTS(row-in-this-project))``
    — the ``is_active=False`` arm is not project-scoped on its own, so a
    user with an inactive history elsewhere but an active row here is
    wrongly dropped (M53 discriminant). The reviewer observed this and
    marked it "harmless in effect" because the legacy recipient path
    catches the user in practice; this helper makes the project-scoping
    explicit so a future regression or migration cannot silently leak
    the cross-project exclusion into notification delivery.
    """
    return (
        IssueSubscriber.objects.filter(
            project_id=project_id,
            issue_id=issue_id,
            subscriber__is_bot=False,
        ).exclude(
            subscriber__in=ProjectMember.objects.filter(
                project_id=project_id,
                is_active=False,
                # SQL NULL guard — see the docstring above.
                member__isnull=False,
            ).values("member_id")
        )
    )


def _spnotify_cooperator_ids_for(project_id, issue_id):
    """Set form of ``_spnotify_cooperator_ids_qs`` — the production
    cooperators-arm recipient set. Materialises the queryset and
    returns the distinct subscriber UUIDs."""
    qs = _spnotify_cooperator_ids_qs(project_id, issue_id)
    return set(qs.values_list("subscriber_id", flat=True))


@shared_task
def notifications(
    type,
    issue_id,
    project_id,
    actor_id,
    subscriber,
    issue_activities_created,
    requested_data,
    current_instance,
):
    try:
        issue_activities_created = (
            json.loads(issue_activities_created) if issue_activities_created is not None else None
        )
        if type not in [
            "cycle.activity.created",
            "cycle.activity.deleted",
            "module.activity.created",
            "module.activity.deleted",
            "issue_reaction.activity.created",
            "issue_reaction.activity.deleted",
            "comment_reaction.activity.created",
            "comment_reaction.activity.deleted",
            "issue_vote.activity.created",
            "issue_vote.activity.deleted",
            "issue_draft.activity.created",
            "issue_draft.activity.updated",
            "issue_draft.activity.deleted",
        ]:
            # Create Notifications
            bulk_notifications = []
            bulk_email_logs = []

            """
            Mention Tasks
            1. Perform Diffing and Extract the mentions, that mention notification needs to be sent
            2. From the latest set of mentions, extract the users which are not a subscribers & make them subscribers
            """

            # get the list of active project members
            project_members = ProjectMember.objects.filter(project_id=project_id, is_active=True).values_list(
                "member_id", flat=True
            )

            # Get new mentions from the newer instance
            new_mentions = get_new_mentions(requested_instance=requested_data, current_instance=current_instance)
            new_mentions = list(set(new_mentions) & {str(member) for member in project_members})
            removed_mention = get_removed_mentions(requested_instance=requested_data, current_instance=current_instance)

            comment_mentions = []
            all_comment_mentions = []

            # Get New Subscribers from the mentions of the newer instance
            requested_mentions = extract_mentions(issue_instance=requested_data)
            mention_subscribers = extract_mentions_as_subscribers(
                project_id=project_id, issue_id=issue_id, mentions=requested_mentions
            )

            for issue_activity in issue_activities_created:
                issue_comment = issue_activity.get("issue_comment")
                issue_comment_new_value = issue_activity.get("new_value")
                issue_comment_old_value = issue_activity.get("old_value")
                if issue_comment is not None:
                    # TODO: Maybe save the comment mentions, so that in future, we can filter out the issues based on comment mentions as well.

                    all_comment_mentions = all_comment_mentions + extract_comment_mentions(issue_comment_new_value)

                    new_comment_mentions = get_new_comment_mentions(
                        old_value=issue_comment_old_value,
                        new_value=issue_comment_new_value,
                    )
                    comment_mentions = comment_mentions + new_comment_mentions
                    comment_mentions = [
                        mention for mention in comment_mentions if UUID(mention) in set(project_members)
                    ]

            comment_mention_subscribers = extract_mentions_as_subscribers(
                project_id=project_id, issue_id=issue_id, mentions=all_comment_mentions
            )
            """
            We will not send subscription activity notification to the below mentioned user sets
            - Those who have been newly mentioned in the issue description, we will send mention notification to them.
            - When the activity is a comment_created and there exist a mention in the comment, 
              then we have to send the "mention_in_comment" notification
            - When the activity is a comment_updated and there exist a mention change, 
              then also we have to send the "mention_in_comment" notification
            """

            # ---------------------------------------------------------------------------------------------------------
            issue_subscribers = list(
                IssueSubscriber.objects.filter(
                    project_id=project_id,
                    issue_id=issue_id,
                    subscriber__in=Subquery(project_members),
                )
                .exclude(subscriber_id__in=list(new_mentions + comment_mentions + [actor_id]))
                .values_list("subscriber", flat=True)
            )

            issue = Issue.objects.filter(pk=issue_id).first()

            if subscriber:
                # add the user to issue subscriber
                try:
                    _ = IssueSubscriber.objects.get_or_create(
                        project_id=project_id, issue_id=issue_id, subscriber_id=actor_id
                    )
                except Exception:
                    pass

            project = Project.objects.get(pk=project_id)

            issue_assignees = IssueAssignee.objects.filter(
                issue_id=issue_id,
                project_id=project_id,
                assignee__in=Subquery(project_members),
            ).values_list("assignee", flat=True)

            # ----- M52 SPNOTIFY (begin) ------------------------------------------------- #
            # Extend the subscription-activity recipient set with three classes the user
            # asked for explicitly: the workspace owner, the project owner, and every
            # IssueSubscriber ("task cooperator"). The widening fires only when the
            # actor is a bot — i.e. when an SP wrote the row. Human-actor writes keep
            # the legacy recipient path (project_members ∩ IssueSubscriber, minus
            # mentions / the actor), which is what test 4 in
            # tests/contract/app/test_sp_notification_recipients.py pins byte-for-byte.
            #
            # Project owner is the union of ``Project.project_lead`` and
            # ``ProjectMember(role=20, is_active=True)``: the schema lets either field
            # name "the project owner", and silently taking only one would drop half
            # the cases. Workspace owner is ``Workspace.owner`` (single FK).
            #
            # Cooperators (IssueSubscriber rows) are *not* the entire table — see
            # M52 r1. Two exclusions are required:
            #
            #  1. ``subscriber__is_bot=False`` — bots have no
            #     ``UserNotificationPreference`` row (``db/models/user.py:298-301``
            #     skips ``is_bot=True`` at the post_save signal), so the loop's
            #     preference lookup below would raise ``DoesNotExist`` and the
            #     task's catch-all at :667-669 would silently abort before
            #     ``Notification.objects.bulk_create`` — every recipient on the
            #     write would get 0 notifications instead of their 1. The M39
            #     invariant already says bots are never ``WorkspaceMember`` /
            #     ``ProjectMember``, but the SP write path itself subscribes
            #     *every* SP's bot as ``IssueSubscriber`` (the legacy :295-297
            #     ``get_or_create(subscriber_id=actor_id)`` runs on every write),
            #     so an IssueSubscriber row whose subscriber is *another* SP's
            #     bot is in scope here. Hard exclude.
            #
            #     What this *does not* do: it does not exclude users who lack a
            #     ``WorkspaceMember`` row. ``is_bot`` is a column on ``users``,
            #     so the filter joins ``users`` (``INNER JOIN "users" ON
            #     ("issue_subscribers"."subscriber_id" = "users"."id")``), not
            #     ``workspace_members``. A subscriber who is a human with no
            #     ``WorkspaceMember`` row stays in scope — that is intended, per
            #     the mission rule "even if not a ProjectMember / WorkspaceMember".
            #
            #  2. ``exclude(subscriber__in=ProjectMember.objects.filter(
            #     project_id=project_id, is_active=False).values("member_id"))``
            #     — content-leak guard for the *cooperator* arm. Removing a
            #     project member only flips ``is_active=False``
            #     (``app/views/project/member.py:319-320``) and never deletes the
            #     ``IssueSubscriber`` row, so an ex-member retains a stale
            #     subscription. The notifications list API filters only by
            #     ``workspace__slug`` + ``receiver_id``
            #     (``app/views/notification/base.py:39-46``), so a still-in-workspace
            #     ex-member can fetch issue content from a project they left.
            #     We accept the narrower exception: a cooperator with no
            #     ``ProjectMember`` row at all is still notified (mission rule:
            #     "even if not a ProjectMember"); a cooperator with an *inactive*
            #     ``ProjectMember`` for this project is dropped. The narrowing
            #     preserves the original intersect's intent (active project
            #     membership in *this* project) without re-imposing it on
            #     never-members. The membership check is project-scoped — a user
            #     with an inactive ``ProjectMember`` row in some *other* project
            #     but an active row here is a valid recipient and is NOT
            #     dropped.
            #
            # The notification *content* is unchanged: ``triggered_by_id`` stays the
            # bot (M39/M41/M47 wired that on the write side), and the activity data
            # flows through unchanged. We only widen the recipient set.
            _spnotify_actor = User.objects.filter(pk=actor_id).first()
            _spnotify_extend = bool(_spnotify_actor and _spnotify_actor.is_bot)

            try:
                _spnotify_actor_uuid = uuid.UUID(actor_id)
            except (ValueError, TypeError):
                _spnotify_actor_uuid = None

            _spnotify_mention_uuids = {
                uuid.UUID(m) for m in (new_mentions + comment_mentions) if m
            }
            _spnotify_exclude = _spnotify_mention_uuids | (
                {_spnotify_actor_uuid} if _spnotify_actor_uuid else set()
            )

            if _spnotify_extend:
                # ``Workspace.owner`` is a non-null FK; resolve via the workspace
                # already loaded on ``project``.
                _spnotify_ws_owner_id = project.workspace.owner_id

                _spnotify_project_owner_ids = set()
                if project.project_lead_id:
                    _spnotify_project_owner_ids.add(project.project_lead_id)
                _spnotify_project_owner_ids.update(
                    ProjectMember.objects.filter(
                        project_id=project_id, role=20, is_active=True
                    ).values_list("member_id", flat=True)
                )

                _spnotify_cooperator_ids = _spnotify_cooperator_ids_for(
                    project_id, issue_id
                )

                _spnotify_extension_uuids = (
                    ({_spnotify_ws_owner_id} if _spnotify_ws_owner_id else set())
                    | _spnotify_project_owner_ids
                    | _spnotify_cooperator_ids
                ) - _spnotify_exclude

                issue_subscribers = list(
                    (set(issue_subscribers) | _spnotify_extension_uuids)
                    - ({_spnotify_actor_uuid} if _spnotify_actor_uuid else set())
                )
            else:
                issue_subscribers = list(
                    set(issue_subscribers)
                    - ({_spnotify_actor_uuid} if _spnotify_actor_uuid else set())
                )
            # ----- M52 SPNOTIFY (end) --------------------------------------------------- #

            for subscriber in issue_subscribers:
                if issue.created_by_id and issue.created_by_id == subscriber:
                    sender = "in_app:issue_activities:created"
                elif subscriber in issue_assignees and issue.created_by_id not in issue_assignees:
                    sender = "in_app:issue_activities:assigned"
                else:
                    sender = "in_app:issue_activities:subscribed"

                # Tolerate a missing preference row: a bot *should* have been
                # excluded above by the cooperators filter, but defensively we
                # still fall back to the post_save signal's defaults
                # (``db/models/user.py:298-311``: ``property_change=True,
                # state_change=True, comment=True, mention=True,
                # issue_completed=True``). The fallback instance is unsaved —
                # the loop below only reads attribute values, never persists.
                preference = UserNotificationPreference.objects.filter(
                    user_id=subscriber
                ).first()
                if preference is None:
                    preference = UserNotificationPreference(
                        user_id=subscriber,
                        property_change=True,
                        state_change=True,
                        comment=True,
                        mention=True,
                        issue_completed=True,
                    )

                for issue_activity in issue_activities_created:
                    # If activity done in blocking then blocked by email should not go
                    if issue_activity.get("issue_detail").get("id") != issue_id:
                        continue

                    # Do not send notification for description update
                    if issue_activity.get("field") == "description":
                        continue

                    # Check if the value should be sent or not
                    send_email = False
                    if issue_activity.get("field") == "state" and preference.state_change:
                        send_email = True
                    elif (
                        issue_activity.get("field") == "state"
                        and preference.issue_completed
                        and State.objects.filter(
                            project_id=project_id,
                            pk=issue_activity.get("new_identifier"),
                            group="completed",
                        ).exists()
                    ):
                        send_email = True
                    elif issue_activity.get("field") == "comment" and preference.comment:
                        send_email = True
                    elif preference.property_change:
                        send_email = True
                    else:
                        send_email = False

                    # If activity is of issue comment fetch the comment
                    issue_comment = (
                        IssueComment.objects.filter(
                            id=issue_activity.get("issue_comment"),
                            issue_id=issue_id,
                            project_id=project_id,
                            workspace_id=project.workspace_id,
                        ).first()
                        if issue_activity.get("issue_comment")
                        else None
                    )

                    # Create in app notification
                    bulk_notifications.append(
                        Notification(
                            workspace=project.workspace,
                            sender=sender,
                            triggered_by_id=actor_id,
                            receiver_id=subscriber,
                            entity_identifier=issue_id,
                            entity_name="issue",
                            project=project,
                            title=issue_activity.get("comment"),
                            data={
                                "issue": {
                                    "id": str(issue_id),
                                    "name": str(issue.name),
                                    "identifier": str(issue.project.identifier),
                                    "sequence_id": issue.sequence_id,
                                    "state_name": issue.state.name,
                                    "state_group": issue.state.group,
                                },
                                "issue_activity": {
                                    "id": str(issue_activity.get("id")),
                                    "verb": str(issue_activity.get("verb")),
                                    "field": str(issue_activity.get("field")),
                                    "actor": str(issue_activity.get("actor_id")),
                                    "new_value": str(issue_activity.get("new_value")),
                                    "old_value": str(issue_activity.get("old_value")),
                                    "issue_comment": str(
                                        issue_comment.comment_stripped if issue_comment is not None else ""
                                    ),
                                    "old_identifier": (
                                        str(issue_activity.get("old_identifier"))
                                        if issue_activity.get("old_identifier")
                                        else None
                                    ),
                                    "new_identifier": (
                                        str(issue_activity.get("new_identifier"))
                                        if issue_activity.get("new_identifier")
                                        else None
                                    ),
                                },
                            },
                        )
                    )
                    # Create email notification
                    if send_email:
                        bulk_email_logs.append(
                            EmailNotificationLog(
                                triggered_by_id=actor_id,
                                receiver_id=subscriber,
                                entity_identifier=issue_id,
                                entity_name="issue",
                                data={
                                    "issue": {
                                        "id": str(issue_id),
                                        "name": str(issue.name),
                                        "identifier": str(issue.project.identifier),
                                        "project_id": str(issue.project.id),
                                        "workspace_slug": str(issue.project.workspace.slug),
                                        "sequence_id": issue.sequence_id,
                                        "state_name": issue.state.name,
                                        "state_group": issue.state.group,
                                    },
                                    "issue_activity": {
                                        "id": str(issue_activity.get("id")),
                                        "verb": str(issue_activity.get("verb")),
                                        "field": str(issue_activity.get("field")),
                                        "actor": str(issue_activity.get("actor_id")),
                                        "new_value": str(issue_activity.get("new_value")),
                                        "old_value": str(issue_activity.get("old_value")),
                                        "issue_comment": str(
                                            issue_comment.comment_stripped if issue_comment is not None else ""
                                        ),
                                        "old_identifier": (
                                            str(issue_activity.get("old_identifier"))
                                            if issue_activity.get("old_identifier")
                                            else None
                                        ),
                                        "new_identifier": (
                                            str(issue_activity.get("new_identifier"))
                                            if issue_activity.get("new_identifier")
                                            else None
                                        ),
                                        "activity_time": issue_activity.get("created_at"),
                                    },
                                },
                            )
                        )

            # -------------------------------------------------------------------------------------------------------- #

            # Add Mentioned as Issue Subscribers
            IssueSubscriber.objects.bulk_create(
                mention_subscribers + comment_mention_subscribers,
                batch_size=100,
                ignore_conflicts=True,
            )

            last_activity = IssueActivity.objects.filter(issue_id=issue_id).order_by("-created_at").first()

            actor = User.objects.get(pk=actor_id)

            for mention_id in comment_mentions:
                if mention_id != actor_id:
                    preference = UserNotificationPreference.objects.get(user_id=mention_id)
                    for issue_activity in issue_activities_created:
                        notification = create_mention_notification(
                            project=project,
                            issue=issue,
                            notification_comment=f"{actor.display_name} has mentioned you in a comment in issue {issue.name}",  # noqa: E501
                            actor_id=actor_id,
                            mention_id=mention_id,
                            issue_id=issue_id,
                            activity=issue_activity,
                        )

                        # check for email notifications
                        if preference.mention:
                            bulk_email_logs.append(
                                EmailNotificationLog(
                                    triggered_by_id=actor_id,
                                    receiver_id=mention_id,
                                    entity_identifier=issue_id,
                                    entity_name="issue",
                                    data={
                                        "issue": {
                                            "id": str(issue_id),
                                            "name": str(issue.name),
                                            "identifier": str(issue.project.identifier),
                                            "sequence_id": issue.sequence_id,
                                            "state_name": issue.state.name,
                                            "state_group": issue.state.group,
                                            "project_id": str(issue.project.id),
                                            "workspace_slug": str(issue.project.workspace.slug),
                                        },
                                        "issue_activity": {
                                            "id": str(issue_activity.get("id")),
                                            "verb": str(issue_activity.get("verb")),
                                            "field": str("mention"),
                                            "actor": str(issue_activity.get("actor_id")),
                                            "new_value": str(issue_activity.get("new_value")),
                                            "old_value": str(issue_activity.get("old_value")),
                                            "old_identifier": (
                                                str(issue_activity.get("old_identifier"))
                                                if issue_activity.get("old_identifier")
                                                else None
                                            ),
                                            "new_identifier": (
                                                str(issue_activity.get("new_identifier"))
                                                if issue_activity.get("new_identifier")
                                                else None
                                            ),
                                            "activity_time": issue_activity.get("created_at"),
                                        },
                                    },
                                )
                            )
                        bulk_notifications.append(notification)

            for mention_id in new_mentions:
                if mention_id != actor_id:
                    preference = UserNotificationPreference.objects.get(user_id=mention_id)
                    if (
                        last_activity is not None
                        and last_activity.field == "description"
                        and actor_id == str(last_activity.actor_id)
                    ):
                        bulk_notifications.append(
                            Notification(
                                workspace=project.workspace,
                                sender="in_app:issue_activities:mentioned",
                                triggered_by_id=actor_id,
                                receiver_id=mention_id,
                                entity_identifier=issue_id,
                                entity_name="issue",
                                project=project,
                                message=f"You have been mentioned in the issue {issue.name}",
                                data={
                                    "issue": {
                                        "id": str(issue_id),
                                        "name": str(issue.name),
                                        "identifier": str(issue.project.identifier),
                                        "sequence_id": issue.sequence_id,
                                        "state_name": issue.state.name,
                                        "state_group": issue.state.group,
                                        "project_id": str(issue.project.id),
                                        "workspace_slug": str(issue.project.workspace.slug),
                                    },
                                    "issue_activity": {
                                        "id": str(last_activity.id),
                                        "verb": str(last_activity.verb),
                                        "field": str(last_activity.field),
                                        "actor": str(last_activity.actor_id),
                                        "new_value": str(last_activity.new_value),
                                        "old_value": str(last_activity.old_value),
                                        "old_identifier": (
                                            str(issue_activity.get("old_identifier"))
                                            if issue_activity.get("old_identifier")
                                            else None
                                        ),
                                        "new_identifier": (
                                            str(issue_activity.get("new_identifier"))
                                            if issue_activity.get("new_identifier")
                                            else None
                                        ),
                                    },
                                },
                            )
                        )
                        if preference.mention:
                            bulk_email_logs.append(
                                EmailNotificationLog(
                                    triggered_by_id=actor_id,
                                    receiver_id=subscriber,
                                    entity_identifier=issue_id,
                                    entity_name="issue",
                                    data={
                                        "issue": {
                                            "id": str(issue_id),
                                            "name": str(issue.name),
                                            "identifier": str(issue.project.identifier),
                                            "sequence_id": issue.sequence_id,
                                            "state_name": issue.state.name,
                                            "state_group": issue.state.group,
                                        },
                                        "issue_activity": {
                                            "id": str(last_activity.id),
                                            "verb": str(last_activity.verb),
                                            "field": "mention",
                                            "actor": str(last_activity.actor_id),
                                            "new_value": str(last_activity.new_value),
                                            "old_value": str(last_activity.old_value),
                                            "old_identifier": (
                                                str(issue_activity.get("old_identifier"))
                                                if issue_activity.get("old_identifier")
                                                else None
                                            ),
                                            "new_identifier": (
                                                str(issue_activity.get("new_identifier"))
                                                if issue_activity.get("new_identifier")
                                                else None
                                            ),
                                            "activity_time": str(last_activity.created_at),
                                        },
                                    },
                                )
                            )
                    else:
                        for issue_activity in issue_activities_created:
                            notification = create_mention_notification(
                                project=project,
                                issue=issue,
                                notification_comment=f"You have been mentioned in the issue {issue.name}",
                                actor_id=actor_id,
                                mention_id=mention_id,
                                issue_id=issue_id,
                                activity=issue_activity,
                            )
                            if preference.mention:
                                bulk_email_logs.append(
                                    EmailNotificationLog(
                                        triggered_by_id=actor_id,
                                        receiver_id=subscriber,
                                        entity_identifier=issue_id,
                                        entity_name="issue",
                                        data={
                                            "issue": {
                                                "id": str(issue_id),
                                                "name": str(issue.name),
                                                "identifier": str(issue.project.identifier),
                                                "sequence_id": issue.sequence_id,
                                                "state_name": issue.state.name,
                                                "state_group": issue.state.group,
                                            },
                                            "issue_activity": {
                                                "id": str(issue_activity.get("id")),
                                                "verb": str(issue_activity.get("verb")),
                                                "field": str("mention"),
                                                "actor": str(issue_activity.get("actor_id")),
                                                "new_value": str(issue_activity.get("new_value")),
                                                "old_value": str(issue_activity.get("old_value")),
                                                "old_identifier": (
                                                    str(issue_activity.get("old_identifier"))
                                                    if issue_activity.get("old_identifier")
                                                    else None
                                                ),
                                                "new_identifier": (
                                                    str(issue_activity.get("new_identifier"))
                                                    if issue_activity.get("new_identifier")
                                                    else None
                                                ),
                                                "activity_time": issue_activity.get("created_at"),
                                            },
                                        },
                                    )
                                )
                            bulk_notifications.append(notification)

            # save new mentions for the particular issue and remove the mentions that has been deleted from the description # noqa: E501
            update_mentions_for_issue(
                issue=issue,
                project=project,
                new_mentions=new_mentions,
                removed_mention=removed_mention,
            )
            # Bulk create notifications
            Notification.objects.bulk_create(bulk_notifications, batch_size=100)
            EmailNotificationLog.objects.bulk_create(bulk_email_logs, batch_size=100, ignore_conflicts=True)
        return
    except Exception as e:
        print(e)
        return
