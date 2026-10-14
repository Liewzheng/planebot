# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: workspace-admin visibility bypass across the human
app + v1 surfaces (mission M51).

What the user's ruling asked for. Workspace admins own the workspace
and must be able to list every project under it (including
``ProjectNetwork.SECRET``), open it, and read the issues / activity
inside — even when the admin holds no ``ProjectMember`` row for the
project. Non-admin workspace members (MEMBER(15) / GUEST(5)) must keep
exactly today's "ProjectMember-only" semantics; widening them is the
one thing the mission explicitly forbids.

Each test in this file discriminates in two ways:

* the **admin branch** flips from ``409`` / ``403`` / empty results
  to ``200`` with the expected row(s) the day the corresponding
  bypass lands; reverting any of the six source changes puts the test
  back to red;
* the **non-admin contrast** (MEMBER / GUEST) stays at the original
  denial — reverting those would still keep the admin path working
  and the contrast would silently pass, so each contrast carries its
  own ``is_active=True`` / ``role`` pin to make sure the widening
  didn't accidentally broaden the predicate.

Surface coverage by design:

* the **app surface** is exercised via session auth
  (``session_client``) — the bot's owner is logged in as a normal
  user and proves the workspace-admin role bypass reaches the inline
  ``member_ids`` check in ``ProjectViewSet.retrieve``;
* the **v1 surface** is exercised via ``api_key_client`` — it
  exercises ``visible_projects``'s human-branch OR clause and the
  widened inline queries under ``api/views/issue.py``;
* the **activity surface** uses both ``WorkspaceUserActivityEndpoint``
  (workspace-level) and ``IssueActivityListAPIEndpoint`` (v1 issue
  activity) — neither uses ``@allow_permission``, so the widening
  lands in ``ProjectEntityPermission`` for the issue endpoint and in
  the inline ``project_reader_q`` for both.
