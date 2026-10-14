# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: the v1 write entry points hand their row to the ownership
step (M41).

M40 gave :func:`plane.core.authz.authorize` a per-resource ownership step
(``DENY_OWNER_MISMATCH``): a member-tier grant (``role_cap < 20``) may only
``update``/``delete`` a row its own bot user created. That step is
fail-closed when the caller supplies no row, so every v1 update/delete view
must pass the instance it is about to mutate. These tests drive the real
HTTP endpoints and pin the user-facing contract:

* a service principal can read, comment on and create around its work items
  at member tier;
* it can update and delete the work items and comments **it created**;
* it cannot update or delete a row another service principal created — the
  403 comes from the authz chain with an ownership reason, not from the
  view's human-permission classes.

The owning column is ``created_by`` for every resource type (the bot user
``BaseModel.save`` credits a service-principal write to), including pages —
never the human-facing ``owned_by``. The page round trip lives in
``test_sp_v1_wiring.py``; this file covers the work-item and comment chains.
"""

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import Issue, IssueComment, Project, ProjectMember
from plane.db.models.api import APIToken
from plane.service_principals.constants import PrincipalType
from plane.service_principals.models import (
    ProjectGrant,
    ServicePrincipal,
    ServiceScope,
)


MEMBER_CAP = 15


def _work_items_url(workspace_slug, project_id):
    return f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/work-items/"


def _work_item_url(workspace_slug, project_id, issue_id):
    return (
        f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}"
        f"/work-items/{issue_id}/"
    )


def _comments_url(workspace_slug, project_id, issue_id):
    return (
        f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}"
        f"/work-items/{issue_id}/comments/"
    )


def _comment_url(workspace_slug, project_id, issue_id, comment_id):
    return _comments_url(workspace_slug, project_id, issue_id) + f"{comment_id}/"


def _make_sp(workspace, owner, name, project):
    """A member-tier SP with read/create/update/delete on work items and
    comments in ``project``."""
    sp = ServicePrincipal.objects.create(
        workspace=workspace, owner=owner, name=name
    )
    APIToken.objects.create(
        label=f"svc:{name}",
        token=f"plane_svc_token_{name}",
        user=owner,
        principal_type=PrincipalType.SERVICE,
        service_principal=sp,
        workspace=workspace,
    )
    for resource_type, actions in (
        ("work_item", ["read", "create", "update", "delete"]),
        ("comment", ["read", "create", "update", "delete"]),
    ):
        for action in actions:
            ServiceScope.objects.create(
                service_principal=sp,
                project=project,
                resource_type=resource_type,
                action=action,
            )
    ProjectGrant.objects.create(
        service_principal=sp, project=project, role_cap=MEMBER_CAP
    )
    return sp


def _client(sp):
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=f"plane_svc_token_{sp.name}")
    return client


def _create_work_item(client, workspace, project, name):
    response = client.post(
        _work_items_url(workspace.slug, project.id),
        data={"name": name},
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED, response.data
    return response.data["id"]


@pytest.fixture
def project(db, workspace, create_user):
    proj = Project.objects.create(
        name="Ownership Wiring Project",
        identifier="OWP",
        workspace=workspace,
        created_by=create_user,
        network=2,
    )
    ProjectMember.objects.create(
        project=proj, member=create_user, role=20, is_active=True
    )
    return proj


@pytest.mark.contract
@pytest.mark.django_db
class TestSPWritesItsOwnRows:
    """The ``resource=`` argument is populated on update/delete: an SP's own
    rows stay writable at member tier."""

    def test_sp_creates_then_updates_and_deletes_its_own_work_item(
        self, workspace, create_user, project
    ):
        sp = _make_sp(workspace, create_user, "own-item", project)
        client = _client(sp)

        issue_id = _create_work_item(client, workspace, project, "Own Item")

        # The v1 create path credits the bot, not the token's human owner —
        # that is what the ownership step then matches on.
        issue = Issue.objects.get(pk=issue_id)
        assert issue.created_by_id == sp.ensure_bot_user().pk

        patched = client.patch(
            _work_item_url(workspace.slug, project.id, issue_id),
            data={"name": "Own Item Renamed"},
            format="json",
        )
        assert patched.status_code == status.HTTP_200_OK, patched.data
        issue.refresh_from_db()
        assert issue.name == "Own Item Renamed"

        deleted = client.delete(
            _work_item_url(workspace.slug, project.id, issue_id)
        )
        assert deleted.status_code == status.HTTP_204_NO_CONTENT, deleted.data

    def test_sp_updates_and_deletes_its_own_comment(
        self, workspace, create_user, project
    ):
        sp = _make_sp(workspace, create_user, "own-comment", project)
        client = _client(sp)
        issue_id = _create_work_item(client, workspace, project, "Commented Item")

        created = client.post(
            _comments_url(workspace.slug, project.id, issue_id),
            data={"comment_html": "<p>mine</p>"},
            format="json",
        )
        assert created.status_code == status.HTTP_201_CREATED, created.data
        comment_id = created.data["id"]
        comment = IssueComment.objects.get(pk=comment_id)
        assert comment.created_by_id == sp.ensure_bot_user().pk

        patched = client.patch(
            _comment_url(workspace.slug, project.id, issue_id, comment_id),
            data={"comment_html": "<p>edited</p>"},
            format="json",
        )
        assert patched.status_code == status.HTTP_200_OK, patched.data

        deleted = client.delete(
            _comment_url(workspace.slug, project.id, issue_id, comment_id)
        )
        assert deleted.status_code == status.HTTP_204_NO_CONTENT, deleted.data


@pytest.mark.contract
@pytest.mark.django_db
class TestOtherSPReadsAndCommentsButCannotWrite:
    """The user's contract: SP B may read SP A's work item and comment on it,
    but every update/delete of that row is a 403 from the authz chain."""

    def test_other_sp_can_read_and_comment_on_a_work_item(
        self, workspace, create_user, project
    ):
        sp_a = _make_sp(workspace, create_user, "read-comment-a", project)
        sp_b = _make_sp(workspace, create_user, "read-comment-b", project)
        issue_id = _create_work_item(
            _client(sp_a), workspace, project, "A's Item"
        )

        detail = _client(sp_b).get(
            _work_item_url(workspace.slug, project.id, issue_id)
        )
        assert detail.status_code == status.HTTP_200_OK, detail.data

        commented = _client(sp_b).post(
            _comments_url(workspace.slug, project.id, issue_id),
            data={"comment_html": "<p>looks good</p>"},
            format="json",
        )
        assert commented.status_code == status.HTTP_201_CREATED, commented.data

    def test_other_sp_cannot_update_or_delete_a_work_item(
        self, workspace, create_user, project
    ):
        sp_a = _make_sp(workspace, create_user, "deny-a", project)
        sp_b = _make_sp(workspace, create_user, "deny-b", project)
        issue_id = _create_work_item(
            _client(sp_a), workspace, project, "A's Item"
        )
        # Provision B's bot user first: without one the chain denies for the
        # blunter "no attribution bot user" reason rather than the
        # "another actor" mismatch this test is about.
        sp_b.ensure_bot_user()
        client_b = _client(sp_b)

        denied_patch = client_b.patch(
            _work_item_url(workspace.slug, project.id, issue_id),
            data={"name": "Hijacked"},
            format="json",
        )
        assert denied_patch.status_code == status.HTTP_403_FORBIDDEN
        assert "may only update a work_item it created" in denied_patch.json()[
            "detail"
        ]

        denied_delete = client_b.delete(
            _work_item_url(workspace.slug, project.id, issue_id)
        )
        assert denied_delete.status_code == status.HTTP_403_FORBIDDEN
        assert "may only delete a work_item it created" in denied_delete.json()[
            "detail"
        ]

        # The denied write changed nothing.
        issue = Issue.objects.get(pk=issue_id)
        assert issue.name == "A's Item"
        assert issue.deleted_at is None

    def test_other_sp_cannot_update_or_delete_a_comment(
        self, workspace, create_user, project
    ):
        sp_a = _make_sp(workspace, create_user, "comment-deny-a", project)
        sp_b = _make_sp(workspace, create_user, "comment-deny-b", project)
        issue_id = _create_work_item(
            _client(sp_a), workspace, project, "A's Item"
        )
        created = _client(sp_a).post(
            _comments_url(workspace.slug, project.id, issue_id),
            data={"comment_html": "<p>A's comment</p>"},
            format="json",
        )
        assert created.status_code == status.HTTP_201_CREATED, created.data
        comment_id = created.data["id"]

        # See the work-item case above: give B a bot user so the denial is the
        # "another actor" mismatch rather than the missing-bot fail-closed.
        sp_b.ensure_bot_user()
        client_b = _client(sp_b)
        denied_patch = client_b.patch(
            _comment_url(workspace.slug, project.id, issue_id, comment_id),
            data={"comment_html": "<p>hijacked</p>"},
            format="json",
        )
        assert denied_patch.status_code == status.HTTP_403_FORBIDDEN
        assert "may only update a comment it created" in denied_patch.json()[
            "detail"
        ]

        denied_delete = client_b.delete(
            _comment_url(workspace.slug, project.id, issue_id, comment_id)
        )
        assert denied_delete.status_code == status.HTTP_403_FORBIDDEN
        assert "may only delete a comment it created" in denied_delete.json()[
            "detail"
        ]

    def test_admin_tier_grant_is_not_ownership_scoped(
        self, workspace, create_user, project
    ):
        """A ``role_cap = 20`` grant writes another SP's row: the ownership
        step exempts admin-tier grants."""
        sp_a = _make_sp(workspace, create_user, "admin-exempt-a", project)
        issue_id = _create_work_item(
            _client(sp_a), workspace, project, "A's Item"
        )

        sp_admin = _make_sp(workspace, create_user, "admin-exempt-b", project)
        ProjectGrant.objects.filter(
            service_principal=sp_admin, project=project
        ).update(role_cap=20)

        response = _client(sp_admin).patch(
            _work_item_url(workspace.slug, project.id, issue_id),
            data={"name": "Admin Edit"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data
