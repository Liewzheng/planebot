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


def _make_issue(project, workspace, created_by, *, name: str = "Visibility issue") -> Issue:
    """Create an issue with a default state. The state lookup is
    idempotent so multiple issues can share a single Backlog state
    (the ``State`` model has a unique constraint on (name, project_id)
    when deleted_at is NULL)."""
    state, _ = State.objects.get_or_create(
        name="Backlog",
        project=project,
        deleted_at__isnull=True,
        defaults={
            "workspace": workspace,
            "group": "backlog",
            "default": True,
        },
    )
    return Issue.objects.create(
        name=name,
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


def _app_issue_list_url(slug: str, project_id) -> str:
    """``IssueViewSet.list`` (line 87 of ``app/views/issue/base.py``) at
    ``/api/workspaces/<slug>/projects/<project_id>/issues/`` — the
    project-scoped create / list endpoint. Uses
    ``@allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])``
    (project-scoped); r2 widened the decorator so an admin caller
    reads this even without a ``ProjectMember`` row."""
    return f"/api/workspaces/{slug}/projects/{project_id}/issues/"


def _app_issue_detail_url(slug: str, project_id) -> str:
    """``IssueDetailEndpoint.get`` at
    ``app/views/issue/base.py:987-1116`` — the project-issue DETAIL /
    paginated LIST endpoint (class name is historical). Uses
    ``permission_subquery`` and ``@allow_permission`` (the two filters
    M51 widens in this round). The URL is
    ``/api/workspaces/<slug>/projects/<project_id>/issues-detail/``
    per ``app/urls/issue.py:48`` — the ``/issues/`` URL routes to
    ``IssueViewSet.list`` instead, see the r2 review note. This is a
    real frontend path: ``issue.service.ts:47`` selects the
    ``*-detail/`` variant when ``expand`` contains ``issue_relation``,
    and ``issue-filter-helper.store.ts:123`` sets that expand for the
    GANTT layout."""
    return f"/api/workspaces/{slug}/projects/{project_id}/issues-detail/"


def _app_issue_activity_url(slug: str, project_id, issue_id) -> str:
    """``IssueActivityEndpoint.get`` (app/views/issue/activity.py) — the
    app-side issue-activity endpoint. Drives both the
    ``ProjectEntityPermission`` gate and the ``@allow_permission``
    decorator on the same handler."""
    return (
        f"/api/workspaces/{slug}/projects/{project_id}"
        f"/issues/{issue_id}/history/"
    )


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


# --------------------------------------------------------------------------- #
# App-side issue LIST endpoint (the user's "列/读" half)                        #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestAppSideIssueDetailEndpoint:
    """``IssueDetailEndpoint.get`` at
    ``app/views/issue/base.py:987-1116`` — the project-issue DETAIL /
    paginated LIST endpoint. URL is
    ``/api/workspaces/<slug>/projects/<project_id>/issues-detail/``
    per ``app/urls/issue.py:48``; the class name is historical, but
    the endpoint has BOTH a ``permission_subquery`` (which filters by
    ProjectMember) AND a ``@allow_permission([ROLE.ADMIN, ROLE.MEMBER,
    ROLE.GUEST])`` (project-scoped). M51 widens both: r1 widened the
    permission class only; r2 widened ``permission_subquery`` and the
    ``@allow_permission`` decorator's project-scoped role check, so an
    admin who is not a ProjectMember of the project can list every
    issue — including in a SECRET project — exactly the symptom the
    user filed. The r2 review confirmed this is a real frontend path:
    ``apps/web/core/services/issue/issue.service.ts:47`` selects the
    ``*-detail/`` variant when ``expand`` contains ``issue_relation``
    (the GANTT layout), and
    ``apps/web/core/store/issue/helpers/issue-filter-helper.store.ts:123``
    sets that expand. **Reverting only the r2 change to
    ``app/views/issue/base.py`` (i.e. dropping the ``WorkspaceMember``
    branch in ``permission_subquery``) flips both tests below to red.**
    """

    def test_admin_lists_issues_in_a_secret_project_they_are_not_a_member_of(
        self, workspace, create_user, ws_admin
    ):
        project = _make_project(
            workspace, create_user, name="Vault", identifier="VLT", network=0
        )
        _make_issue(project, workspace, create_user)
        _make_issue(project, workspace, create_user)  # a second row

        client = APIClient()
        client.force_authenticate(user=ws_admin)
        response = client.get(_app_issue_detail_url(workspace.slug, project.id))

        assert response.status_code == status.HTTP_200_OK, (
            f"workspace admin must list issues in a SECRET project they "
            f"do not belong to via the /issues-detail/ route "
            f"(the '看得到但看不见里面' symptom); got {response.status_code}: "
            f"{response.data!r}"
        )

        results = response.data.get("results", response.data) or []
        ids = {str(row["id"]) for row in results if "id" in row}
        # Both seeded issues must surface.
        assert len(ids) >= 2, (
            "app-side issue list (issues-detail/) returned fewer rows "
            "than seeded — the permission_subquery widening did not land "
            "(the admin is ProjectMember-less on this project)."
        )

    def test_non_admin_member_gets_empty_for_views_detail_route(
        self, workspace, create_user, ws_member
    ):
        """Contrast: a workspace MEMBER who is not a ProjectMember must
        still see zero rows on the /issues-detail/ route (the existing
        ProjectMember predicate holds). This pins the "不得过度放宽"
        half on the ``IssueDetailEndpoint``."""
        project = _make_project(
            workspace, create_user, name="Quiet", identifier="QZT", network=0
        )
        _make_issue(project, workspace, create_user)
        _make_issue(project, workspace, create_user)

        client = APIClient()
        client.force_authenticate(user=ws_member)
        response = client.get(_app_issue_detail_url(workspace.slug, project.id))

        # The MEMBER is a workspace member so passes the workspace
        # scope; the project-scoped ``@allow_permission`` (with the
        # new admin bypass restricted to role=20) still denies; the
        # permission_subquery (non-admin branch) returns zero rows.
        # Whichever layer fires first — both are correct.
        assert response.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_200_OK,
        ), (
            f"workspace MEMBER (role=15) must NOT be admitted on the "
            f"/issues-detail/ route for a project they do not belong to; "
            f"got {response.status_code}: {response.data!r}"
        )
        if response.status_code == status.HTTP_200_OK:
            results = response.data.get("results", response.data) or []
            assert len(results) == 0, (
                f"workspace MEMBER must see zero issues via /issues-detail/ "
                f"in a project they do not belong to; got {len(results)}"
            )