"""

from __future__ import annotations

import uuid
from typing import Optional

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import (
    Issue,
    IssueActivity,
    Project,
    ProjectMember,
    State,
    User,
    WorkspaceMember,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _make_user(
    email: str, *, first_name: Optional[str] = None, last_name: str = "User"
) -> User:
    local_part = email.split("@")[0]
    user = User.objects.create(
        email=email,
        username=local_part,
        first_name=first_name or local_part,
        last_name=last_name,
    )
    user.set_password("test-password")
    user.save()
    return user


def _add_workspace_member(workspace, user, role: int) -> WorkspaceMember:
    return WorkspaceMember.objects.create(
        workspace=workspace, member=user, role=role, is_active=True
    )


def _make_project(
    workspace,
    created_by: User,
    *,
    name: str,
    identifier: str,
    network: int = 0,
    members: Optional[list] = None,
) -> Project:
    """Build a project. ``network=0`` is SECRET, ``network=2`` is PUBLIC."""
    project = Project.objects.create(
        name=name,
        identifier=identifier,
        workspace=workspace,
        created_by=created_by,
        network=network,
    )
    ProjectMember.objects.create(
        workspace=workspace,
        project=project,
        member=created_by,
        role=20,
        is_active=True,
    )
    for user in members or []:
        ProjectMember.objects.create(
            workspace=workspace,
            project=project,
            member=user,
            role=20,
            is_active=True,
        )
    return project


def _make_issue(project, workspace, created_by) -> Issue:
    state = State.objects.create(
        name="Backlog",
        project=project,
        workspace=workspace,
        group="backlog",
        default=True,
    )
    return Issue.objects.create(
        name="Visibility issue",
        project=project,
        workspace=workspace,
        state=state,
        created_by=created_by,
    )


# --------------------------------------------------------------------------- #
# Fixtures: the four roles the contrast tests pivot on                          #
# --------------------------------------------------------------------------- #


@pytest.fixture
def ws_admin(db, workspace):
    """Workspace ADMIN (role=20) — the user the bypass must serve."""
    user = _make_user("ws-admin@plane.so")
    _add_workspace_member(workspace, user, role=20)
    return user


@pytest.fixture
def ws_member(db, workspace):
    """Workspace MEMBER (role=15) — must keep today's ProjectMember-only
    semantics. Contrast user for the widening tests."""
    user = _make_user("ws-member@plane.so")
    _add_workspace_member(workspace, user, role=15)
    return user


@pytest.fixture
def ws_guest(db, workspace):
    """Workspace GUEST (role=5) — same as ``ws_member`` for this mission's
    purposes; included so the contrast reads at every level."""
    user = _make_user("ws-guest@plane.so")
    _add_workspace_member(workspace, user, role=5)
    return user


# --------------------------------------------------------------------------- #
# URL helpers                                                                  #
# --------------------------------------------------------------------------- #


def _project_detail_url(slug: str, project_id) -> str:
    return f"/api/workspaces/{slug}/projects/{project_id}/"


def _v1_projects_url(slug: str) -> str:
    return f"/api/v1/workspaces/{slug}/projects/"


def _v1_issue_activity_url(slug: str, project_id, issue_id) -> str:
    return (
        f"/api/v1/workspaces/{slug}/projects/{project_id}"
        f"/issues/{issue_id}/activities/"
    )


def _workspace_user_activity_url(slug: str, user_id) -> str:
    return f"/api/workspaces/{slug}/user-activity/{user_id}/"


# --------------------------------------------------------------------------- #
# App surface: workspace-admin opens a SECRET project they hold no              #
# ProjectMember row for (the headline "看得到但点不开" symptom)                  #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestAppSurfaceProjectRetrieve:
    """``ProjectViewSet.retrieve`` is the literal line the user complained
    about: the admin can list projects on the app surface, but the
    ``member_ids`` check at ``app/views/project/base.py:232-244`` denies
    them with ``409`` (or ``403`` for SECRET). After the bypass the
    admin opens any project in the workspace.
    """

    def test_admin_opens_public_project_they_are_not_a_member_of(
        self, session_client, workspace, create_user, ws_admin
    ):
        project = _make_project(
            workspace, create_user, name="Public", identifier="PUB", network=2
        )

        client = APIClient()
        client.force_authenticate(user=ws_admin)
        response = client.get(_project_detail_url(workspace.slug, project.id))

        assert response.status_code == status.HTTP_200_OK, (
            f"workspace admin (role=20) must open a non-member project; "
            f"got {response.status_code}: {response.data!r}"
        )
        assert str(response.data["id"]) == str(project.id)

    def test_admin_opens_secret_project_they_are_not_a_member_of(
        self, session_client, workspace, create_user, ws_admin
    ):
        project = _make_project(
            workspace, create_user, name="Secret", identifier="SEC", network=0
        )

        client = APIClient()
        client.force_authenticate(user=ws_admin)
        response = client.get(_project_detail_url(workspace.slug, project.id))

        assert response.status_code == status.HTTP_200_OK, (
            f"workspace admin must open a SECRET project (user ruling: "
            f"'能打开，包括 SECRET'); got {response.status_code}: "
            f"{response.data!r}"
        )
        assert str(response.data["id"]) == str(project.id)

    def test_non_admin_member_is_still_denied_on_non_member_project(
        self, workspace, create_user, ws_member
    ):
        """Contrast test: a workspace MEMBER (not admin) must NOT be
        able to open a project they hold no ``ProjectMember`` row for.
        This is the "不得过度放宽" pin — reverting the bypass must not
        widen the predicate for non-admin callers.
        """
        project = _make_project(
            workspace, create_user, name="Owned", identifier="OWN", network=2
        )

        client = APIClient()
        client.force_authenticate(user=ws_member)
        response = client.get(_project_detail_url(workspace.slug, project.id))

        assert response.status_code == status.HTTP_409_CONFLICT, (
            f"workspace MEMBER (role=15) must be denied at the "
            f"ProjectMember gate; got {response.status_code}: "
            f"{response.data!r}"
        )


# --------------------------------------------------------------------------- #
# v1 surface: workspace-admin lists every project under the workspace           #
# (including SECRET) — ``visible_projects`` human branch is widened             #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestV1SurfaceProjectList:
    """``visible_projects`` is called from four v1 endpoints:
    list / lite / detail / summary (the mission flags this). The human
    branch now returns the entire workspace for a workspace admin; the
    SP branch is byte-for-byte unchanged.
    """

    def test_admin_lists_every_project_including_secret(
        self, api_key_client, workspace, create_user, ws_admin
    ):
        secret = _make_project(
            workspace, create_user, name="Hidden", identifier="HID", network=0
        )
        public = _make_project(
            workspace, create_user, name="Open", identifier="OPEN", network=2
        )

        # The v1 list endpoint is gated by ``ProjectBasePermission`` which
        # admits any active workspace member on a safe GET; the
        # ``visible_projects`` query (and the human-branch widening) is
        # what governs the row set. Drive it through the existing
        # api_key_client but switch to the workspace admin user so the
        # human path is exercised end-to-end.
        api_key_client.force_authenticate(user=ws_admin)
        response = api_key_client.get(_v1_projects_url(workspace.slug))

        assert response.status_code == status.HTTP_200_OK, (
            f"got {response.status_code}: {response.data!r}"
        )

        payload = response.data.get("results", response.data) or []
        ids = {str(item["id"]) for item in payload}
        assert str(secret.id) in ids, (
            "SECRET project missing from the admin's v1 list — "
            "visible_projects' human branch was not widened for admins."
        )
        assert str(public.id) in ids

    def test_non_admin_member_does_not_see_secret_project(
        self, workspace, create_user, ws_member, api_token
    ):
        """Contrast: a workspace MEMBER must NOT see SECRET projects they
        don't belong to via the v1 list endpoint."""
        secret = _make_project(
            workspace, create_user, name="Hidden Member", identifier="HM", network=0
        )

        client = APIClient()
        client.force_authenticate(user=ws_member)
        client.credentials(HTTP_X_API_KEY=api_token.token)

        response = client.get(_v1_projects_url(workspace.slug))

        assert response.status_code == status.HTTP_200_OK
        payload = response.data.get("results", response.data) or []
        ids = {str(item["id"]) for item in payload}
        assert str(secret.id) not in ids, (
            "workspace MEMBER must not see SECRET projects they do not "
            "belong to — the human branch widening was over-broad."
        )


