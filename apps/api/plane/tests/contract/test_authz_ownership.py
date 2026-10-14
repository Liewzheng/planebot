# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: per-resource ownership in ``plane.core.authz`` (M40).

The user-facing rule this mission lands: a member-tier service principal may
read everything in a project and comment on anyone's work item, but may only
``update``/``delete`` rows **it created**; an ``ADMIN``-tier grant
(``role_cap >= 20``) is unrestricted; and a row whose creator is unknown
(historical data, or a missing ``created_by``) is denied — fail-closed.

The engine already carried the ``resource`` argument ("reserved for future
per-row decisions"); M40 makes it live via **step 3.5** in
:func:`plane.core.authz.authorize`, which compares the row's ``created_by``
with the SP's shadow bot user (``ServicePrincipal.bot_user``, M39).

What this file pins:

* Member-tier ``update``/``delete`` on a row the SP's bot created → ALLOW;
* Member-tier ``update``/``delete`` on someone else's row → DENY
  (``DENY_OWNER_MISMATCH``);
* Admin-tier (``role_cap = 20``) writes on someone else's row → ALLOW;
* unknown ownership — no creator, no bot user, or no resource row at all →
  DENY (``None`` never matches ``None``);
* ``read``/``list``/``create`` are untouched by the ownership step;
* the ``_resolve_project_id`` fix: a resource whose *own* ``project`` is NULL
  (a workspace-wide ``Label``/``Estimate``) must deny with
  ``DENY_WORKSPACE_LEVEL_WRITE``, not ``DENY_GRANT_MISS``.
"""

from __future__ import annotations

import pytest

from plane.core.authz import (
    ALLOWED,
    DENY_GRANT_MISS,
    DENY_OWNER_MISMATCH,
    DENY_WORKSPACE_LEVEL_WRITE,
    AuthzContext,
    Action,
    ResourceType,
    ServicePrincipal_,
    authorize,
)
from plane.db.models import (
    Issue,
    IssueComment,
    Label,
    Project,
    ProjectMember,
    User,
)
from plane.service_principals.models import (
    ProjectGrant,
    ServicePrincipal,
    ServiceScope,
)


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="Ownership Project",
        identifier="OWN",
        workspace=workspace,
        created_by=create_user,
        network=2,
    )
    ProjectMember.objects.create(
        project=project, member=create_user, role=20, is_active=True
    )
    return project


@pytest.fixture
def sp(db, workspace, create_user):
    """An SP with **no** bot user yet — ``bot_user`` is NULL until the
    ``bot_user`` fixture (or the mission under test) provisions it."""
    return ServicePrincipal.objects.create(
        workspace=workspace, owner=create_user, name="owner-bot"
    )


@pytest.fixture
def bot_user(sp):
    return sp.ensure_bot_user()


@pytest.fixture
def other_user(db):
    """A human who owns nothing of the SP's — used to author "foreign" rows."""
    user = User.objects.create(
        email="other@plane.so",
        username="other-owner",
        first_name="Other",
        last_name="Owner",
    )
    user.set_password("password")
    user.save()
    return user


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _scope(sp, resource_type, action, project=None):
    return ServiceScope.objects.create(
        service_principal=sp,
        project=project,
        resource_type=resource_type,
        action=action,
    )


def _grant(sp, project, role_cap=15, is_active=True):
    return ProjectGrant.objects.create(
        service_principal=sp,
        project=project,
        role_cap=role_cap,
        is_active=is_active,
    )


def _create(model, **kwargs):
    """Create a row with an explicit ``created_by``.

    ``BaseModel.save`` credits every write to the in-flight request user, and
    overwrites ``created_by`` with ``None`` when there is none — which is
    always the case under pytest. A test that needs a *specific* author must
    therefore opt out of auto-attribution. ``refresh_from_db`` afterwards
    re-reads the persisted row, because some models (``IssueComment``) run a
    follow-up save that scrubs ``created_by`` from the in-memory instance
    without writing that change to the row.
    """
    obj = model(**kwargs)
    obj.save(disable_auto_set_user=True)
    obj.refresh_from_db()
    return obj


def _issue(project, workspace, created_by):
    return _create(
        Issue,
        name="Ownership Issue",
        project=project,
        workspace=workspace,
        created_by=created_by,
    )


def _comment(project, workspace, issue, created_by):
    return _create(
        IssueComment,
        issue=issue,
        project=project,
        workspace=workspace,
        comment_html="<p>hello</p>",
        created_by=created_by,
        actor=created_by,
    )


def _label(project, workspace, created_by, name="Ownership Label"):
    return _create(
        Label,
        name=name,
        project=project,
        workspace=workspace,
        created_by=created_by,
    )


def _decide(sp, action, resource_type, resource, project, workspace, *, cap=15):
    """Authorize a single SP call with one scope row and one grant."""
    _scope(sp, resource_type, action, project=project)
    _grant(sp, project, role_cap=cap)
    return authorize(
        ServicePrincipal_(service_principal=sp),
        action,
        resource_type,
        resource=resource,
        ctx=AuthzContext(
            workspace_slug=workspace.slug,
            project_id=str(project.id),
        ),
    )


# --------------------------------------------------------------------------- #
# ① Member-tier writes on rows it created                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestMemberWritesOwnRowAllowed:
    def test_member_updates_own_row_allowed(
        self, sp, bot_user, project, workspace
    ):
        issue = _issue(project, workspace, bot_user)
        d = _decide(
            sp, Action.UPDATE, ResourceType.WORK_ITEM, issue, project, workspace
        )
        assert d.allowed, f"member should update its own row; got {d}"
        assert d.reason == ALLOWED

    def test_member_deletes_own_row_allowed(
        self, sp, bot_user, project, workspace
    ):
        issue = _issue(project, workspace, bot_user)
        d = _decide(
            sp, Action.DELETE, ResourceType.WORK_ITEM, issue, project, workspace
        )
        assert d.allowed, f"member should delete its own row; got {d}"

    def test_member_updates_own_comment_allowed(
        self, sp, bot_user, project, workspace
    ):
        issue = _issue(project, workspace, bot_user)
        comment = _comment(project, workspace, issue, bot_user)
        d = _decide(
            sp, Action.UPDATE, ResourceType.COMMENT, comment, project, workspace
        )
        assert d.allowed, f"member should update its own comment; got {d}"


# --------------------------------------------------------------------------- #
# ② Member-tier writes on someone else's row                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestMemberWritesOtherRowDenied:
    def test_member_updates_other_row_denied(
        self, sp, bot_user, project, workspace, other_user
    ):
        issue = _issue(project, workspace, other_user)
        d = _decide(
            sp, Action.UPDATE, ResourceType.WORK_ITEM, issue, project, workspace
        )
        assert not d.allowed
        assert d.reason == DENY_OWNER_MISMATCH
        assert "another actor" in d.detail, d.detail

    def test_member_deletes_other_row_denied(
        self, sp, bot_user, project, workspace, other_user
    ):
        issue = _issue(project, workspace, other_user)
        d = _decide(
            sp, Action.DELETE, ResourceType.WORK_ITEM, issue, project, workspace
        )
        assert not d.allowed
        assert d.reason == DENY_OWNER_MISMATCH
        assert "another actor" in d.detail, d.detail

    def test_member_deletes_other_comment_denied(
        self, sp, bot_user, project, workspace, other_user
    ):
        issue = _issue(project, workspace, other_user)
        comment = _comment(project, workspace, issue, other_user)
        d = _decide(
            sp, Action.DELETE, ResourceType.COMMENT, comment, project, workspace
        )
        assert not d.allowed
        assert d.reason == DENY_OWNER_MISMATCH

    def test_ownership_denial_appends_remedy(self, sp, bot_user, project, workspace, other_user):
        """The 403 body must join the existing remedy system (M27/M30)."""
        issue = _issue(project, workspace, other_user)
        d = _decide(
            sp, Action.UPDATE, ResourceType.WORK_ITEM, issue, project, workspace
        )
        assert d.reason == DENY_OWNER_MISMATCH
        assert "workspace admin" in d.detail
        assert (
            "Workspace settings -> Developer -> Service principals "
            "-> Manage grants" in d.detail
        ), f"remedy location drifted: {d.detail!r}"


# --------------------------------------------------------------------------- #
# ③ Admin-tier writes are unrestricted                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestAdminTierUnrestricted:
    def test_admin_updates_other_row_allowed(
        self, sp, bot_user, project, workspace, other_user
    ):
        issue = _issue(project, workspace, other_user)
        d = _decide(
            sp,
            Action.UPDATE,
            ResourceType.WORK_ITEM,
            issue,
            project,
            workspace,
            cap=20,
        )
        assert d.allowed, f"admin grant must not be ownership-scoped; got {d}"
        assert d.effective_role == 20

    def test_admin_deletes_other_row_allowed(
        self, sp, bot_user, project, workspace, other_user
    ):
        issue = _issue(project, workspace, other_user)
        d = _decide(
            sp,
            Action.DELETE,
            ResourceType.WORK_ITEM,
            issue,
            project,
            workspace,
            cap=20,
        )
        assert d.allowed

    def test_admin_write_without_resource_allowed(
        self, sp, bot_user, project, workspace
    ):
        """An admin grant is exempt from the ownership step, so a missing
        resource row does not fail it closed (only member-tier grants do)."""
        d = _decide(
            sp,
            Action.DELETE,
            ResourceType.WORK_ITEM,
            None,
            project,
            workspace,
            cap=20,
        )
        assert d.allowed


# --------------------------------------------------------------------------- #
# ④ Unknown ownership fails closed                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestUnknownOwnershipFailsClosed:
    def test_member_update_row_without_creator_denied(
        self, sp, bot_user, project, workspace
    ):
        """Historical row with no recorded creator — must not be treated as
        owned by anyone (least of all "matches another NULL")."""
        issue = _issue(project, workspace, None)
        assert issue.created_by_id is None
        d = _decide(
            sp, Action.UPDATE, ResourceType.WORK_ITEM, issue, project, workspace
        )
        assert not d.allowed
        assert d.reason == DENY_OWNER_MISMATCH
        assert "no recorded creator" in d.detail, d.detail

    def test_member_delete_row_without_creator_denied(
        self, sp, bot_user, project, workspace
    ):
        issue = _issue(project, workspace, None)
        d = _decide(
            sp, Action.DELETE, ResourceType.WORK_ITEM, issue, project, workspace
        )
        assert not d.allowed
        assert d.reason == DENY_OWNER_MISMATCH

    def test_member_update_without_resource_denied(
        self, sp, bot_user, project, workspace
    ):
        """``resource=None`` on a member-tier update is fail-closed, not a
        skip: ownership cannot be verified without the row."""
        d = _decide(
            sp, Action.UPDATE, ResourceType.WORK_ITEM, None, project, workspace
        )
        assert not d.allowed
        assert d.reason == DENY_OWNER_MISMATCH
        assert "did not supply the resource row" in d.detail, d.detail

    def test_member_delete_without_resource_denied(
        self, sp, bot_user, project, workspace
    ):
        d = _decide(
            sp, Action.DELETE, ResourceType.WORK_ITEM, None, project, workspace
        )
        assert not d.allowed
        assert d.reason == DENY_OWNER_MISMATCH

    def test_member_write_when_bot_user_is_null_denied(
        self, sp, project, workspace
    ):
        """M39's ``bot_user`` is SET_NULL and is not guaranteed present; a
        NULL must deny rather than match another row's NULL creator."""
        assert sp.bot_user_id is None
        issue = _issue(project, workspace, None)
        d = _decide(
            sp, Action.UPDATE, ResourceType.WORK_ITEM, issue, project, workspace
        )
        assert not d.allowed
        assert d.reason == DENY_OWNER_MISMATCH
        assert "no attribution bot user" in d.detail, d.detail

    def test_null_bot_user_never_matches_a_real_creator(
        self, sp, project, workspace, other_user
    ):
        assert sp.bot_user_id is None
        issue = _issue(project, workspace, other_user)
        d = _decide(
            sp, Action.UPDATE, ResourceType.WORK_ITEM, issue, project, workspace
        )
        assert not d.allowed
        assert d.reason == DENY_OWNER_MISMATCH


# --------------------------------------------------------------------------- #
# ⑤ read / list / create are untouched                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestReadListCreateUnaffected:
    def test_read_other_row_allowed(
        self, sp, bot_user, project, workspace, other_user
    ):
        issue = _issue(project, workspace, other_user)
        d = _decide(
            sp,
            Action.READ,
            ResourceType.WORK_ITEM,
            issue,
            project,
            workspace,
            cap=5,
        )
        assert d.allowed, f"read is not ownership-scoped; got {d}"

    def test_list_other_row_allowed(
        self, sp, bot_user, project, workspace, other_user
    ):
        issue = _issue(project, workspace, other_user)
        d = _decide(
            sp,
            Action.LIST,
            ResourceType.WORK_ITEM,
            issue,
            project,
            workspace,
            cap=5,
        )
        assert d.allowed

    def test_read_row_without_creator_allowed(
        self, sp, bot_user, project, workspace
    ):
        issue = _issue(project, workspace, None)
        d = _decide(
            sp,
            Action.READ,
            ResourceType.WORK_ITEM,
            issue,
            project,
            workspace,
            cap=5,
        )
        assert d.allowed

    def test_create_without_resource_allowed(
        self, sp, bot_user, project, workspace
    ):
        d = _decide(
            sp, Action.CREATE, ResourceType.WORK_ITEM, None, project, workspace
        )
        assert d.allowed, f"create has no existing row to own; got {d}"

    def test_create_ignores_other_actors_row(
        self, sp, bot_user, project, workspace, other_user
    ):
        """A create whose payload row carries another user's authorship is
        still evaluated on the scope/grant/role chain only."""
        issue = _issue(project, workspace, other_user)
        d = _decide(
            sp, Action.CREATE, ResourceType.WORK_ITEM, issue, project, workspace
        )
        assert d.allowed


# --------------------------------------------------------------------------- #
# _resolve_project_id — a resource whose own project is NULL                    #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestWorkspaceWideResourceProjectResolution:
    """A resource carrying ``workspace_id`` and a NULL ``project`` is not a
    Project. ``Label.project`` is nullable (workspace-wide labels exist), so
    a label row is the reachable case. Before the fix
    ``_resolve_project_id`` returned the row's own id as the project id, so
    the write reached the grant step and denied ``DENY_GRANT_MISS``; it must
    instead trip the workspace-level write guard
    (``DENY_WORKSPACE_LEVEL_WRITE``)."""

    def test_workspace_wide_label_write_denies_workspace_level_write(
        self, sp, bot_user, workspace
    ):
        label = _label(None, workspace, bot_user, name="Workspace Label")
        # A workspace-wide scope row is what would have let the buggy path
        # reach the grant step and deny DENY_GRANT_MISS.
        _scope(sp, ResourceType.LABEL, Action.UPDATE, project=None)

        d = authorize(
            ServicePrincipal_(service_principal=sp),
            Action.UPDATE,
            ResourceType.LABEL,
            resource=label,
            ctx=AuthzContext(workspace_slug=workspace.slug),
        )
        assert not d.allowed
        assert d.reason == DENY_WORKSPACE_LEVEL_WRITE
        assert d.reason != DENY_GRANT_MISS
        assert d.detail.startswith(
            "Writes on workspace-level resource 'label' are not permitted;"
        ), f"workspace-level detail drifted: {d.detail!r}"

    def test_workspace_wide_label_write_ignores_ctx_project(
        self, sp, bot_user, project, workspace
    ):
        """Even when the routing context names a project, a resource whose
        own ``project`` is NULL is a workspace-level row — ``ctx`` must not
        rescue it back onto the project-scoped path."""
        label = _label(None, workspace, bot_user, name="Workspace Label")
        _scope(sp, ResourceType.LABEL, Action.UPDATE, project=None)

        d = authorize(
            ServicePrincipal_(service_principal=sp),
            Action.UPDATE,
            ResourceType.LABEL,
            resource=label,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(project.id),
            ),
        )
        assert not d.allowed
        assert d.reason == DENY_WORKSPACE_LEVEL_WRITE
        assert d.reason != DENY_GRANT_MISS

    def test_row_project_wins_over_ctx_project(
        self, sp, bot_user, project, workspace
    ):
        """A project row's own ``project_id`` decides the project, ahead of
        ``ctx.project_id``."""
        issue = _issue(project, workspace, bot_user)
        _scope(sp, ResourceType.WORK_ITEM, Action.UPDATE, project=project)
        _grant(sp, project, role_cap=15)

        d = authorize(
            ServicePrincipal_(service_principal=sp),
            Action.UPDATE,
            ResourceType.WORK_ITEM,
            resource=issue,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id="00000000-0000-0000-0000-000000000000",
            ),
        )
        assert d.allowed, f"row's own project must win over ctx; got {d}"

    def test_workspace_wide_label_read_allowed(
        self, sp, bot_user, workspace
    ):
        """The fix must not turn a legitimate workspace-wide read into a
        deny: with a workspace-wide read scope, the label is readable."""
        label = _label(None, workspace, bot_user, name="Workspace Label")
        _scope(sp, ResourceType.LABEL, Action.READ, project=None)

        d = authorize(
            ServicePrincipal_(service_principal=sp),
            Action.READ,
            ResourceType.LABEL,
            resource=label,
            ctx=AuthzContext(workspace_slug=workspace.slug),
        )
        assert d.allowed, f"workspace-wide label read should allow; got {d}"

    def test_project_scoped_label_write_uses_own_project(
        self, sp, bot_user, project, workspace, other_user
    ):
        """A project-scoped label still resolves its own project and is
        judged against that project's grant + ownership."""
        label = _label(project, workspace, bot_user, name="Project Label")
        _scope(sp, ResourceType.LABEL, Action.UPDATE, project=project)
        _grant(sp, project, role_cap=15)

        d = authorize(
            ServicePrincipal_(service_principal=sp),
            Action.UPDATE,
            ResourceType.LABEL,
            resource=label,
            ctx=AuthzContext(workspace_slug=workspace.slug),
        )
        assert d.allowed, f"project label update by its creator; got {d}"

        # And someone else's project label is denied by the ownership step.
        other_label = _label(project, workspace, other_user, name="Other Label")
        d2 = authorize(
            ServicePrincipal_(service_principal=sp),
            Action.UPDATE,
            ResourceType.LABEL,
            resource=other_label,
            ctx=AuthzContext(workspace_slug=workspace.slug),
        )
        assert not d2.allowed
        assert d2.reason == DENY_OWNER_MISMATCH