# --------------------------------------------------------------------------- #
# App-side issue ACTIVITY endpoint — the second half of the user's complaint   #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestAppSideIssueActivityEndpoint:
    """``IssueActivityEndpoint`` at
    ``app/views/issue/activity.py:24-86`` is the app-side activity
    feed. The handler has BOTH ``permission_classes = [ProjectEntityPermission]``
    AND ``@allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])`` —
    both must widen or the decorator (the r1 gap) blocks the admin
    before the inline ``project_reader_q`` is even reached.
    """

    def test_admin_reads_app_side_issue_activity_for_a_non_member_project(
        self, workspace, create_user, ws_admin
    ):
        project = _make_project(
            workspace, create_user, name="Ledger", identifier="LDG", network=0
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
        # ``?activity_type=issue-property`` selects the serialized
        # branch; the combined branch (no query param) has a pre-
        # existing ``instance["created_at"]`` access that predates
        # M51 and is unrelated to this mission's bypass.
        response = client.get(
            _app_issue_activity_url(workspace.slug, project.id, issue.id)
            + "?activity_type=issue-property"
        )

        assert response.status_code == status.HTTP_200_OK, (
            f"workspace admin must read the app-side activity feed for a "
            f"SECRET project they are not a member of (the user's "
            f"'看得到但看不见里面' second half); got {response.status_code}: "
            f"{response.data!r}"
        )

        assert isinstance(response.data, list) and len(response.data) >= 1, (
            "app-side activity returned no rows — either the "
            "@allow_permission widening did not fire or the inline "
            "project_reader_q widening did not land."
        )

    def test_non_admin_member_is_denied_at_the_app_side_activity_endpoint(
        self, workspace, create_user, ws_member
    ):
        """Contrast: a workspace MEMBER who is not a ProjectMember must
        still be denied at ``@allow_permission``. The widening is
        restricted to ``WorkspaceMember.role=20`` AND ``SAFE_METHODS``
        so the role=15 path is unchanged."""
        project = _make_project(
            workspace, create_user, name="Ledger Private", identifier="LDP", network=0
        )
        issue = _make_issue(project, workspace, create_user)

        client = APIClient()
        client.force_authenticate(user=ws_member)
        # Same query param as the admin branch so both branches run
        # the same code path.
        response = client.get(
            _app_issue_activity_url(workspace.slug, project.id, issue.id)
            + "?activity_type=issue-property"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN, (
            f"workspace MEMBER (role=15) without ProjectMember must be "
            f"denied at @allow_permission; got {response.status_code}: "
            f"{response.data!r}"
        )


# --------------------------------------------------------------------------- #
# Write-side guarantee: a non-member admin can read but NOT write              #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestAppSideWriteSideStaysDenied:
    """M51 widens the SAFE reads. The write path through
    ``@allow_permission`` must remain at the original ProjectMember /
    workspace-admin-and-ProjectMember semantics — the admin bypass is
    SAFE_METHODS-only by construction. This class exercises a write
    endpoint (``POST`` to create a new issue in a SECRET project the
    admin is not a member of) and asserts the admin gets 403 even
    though they are a workspace ADMIN. If this test ever flips green,
    the SAFE_METHODS gating on ``allow_permission`` has been removed
    and a workspace admin can mutate any project under their
    workspace — that is the regression to catch.

    The endpoint picked is the project-scoped ``IssueViewSet.create`` at
    ``app/views/issue/base.py`` (the project-scoped create that uses
    ``@allow_permission([ROLE.ADMIN, ROLE.MEMBER])``).
    """

    def test_admin_cannot_create_issue_in_a_project_they_are_not_a_member_of(
        self, workspace, create_user, ws_admin
    ):
        from plane.db.models import State

        project = _make_project(
            workspace, create_user, name="Off-limits", identifier="OFF", network=0
        )
        state = State.objects.create(
            name="Backlog",
            project=project,
            workspace=workspace,
            group="backlog",
            default=True,
        )

        client = APIClient()
        client.force_authenticate(user=ws_admin)
        # ``IssueViewSet.create`` — POST is not SAFE_METHODS, so the
        # M51 admin bypass MUST NOT fire; the original ProjectMember
        # predicate denies.
        response = client.post(
            _app_issue_list_url(workspace.slug, project.id),
            {"name": "Admin issue", "state_id": str(state.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN, (
            f"workspace admin (role=20) without ProjectMember must NOT "
            f"be able to CREATE an issue in a SECRET project (writes "
            f"are not part of the bypass); got {response.status_code}: "
            f"{response.data!r}"
        )


# --------------------------------------------------------------------------- #
# P1 probe: ``/issues-detail/`` smoke                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestIssuesDetailEndpointSmoke:
    """Smoke checks against ``GET /api/workspaces/<slug>/projects/<pid>/issues-detail/``.

    The r2 round shipped a ``NameError`` here (``WorkspaceMember``
    referenced without an import) that surfaced as an HTTP 500 on
    this route. No test in the file touched the endpoint at the time,
    so the regression reached the merge gate green. The r3 commit
    landed the import; these probes exercise the route so a future
    missing-import regression cannot.

    The SP probe asserts the deny body (``{"detail": "Authentication
    required."}``) rather than only the status code — that is the
    signal that the request was rejected by
    ``BaseAPIView.permission_classes = [IsAuthenticatedNoSP]``
    (``apps/api/plane/app/views/base.py:167``) before the decorator
    and the body ran. An SP request that reached ``_authorize_sp``
    would produce a different body (``{"error": "Endpoint does not
    declare resource_type for SP authz."}``), so the body assertion
    distinguishes the two.
    """

    @pytest.mark.parametrize("network", [0, 2], ids=["SECRET", "PUBLIC"])
    def test_probe_admin_gets_200_on_issues_detail(self, workspace, ws_admin, network):
        from plane.db.models import ProjectMember

        project = _make_project(
            workspace, ws_admin, name=f"Admin {network}", identifier=f"A{network}", network=network
        )
        ProjectMember.objects.filter(project=project, member=ws_admin).delete()

        client = APIClient()
        client.force_authenticate(user=ws_admin)
        response = client.get(_app_issue_detail_url(workspace.slug, project.id))
        assert response.status_code == status.HTTP_200_OK, (
            f"admin /issues-detail/ on network={network} returned "
            f"{response.status_code} (likely NameError on WorkspaceMember): "
            f"{response.data!r}"
        )

    @pytest.mark.parametrize("network", [0, 2], ids=["SECRET", "PUBLIC"])
    def test_probe_plain_member_gets_200_on_issues_detail(
        self, workspace, create_user, ws_member, network
    ):
        from plane.db.models import ProjectMember

        # The MEMBER has workspace role=15 but no ProjectMember row;
        # for this probe we want them to be a ProjectMember (any role)
        # so they reach the endpoint body.
        project = _make_project(
            workspace, create_user,
            name=f"Member {network}", identifier=f"M{network}", network=network,
            members=[ws_member],
        )
        ProjectMember.objects.filter(
            project=project, member=ws_member
        ).update(role=15)

        client = APIClient()
        client.force_authenticate(user=ws_member)
        response = client.get(_app_issue_detail_url(workspace.slug, project.id))
        assert response.status_code == status.HTTP_200_OK, (
            f"project-member /issues-detail/ on network={network} "
            f"returned {response.status_code} (likely NameError on "
            f"WorkspaceMember): {response.data!r}"
        )

    @pytest.mark.parametrize("network", [0, 2], ids=["SECRET", "PUBLIC"])
    def test_probe_guest_project_member_gets_200_on_issues_detail(
        self, workspace, create_user, ws_guest, network
    ):
        from plane.db.models import ProjectMember

        project = _make_project(
            workspace, create_user,
            name=f"Guest {network}", identifier=f"G{network}", network=network,
            members=[ws_guest],
        )
        ProjectMember.objects.filter(
            project=project, member=ws_guest
        ).update(role=5)

        client = APIClient()
        client.force_authenticate(user=ws_guest)
        response = client.get(_app_issue_detail_url(workspace.slug, project.id))
        assert response.status_code == status.HTTP_200_OK, (
            f"guest-as-ProjectMember /issues-detail/ on network={network} "
            f"returned {response.status_code} (likely NameError on "
            f"WorkspaceMember): {response.data!r}"
        )

    @pytest.mark.parametrize("network", [0, 2], ids=["SECRET", "PUBLIC"])
    def test_probe_sp_gets_403_on_issues_detail(
        self, workspace, create_user, network
    ):
        from plane.db.models import APIToken
        from plane.service_principals.constants import PrincipalType
        from plane.service_principals.models import ServicePrincipal

        project = _make_project(
            workspace, create_user,
            name=f"SP {network}", identifier=f"S{network}", network=network,
        )

        sp_name = f"probe-sp-{network}"
        sp = ServicePrincipal.objects.create(
            workspace=workspace, owner=create_user, name=sp_name
        )
        APIToken.objects.create(
            user=create_user,
            label=f"svc:{sp_name}",
            token=f"plane_svc_token_{sp_name}",
            user_type=1,
            is_service=True,
            principal_type=PrincipalType.SERVICE,
            service_principal=sp,
            workspace=workspace,
        )

        client = APIClient()
        client.credentials(HTTP_X_API_KEY=f"plane_svc_token_{sp_name}")
        response = client.get(_app_issue_detail_url(workspace.slug, project.id))

        assert response.status_code == status.HTTP_403_FORBIDDEN, (
            f"SP /issues-detail/ on network={network} returned "
            f"{response.status_code} (expected 403): {response.data!r}"
        )
        assert response.data == {"detail": "Authentication required."}, (
            f"SP /issues-detail/ on network={network} returned an "
            f"unexpected deny body {response.data!r}; the body "
            f"``{{'detail': 'Authentication required.'}}`` is what "
            f"``IsAuthenticatedNoSP`` returns when the request is "
            f"rejected before the decorator and the body run."
        )

    @pytest.mark.parametrize("network", [0, 2], ids=["SECRET", "PUBLIC"])
    def test_probe_expand_issue_relation_does_not_500(self, workspace, ws_admin, network):
        project = _make_project(
            workspace, ws_admin,
            name=f"Expand {network}", identifier=f"E{network}", network=network,
        )

        client = APIClient()
        client.force_authenticate(user=ws_admin)
        response = client.get(
            _app_issue_detail_url(workspace.slug, project.id)
            + "?expand=issue_relation"
        )
        assert response.status_code == status.HTTP_200_OK, (
            f"admin /issues-detail/?expand=issue_relation on network="
            f"{network} returned {response.status_code} (likely "
            f"NameError on WorkspaceMember): {response.data!r}"
        )