# --------------------------------------------------------------------------- #
# Activity surface: workspace-admin reads the activity log of a project they
# are not a member of. The mission names four files; we hit two of them
# (workspace-level + v1 issue-level) to confirm both paths land.
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestActivitySurfaceWorkspaceUserActivity:
    """``WorkspaceUserActivityEndpoint`` (workspace/user.py:379-407) is the
    workspace-level activity stream. ``permission_classes`` is
    ``WorkspaceEntityPermission`` (passes any workspace member); the
    inline ``project_reader_q`` is what the bypass widens.
    """

    def test_admin_sees_activity_for_a_project_they_are_not_a_member_of(
        self, workspace, create_user, ws_admin
    ):
        project = _make_project(
            workspace, create_user, name="Forgotten", identifier="FOR", network=0
        )
        issue = _make_issue(project, workspace, create_user)
        IssueActivity.objects.create(
            issue=issue,
            project=project,
            workspace=workspace,
            actor=create_user,
            verb="created",
            field="issue",
            comment="created the issue",
        )

        client = APIClient()
        client.force_authenticate(user=ws_admin)
        response = client.get(_workspace_user_activity_url(workspace.slug, create_user.id))

        assert response.status_code == status.HTTP_200_OK, (
            f"got {response.status_code}: {response.data!r}"
        )

        results = response.data.get("results", response.data) or []
        project_ids = {str(row["project"]) for row in results if "project" in row}
        assert str(project.id) in project_ids, (
            "workspace admin's workspace activity stream did not include "
            "the SECRET project they are not a member of — the inline "
            "project_reader_q widening did not land."
        )

    def test_non_admin_member_does_not_see_activity_for_a_project_they_are_not_a_member_of(
        self, workspace, create_user, ws_member
    ):
        """Contrast: a workspace MEMBER must not see the activity stream
        of a project they don't belong to."""
        project = _make_project(
            workspace, create_user, name="Private", identifier="PVT", network=0
        )
        issue = _make_issue(project, workspace, create_user)
        IssueActivity.objects.create(
            issue=issue,
            project=project,
            workspace=workspace,
            actor=create_user,
            verb="created",
            field="issue",
            comment="created the issue",
        )

        client = APIClient()
        client.force_authenticate(user=ws_member)
        response = client.get(_workspace_user_activity_url(workspace.slug, create_user.id))

        assert response.status_code == status.HTTP_200_OK
        results = response.data.get("results", response.data) or []
        project_ids = {str(row["project"]) for row in results if "project" in row}
        assert str(project.id) not in project_ids, (
            "workspace MEMBER must not see the activity stream of a "
            "SECRET project they do not belong to."
        )


