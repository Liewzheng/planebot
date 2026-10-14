# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
from itertools import chain

# Django imports
from django.db.models import Prefetch, Q
from django.utils.decorators import method_decorator
from django.views.decorators.gzip import gzip_page

# Third Party imports
from rest_framework.response import Response
from rest_framework import status

# Module imports
from .. import BaseAPIView
from plane.app.serializers import IssueActivitySerializer, IssueCommentSerializer
from plane.app.permissions import ProjectEntityPermission, allow_permission, ROLE
from plane.db.models import IssueActivity, IssueComment, CommentReaction, IntakeIssue, WorkspaceMember


class IssueActivityEndpoint(BaseAPIView):
    permission_classes = [ProjectEntityPermission]
    use_read_replica = True

    @method_decorator(gzip_page)
    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def get(self, request, slug, project_id, issue_id):
        filters = {}
        if request.GET.get("created_at__gt", None) is not None:
            filters = {"created_at__gt": request.GET.get("created_at__gt")}

        # M51: workspace admins own the workspace and must read the
        # activity log in any project, including ``ProjectNetwork.SECRET``
        # ones where they hold no ``ProjectMember`` row. The widening is
        # intentional and load-bearing — the inline ``project_member_q``
        # alone would zero the result set for an admin caller. Non-admin
        # callers go through the original ProjectMember-only predicate
        # unchanged.
        is_workspace_admin = WorkspaceMember.objects.filter(
            member=self.request.user,
            workspace__slug=slug,
            role=ROLE.ADMIN.value,
            is_active=True,
        ).exists()
        if is_workspace_admin:
            project_reader_q = Q()  # admin sees all in-project activities
        else:
            project_reader_q = Q(
                project__project_projectmember__member=self.request.user,
                project__project_projectmember__is_active=True,
            )

        issue_activities = (
            IssueActivity.objects.filter(issue_id=issue_id, project_id=project_id)
            .filter(
                ~Q(field__in=["comment", "vote", "reaction", "draft"]),
                project_reader_q,
                project__archived_at__isnull=True,
                workspace__slug=slug,
            )
            .filter(**filters)
            .select_related("actor", "workspace", "issue", "project")
        ).order_by("created_at")
        issue_comments = (
            IssueComment.objects.filter(issue_id=issue_id, project_id=project_id)
            .filter(
                project_reader_q,
                project__archived_at__isnull=True,
                workspace__slug=slug,
            )
            .filter(**filters)
            .order_by("created_at")
            .select_related("actor", "issue", "project", "workspace")
            .prefetch_related(
                Prefetch(
                    "comment_reactions",
                    queryset=CommentReaction.objects.select_related("actor"),
                )
            )
        )

        if request.GET.get("activity_type", None) == "issue-property":
            issue_activities = issue_activities.prefetch_related(
                Prefetch(
                    "issue__issue_intake",
                    queryset=IntakeIssue.objects.only("source_email", "source", "extra"),
                    to_attr="source_data",
                )
            )
            issue_activities = IssueActivitySerializer(issue_activities, many=True).data
            return Response(issue_activities, status=status.HTTP_200_OK)

        if request.GET.get("activity_type", None) == "issue-comment":
            issue_comments = IssueCommentSerializer(issue_comments, many=True).data
            return Response(issue_comments, status=status.HTTP_200_OK)

        result_list = sorted(
            chain(issue_activities, issue_comments),
            key=lambda instance: instance["created_at"],
        )

        return Response(result_list, status=status.HTTP_200_OK)
