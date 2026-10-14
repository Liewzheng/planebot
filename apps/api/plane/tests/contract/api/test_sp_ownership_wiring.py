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
``test_sp_v1_wiring.py``; this file covers the work-item and comment chains
plus the M41 r2 sweep (PLANE-111 migration to ``/projects/<id>/summary/``,
the intake PATCH "owner-keyed ProjectMember" guard, and the cycle transfer
activity signature).
"""

import uuid
from unittest.mock import patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import (
    Cycle,
    CycleIssue,
    FileAsset,
    Intake,
    IntakeIssue,
    Issue,
    IssueComment,
    Project,
    ProjectMember,
    State,
    StateGroup,
)
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


def _attachment_url(workspace_slug, project_id, issue_id, asset_id):
    return (
        f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}"
        f"/work-items/{issue_id}/attachments/{asset_id}/"
    )


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


@pytest.fixture
def ownerless_project(db, workspace, create_user):
    """A project the SP's human owner is deliberately NOT a member of.

    The SP is still granted into it, so the authz chain authorizes its writes;
    the legacy attachment guard — which keyed off ``request.user``, the SP's
    *owner* — did not.
    """
    return Project.objects.create(
        name="Ownerless Project",
        identifier="OWNP",
        workspace=workspace,
        network=2,
    )


def _attachment(project, workspace, issue, created_by):
    """An unconfirmed attachment row owned by ``created_by``.

    ``storage_metadata`` is pre-populated so the view skips the
    ``get_asset_object_metadata`` S3 task. ``created_by`` is written with
    ``disable_auto_set_user`` because ``BaseModel.save`` would otherwise null
    it outside a request — and it is the column the ownership step reads.
    """
    asset = FileAsset(
        attributes={"name": "note.pdf", "type": "application/pdf", "size": 12},
        asset=f"{uuid.uuid4().hex}-note.pdf",
        size=12,
        workspace_id=workspace.id,
        project_id=project.id,
        issue_id=issue.id,
        entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
        is_uploaded=False,
        storage_metadata={"ContentLength": 12},
        created_by=created_by,
    )
    asset.save(disable_auto_set_user=True)
    asset.refresh_from_db()
    return asset


@pytest.mark.contract
@pytest.mark.django_db
class TestSPAttachmentWritesDoNotConsultTheOwnersMembership:
    """The attachment guards used to call
    ``user_has_issue_permission(request.user.id, ...)`` — the SP's human
    **owner**. For a ``plane_svc_`` token that is the wrong principal: the
    authz chain has already run in ``check_permissions`` and, at member tier,
    requires the SP's own bot user to have created the row, so the membership
    model must not be re-applied to the owner. Before the fix an SP whose owner
    was not a project member got a 403 from the body guard on an attachment the
    chain had allowed.

    The first two tests fail against the old guard and pass against the new
    one; the third proves the change is not fail-open.
    """

    def _scenario(self, workspace, create_user, ownerless_project, name="att-sp"):
        sp = _make_sp(workspace, create_user, name, ownerless_project)
        bot = sp.ensure_bot_user()
        issue = Issue.objects.create(
            name="Ownerless Issue",
            project=ownerless_project,
            workspace=workspace,
        )
        asset = _attachment(ownerless_project, workspace, issue, bot)
        return sp, issue, asset

    def test_sp_confirms_its_own_attachment_upload(
        self, workspace, create_user, ownerless_project
    ):
        sp, issue, asset = self._scenario(workspace, create_user, ownerless_project)

        response = _client(sp).patch(
            _attachment_url(
                workspace.slug, ownerless_project.id, issue.id, asset.id
            ),
            data={"is_uploaded": True},
            format="json",
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT, response.data
        asset.refresh_from_db()
        assert asset.is_uploaded is True
        # The confirm stays credited to the bot, so the SP keeps owning the row.
        assert asset.created_by_id == sp.bot_user_id

    def test_sp_deletes_its_own_attachment(
        self, workspace, create_user, ownerless_project
    ):
        sp, issue, asset = self._scenario(workspace, create_user, ownerless_project)

        response = _client(sp).delete(
            _attachment_url(
                workspace.slug, ownerless_project.id, issue.id, asset.id
            )
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT, response.data
        asset.refresh_from_db()
        assert asset.is_deleted is True

    def test_other_sp_still_cannot_touch_the_attachment(
        self, workspace, create_user, ownerless_project
    ):
        """Not fail-open: the chain still denies a second SP the attachment row
        it did not create, before the handler ever runs."""
        _sp_a, issue, asset = self._scenario(
            workspace, create_user, ownerless_project, name="att-sp-a"
        )
        sp_b = _make_sp(workspace, create_user, "att-sp-b", ownerless_project)
        sp_b.ensure_bot_user()

        response = _client(sp_b).patch(
            _attachment_url(
                workspace.slug, ownerless_project.id, issue.id, asset.id
            ),
            data={"is_uploaded": True},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "may only update a work_item it created" in response.json()["detail"]


# --- M41 r2 sweep tests --------------------------------------------------
#
# Three findings the reviewer-m41 round 1 raised as P2:
#
# * P2-1 — ``ProjectSummaryAPIEndpoint`` was the one project-returning
#   endpoint not migrated to ``visible_projects()``, so for an SP without a
#   grant it leaked the project's name / identifier / counts (the
#   list / lite / detail endpoint siblings already filtered by grant).
# * P2-2 — IntakeIssue PATCH did ``ProjectMember.objects.get(member=
#   request.user)`` (the SP's human **owner**) and crashed with a 404 on
#   writes the chain had authorized, plus the same defect in cycle /
#   module / page / issue DELETE handlers.
# * P2-3 — ``TransferCycleIssueAPIEndpoint.post`` signed the activity row
#   with ``self.request.user.id`` (the SP's human owner) instead of the
#   acting principal.
#
# Each test below reproduces the "before the fix" failure and pins the new
# behaviour so a future regression is caught at the test layer.


def _project_summary_url(workspace_slug, project_id):
    return (
        f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/summary/"
    )


def _intake_url(workspace_slug, project_id, issue_id):
    return (
        f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}"
        f"/intake-issues/{issue_id}/"
    )


def _cycle_transfer_url(workspace_slug, project_id, cycle_id):
    return (
        f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}"
        f"/cycles/{cycle_id}/transfer-issues/"
    )


@pytest.mark.contract
@pytest.mark.django_db
class TestSPSummaryEndpointHonorsGrants:
    """PLANE-111 acceptance: for an SP without a grant on project X,
    ``/projects/<X>/summary/`` returns 404 instead of leaking X's
    name / identifier / counts (companion of the list / lite / detail
    endpoints already filtered through ``visible_projects``).

    The first test fails against the un-migrated endpoint (200 with the
    project's metadata) and passes against ``visible_projects`` (404).
    The second test pins that the same SP can still see *granted*
    projects' summaries — i.e., the migration is grant-scoped, not a
    blanket denial.
    """

    def _make_summary_sp(self, workspace, owner, name):
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
        # Workspace-wide ``project:read`` so the engine's scope step
        # admits workspace-level reads (READ is the only
        # ``ALLOWED_WORKSPACE_LEVEL_ACTION``).
        ServiceScope.objects.create(
            service_principal=sp,
            project=None,
            resource_type="project",
            action="read",
        )
        return sp

    def test_ungranted_project_summary_404s(self, workspace, create_user):
        secret = Project.objects.create(
            name="Secret MacDock",
            identifier="MD",
            workspace=workspace,
            created_by=create_user,
            network=2,
        )

        sp = self._make_summary_sp(workspace, create_user, "summary-sp")

        response = _client(sp).get(
            _project_summary_url(workspace.slug, secret.id)
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND, (
            response.status_code, response.data
        )

    def test_granted_project_summary_still_200(
        self, workspace, create_user
    ):
        granted = Project.objects.create(
            name="Granted Project",
            identifier="GP",
            workspace=workspace,
            created_by=create_user,
            network=2,
        )
        sp = self._make_summary_sp(
            workspace, create_user, "summary-granted-sp"
        )
        # Add a ProjectGrant — the SP is *meant* to see this project.
        ProjectGrant.objects.create(
            service_principal=sp, project=granted, role_cap=15
        )
        response = _client(sp).get(
            _project_summary_url(workspace.slug, granted.id)
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        body = response.json()
        assert body["name"] == "Granted Project"
        assert body["identifier"] == "GP"


@pytest.mark.contract
@pytest.mark.django_db
class TestSPCanDeleteOwnIssueThroughChainWhenOwnerIsNotProjectMember:
    """Regression for the M41 r2 sweep: ``IssueDetail.delete`` used to
    gate on ``member=request.user`` (the SP's human **owner**); an SP
    whose owner is not a project member got a 403 even when the chain
    authorized the write. The M40-correct owner field is
    ``created_by_id``; that is also what the engine reads.
    """

    def _scenario(self, workspace, create_user, ownerless_project):
        sp = _make_sp(workspace, create_user, "issue-del-sp", ownerless_project)
        bot = sp.ensure_bot_user()
        issue = Issue(
            name="Ownerless Issue",
            project=ownerless_project,
            workspace=workspace,
            created_by=bot,
        )
        # ``disable_auto_set_user`` keeps ``BaseModel.save`` from nulling
        # ``created_by`` when no thread-local user is set — the bot
        # author must stick for the engine's ownership step to match.
        issue.save(disable_auto_set_user=True)
        return sp, issue

    def test_sp_can_delete_own_work_item(
        self, workspace, create_user, ownerless_project
    ):
        sp, issue = self._scenario(workspace, create_user, ownerless_project)

        response = _client(sp).delete(
            f"/api/v1/workspaces/{workspace.slug}/projects/"
            f"{ownerless_project.id}/work-items/{issue.id}/"
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT, response.data
        assert not Issue.objects.filter(pk=issue.id).exists()


def _ensure_intake_view(project, workspace):
    Intake.objects.get_or_create(
        project=project, is_default=True,
        defaults={"name": f"{project.name} Intake", "workspace": workspace},
    )


def _ensure_triage_state(project, workspace):
    triage_state = State.objects.filter(
        project=project, group=StateGroup.TRIAGE.value
    ).first()
    if triage_state:
        return triage_state
    return State.objects.create(
        name="Triage",
        group=StateGroup.TRIAGE.value,
        project=project,
        workspace=workspace,
        color="#4E5355",
        sequence=65000,
        default=False,
    )


@pytest.mark.contract
@pytest.mark.django_db
class TestSPIntakePatchOwnerNotAMember:
    """The intake PATCH used to ``ProjectMember.objects.get(member=
    request.user)`` — the SP's human owner — and 404'd on writes the authz
    chain had authorized (a member-tier SP, on a row the SP created, when
    the owner is not a project member). Mirror of the
    ``TestSPAttachmentWritesDoNotConsultTheOwnersMembership`` shape.
    """

    def _scenario(self, workspace, create_user, ownerless_project):
        sp = _make_sp(workspace, create_user, "intake-sp", ownerless_project)
        bot = sp.ensure_bot_user()
        # The intake PATCH is an ``intake:update``; the existing
        # ``_make_sp`` helper provisions only ``work_item`` / ``comment``
        # scopes, so add the scope row the engine's scope step requires.
        ServiceScope.objects.create(
            service_principal=sp,
            project=ownerless_project,
            resource_type="intake",
            action="update",
        )
        _ensure_intake_view(ownerless_project, workspace)
        triage_state = _ensure_triage_state(ownerless_project, workspace)
        intake = Intake.objects.get(project=ownerless_project, is_default=True)
        # Created via ``Issue(...).save(...)`` so the bot's id is the
        # author; ``BaseModel.save`` would otherwise null ``created_by``
        # when no thread-local user is set, and the engine's ownership
        # step then fails closed on "no recorded creator".
        issue = Issue(
            name="Intake-Ownerless Issue",
            project=ownerless_project,
            workspace=workspace,
            state=triage_state,
            created_by=bot,
        )
        issue.save(disable_auto_set_user=True)
        intake_issue = IntakeIssue(
            intake=intake,
            project=ownerless_project,
            workspace=workspace,
            issue=issue,
            source="in_app",
            created_by=bot,
        )
        intake_issue.save(disable_auto_set_user=True)
        return sp, intake_issue

    def test_sp_can_patch_its_own_intake_issue(
        self, workspace, create_user, ownerless_project
    ):
        sp, intake_issue = self._scenario(
            workspace, create_user, ownerless_project
        )

        response = _client(sp).patch(
            _intake_url(
                workspace.slug,
                ownerless_project.id,
                intake_issue.issue_id,
            ),
            data={"status": -1},  # reject
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, (
            response.status_code, response.data
        )
        intake_issue.refresh_from_db()
        assert intake_issue.status == -1


@pytest.mark.contract
@pytest.mark.django_db
class TestSPCycleTransferCreditsTheBotNotTheOwner:
    """``TransferCycleIssueAPIEndpoint.post`` used to call
    ``transfer_cycle_issues(..., user_id=self.request.user.id)``. The
    utility's only use of ``user_id`` is the activity row's
    ``actor_id``, so an SP-driven transfer was signed by the SP's human
    **owner** in the audit trail. The fix swaps it for
    ``actor_id_for_request(request)``.

    The test mocks ``issue_activity.delay`` (the task the activity row
    is dispatched through) and asserts the recorded ``actor_id`` is the
    SP's bot user — never the human owner. Fails before the fix with
    the owner's id; passes after.
    """

    def test_sp_transfer_credits_the_bot(
        self, workspace, create_user, ownerless_project
    ):
        from datetime import timedelta

        from django.utils import timezone as dj_timezone

        sp = _make_sp(
            workspace, create_user, "transfer-sp", ownerless_project
        )
        bot = sp.ensure_bot_user()
        # The cycle transfer endpoint is a POST; the engine maps POST
        # to ``CREATE``, so ``_make_sp``'s work_item/comment scopes do
        # not cover it and we need a ``cycle:create`` scope row for the
        # engine's scope step to admit the request.
        ServiceScope.objects.create(
            service_principal=sp,
            project=ownerless_project,
            resource_type="cycle",
            action="create",
        )

        # ``Cycle.save`` clears ``created_by`` when no thread-local user
        # is set (the test request has none), so build the row with
        # ``disable_auto_set_user=True`` to keep the SP's bot as the
        # creator — that is the column the ownership step reads.
        def _make_cycle(name, end_date):
            cycle = Cycle(
                name=name,
                project=ownerless_project,
                owned_by=bot,
                start_date=dj_timezone.now() - timedelta(days=30),
                end_date=end_date,
                created_by=bot,
            )
            cycle.save(disable_auto_set_user=True)
            return cycle

        old_cycle = _make_cycle(
            "Old Cycle", dj_timezone.now() - timedelta(days=1)
        )
        new_cycle = _make_cycle(
            "New Cycle", dj_timezone.now() + timedelta(days=30)
        )

        with patch(
            "plane.utils.cycle_transfer_issues.issue_activity"
        ) as mock_activity:
            response = _client(sp).post(
                _cycle_transfer_url(
                    workspace.slug,
                    ownerless_project.id,
                    old_cycle.id,
                ),
                data={"new_cycle_id": str(new_cycle.id)},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, (
            response.status_code, response.data
        )

        # The activity row's ``actor_id`` must be the SP's bot, not the
        # human owner — that is the only consumer of ``user_id`` in
        # ``transfer_cycle_issues``.
        assert mock_activity.delay.called, (
            "cycle transfer did not dispatch an activity task"
        )
        recorded_actor_id = mock_activity.delay.call_args.kwargs["actor_id"]
        assert recorded_actor_id == str(bot.id), (
            f"actor_id should be the SP's bot ({bot.id}), got "
            f"{recorded_actor_id} (the human owner would be "
            f"{create_user.id})"
        )
        assert recorded_actor_id != str(create_user.id)


@pytest.mark.contract
@pytest.mark.django_db
class TestSPCanDeleteOwnPageWithoutBeingAProjectMember:
    """``PageDetail.delete`` used to gate on ``member=request.user``
    (the SP's human **owner**) with a role=20 admin lookup. An admin-tier
    SP whose owner was not a project member got a 403 from the body guard
    even when the engine authorized the write. Mirrors the
    ``TestSPAttachmentWritesDoNotConsultTheOwnersMembership`` shape.
    """

    def test_admin_tier_sp_can_delete_a_page(
        self, workspace, create_user, ownerless_project
    ):
        from django.utils import timezone as dj_timezone

        from plane.db.models import Page, ProjectPage

        admin_sp = _make_sp(
            workspace, create_user, "page-del-admin-sp", ownerless_project
        )
        admin_sp.ensure_bot_user()
        # ``_make_sp`` only provisions ``work_item`` / ``comment`` scopes;
        # page DELETE is a ``page:delete`` action in the engine, so the
        # scope row is needed to even reach ownership.
        ServiceScope.objects.create(
            service_principal=admin_sp,
            project=ownerless_project,
            resource_type="page",
            action="delete",
        )
        # Admin tier: engine authorizes DELETE on any row whose project
        # the SP is granted into, regardless of created_by.
        ProjectGrant.objects.filter(
            service_principal=admin_sp, project=ownerless_project
        ).update(role_cap=20)

        # A second SP "owns" the page (its `created_by_id` is the bot;
        # `owned_by` is whatever the v1 stamp picked — for this test we
        # set it directly to the *first* SP's owner so the old guard
        # would have tripped).
        owner_sp = _make_sp(
            workspace, create_user, "page-del-owner-sp", ownerless_project
        )
        owner_bot = owner_sp.ensure_bot_user()
        page = Page(
            name="Archived Page",
            workspace=workspace,
            owned_by=create_user,
            access=0,
            archived_at=dj_timezone.now(),
            created_by=owner_bot,
        )
        page.save(disable_auto_set_user=True)
        ProjectPage.objects.create(
            project=ownerless_project, workspace=workspace, page=page
        )

        response = _client(admin_sp).delete(
            f"/api/v1/workspaces/{workspace.slug}/projects/"
            f"{ownerless_project.id}/pages/{page.id}/"
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT, (
            response.status_code, response.data
        )