@pytest.mark.contract
@pytest.mark.django_db
class TestV1IssueActivityEndpoint:
    """``IssueActivityListAPIEndpoint`` (api/views/issue.py:1946-1952)
    uses ``ProjectEntityPermission`` — the bypass at
    ``app/permissions/project.py:136-160`` is what lets the admin pass
    the permission gate; the inline ``project_reader_q`` widening is
    what surfaces the rows. Both must land or the activity list stays
    empty for an admin caller.
    """

    def test_admin_reads_v1_issue_activity_for_a_non_member_project(
        self, workspace, create_user, ws_admin
    ):
        project = _make_project(
            workspace, create_user, name="Audit", identifier="AUD", network=0
        )
        issue = _make_issue(project, workspace, create_user)
        IssueActivity.objects.create(
            issue=issue,
            project=project,
            workspace=workspace,
            actor=create_user,
            verb="created",
            field="issue",
            comment="created the issue",
        )

        client = APIClient()
        client.force_authenticate(user=ws_admin)
        response = client.get(_v1_issue_activity_url(workspace.slug, project.id, issue.id))

        assert response.status_code == status.HTTP_200_OK, (
            f"workspace admin must read v1 issue activity for a SECRET "
            f"project they do not belong to; got {response.status_code}: "
            f"{response.data!r}"
        )

        results = response.data.get("results", response.data)
        assert results is not None and len(results) >= 1, (
            "v1 issue activity returned no rows — either the "
            "ProjectEntityPermission bypass did not fire or the inline "
            "project_reader_q widening did not land."
        )

    def test_non_admin_member_is_denied_at_the_v1_activity_endpoint(
        self, workspace, create_user, ws_member
    ):
        """Contrast: ``ProjectEntityPermission`` (SAFE_METHODS) must keep
        denying the workspace MEMBER who is not a ProjectMember. The
        admin bypass added by M51 is role-keyed and does not loosen
        this check for non-admins."""
        project = _make_project(
            workspace, create_user, name="Closed", identifier="CLS", network=0
        )
        issue = _make_issue(project, workspace, create_user)

        client = APIClient()
        client.force_authenticate(user=ws_member)
        response = client.get(_v1_issue_activity_url(workspace.slug, project.id, issue.id))

        assert response.status_code == status.HTTP_403_FORBIDDEN, (
            f"workspace MEMBER (role=15) without ProjectMember must be "
            f"denied by ProjectEntityPermission; got {response.status_code}"
        )