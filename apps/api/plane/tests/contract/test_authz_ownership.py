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
* ownership reads the ``created_by`` *attribution* column for every resource
  type — including ``Page``, whose ``owned_by`` column is the human-facing
  page owner, not the attribution (the v1 write path stamps ``owned_by`` from
  ``request.user``, which stays the SP's human owner);
* the ``_resolve_project_id`` fix: a resource whose *own* ``project`` is NULL
  (a workspace-wide ``Label``) must deny with
  ``DENY_WORKSPACE_LEVEL_WRITE``, not ``DENY_GRANT_MISS``;
* the capability probe (``effective_actions``) reports scope → grant →
  role_cap and skips the ownership step, while a real write by the same
  principal is still denied on a foreign row.
"""

from __future__ import annotations

import pytest
from rest_framework import status
from rest_framework.test import APIClient

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
    effective_actions,
)
from plane.db.models import (
    APIToken,
    Issue,
    IssueComment,
    Label,
    Page,
    Project,
    ProjectMember,
    User,
)
from plane.service_principals.constants import PrincipalType
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


def _page(workspace, created_by, owned_by, name="Ownership Page"):
    """Build a page row.

    ``created_by`` is the attribution column the ownership rule reads.
    ``owned_by`` is the product's human-facing page owner — the v1 create
    view stamps it from ``request.user``, which for a ``plane_svc_`` token
    stays the SP's human owner. Both are set explicitly here so a test can
    hold either one fixed while varying the other.
    """
    return _create(
        Page,
        name=name,
        workspace=workspace,
        owned_by=owned_by,
        created_by=created_by,
        description_html="<p>ownership page</p>",
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
        """Historical row with no recorded owner — must not be treated as
        owned by anyone (least of all "matches another NULL")."""
        issue = _issue(project, workspace, None)
        assert issue.created_by_id is None
        d = _decide(
            sp, Action.UPDATE, ResourceType.WORK_ITEM, issue, project, workspace
        )
        assert not d.allowed
        assert d.reason == DENY_OWNER_MISMATCH
        assert "no recorded owner" in d.detail, d.detail

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


# --------------------------------------------------------------------------- #
# Page ownership — created_by is the attribution column, not owned_by           #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestPageOwnership:
    """Pages resolve ownership from ``created_by``, like every other type.

    ``Page.owned_by`` is the product's **human-facing** page owner: the v1
    create view stamps it from ``request.user`` (``api/views/page.py`` ->
    ``PageCreateSerializer``), and for a ``plane_svc_`` token the v1 auth
    layer keeps ``request.user`` as the SP's human **owner** on purpose. So a
    page created by an SP is ``owned_by`` = the human owner and ``created_by``
    = the SP's bot user (``BaseModel.save`` -> ``current_sp_actor``). Reading
    ``owned_by`` would deny an SP the very pages it just created — the p1 in
    M40's review (r1) — which is why ``_OWNER_ATTNAME`` is ``created_by_id``.
    """

    def test_member_updates_page_created_by_bot_allowed(
        self, sp, bot_user, project, workspace, create_user
    ):
        # The exact shape the v1 POST produces: owned_by = the human owner,
        # created_by = the bot. The write must be allowed.
        page = _page(workspace, created_by=bot_user, owned_by=create_user)
        d = _decide(
            sp, Action.UPDATE, ResourceType.PAGE, page, project, workspace
        )
        assert d.allowed, f"page created by the bot should allow; got {d}"

    def test_member_deletes_page_created_by_bot_allowed(
        self, sp, bot_user, project, workspace, create_user
    ):
        page = _page(workspace, created_by=bot_user, owned_by=create_user)
        d = _decide(
            sp, Action.DELETE, ResourceType.PAGE, page, project, workspace
        )
        assert d.allowed, f"page created by the bot should allow; got {d}"

    def test_member_updates_page_created_by_someone_else_denied(
        self, sp, bot_user, project, workspace, create_user, other_user
    ):
        page = _page(workspace, created_by=other_user, owned_by=create_user)
        d = _decide(
            sp, Action.UPDATE, ResourceType.PAGE, page, project, workspace
        )
        assert not d.allowed
        assert d.reason == DENY_OWNER_MISMATCH

    def test_page_created_by_other_denied_even_when_owned_by_bot(
        self, sp, bot_user, project, workspace, other_user
    ):
        """``owned_by`` is NOT what decides: a page the bot owns but someone
        else created is not the bot's to write."""
        page = _page(workspace, created_by=other_user, owned_by=bot_user)
        assert page.owned_by_id == bot_user.id
        d = _decide(
            sp, Action.UPDATE, ResourceType.PAGE, page, project, workspace
        )
        assert not d.allowed
        assert d.reason == DENY_OWNER_MISMATCH

    def test_page_created_by_bot_allowed_even_when_owned_by_other(
        self, sp, bot_user, project, workspace, other_user
    ):
        """The mirror of the case above: the bot's authorship in
        ``created_by`` is what allows the write, regardless of ``owned_by``."""
        page = _page(workspace, created_by=bot_user, owned_by=other_user)
        assert page.owned_by_id == other_user.id
        d = _decide(
            sp, Action.UPDATE, ResourceType.PAGE, page, project, workspace
        )
        assert d.allowed, f"created_by is authoritative; got {d}"

    def test_page_created_by_other_denied_for_delete(
        self, sp, bot_user, project, workspace, create_user, other_user
    ):
        page = _page(workspace, created_by=other_user, owned_by=create_user)
        d = _decide(
            sp, Action.DELETE, ResourceType.PAGE, page, project, workspace
        )
        assert not d.allowed
        assert d.reason == DENY_OWNER_MISMATCH

    def test_v1_write_path_stamps_the_bot_in_created_by(
        self, sp, bot_user, project, workspace, create_user
    ):
        """Drive the real v1 write path so the asserted columns are the ones
        the product actually stamps — not a hand-built row."""
        _scope(sp, ResourceType.PAGE, Action.CREATE, project=project)
        _scope(sp, ResourceType.PAGE, Action.UPDATE, project=project)
        _scope(sp, ResourceType.PAGE, Action.DELETE, project=project)
        _grant(sp, project, role_cap=15)
        APIToken.objects.create(
            label="svc:own-page",
            token="plane_svc_token_own-page",
            user=create_user,
            principal_type=PrincipalType.SERVICE,
            service_principal=sp,
            workspace=workspace,
        )

        client = APIClient()
        client.credentials(HTTP_X_API_KEY="plane_svc_token_own-page")
        response = client.post(
            f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/pages/",
            data={"name": "SP Page", "description_html": "<p>made by the bot</p>"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data

        page = Page.objects.get(name="SP Page")
        # The row the product produces: authorship in created_by (the bot),
        # human-facing ownership in owned_by (the SP's owner).
        assert page.created_by_id == bot_user.id
        assert page.owned_by_id == create_user.id

        ctx = AuthzContext(
            workspace_slug=workspace.slug, project_id=str(project.id)
        )
        principal = ServicePrincipal_(service_principal=sp)
        for action in (Action.UPDATE, Action.DELETE):
            d = authorize(
                principal, action, ResourceType.PAGE, resource=page, ctx=ctx
            )
            assert d.allowed, (
                f"{action} on the page this SP just created must be allowed; "
                f"got {d}"
            )


# --------------------------------------------------------------------------- #
# Capability probe vs enforcement (effective_actions)                          #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestEffectiveActionsIsACapabilityProbe:
    """``effective_actions`` has no row to judge, so it reports scope →
    grant → role_cap and skips the ownership step; the *same* principal is
    still denied when it writes a foreign row for real."""

    def test_probe_reports_update_delete_capability_for_member_tier(
        self, sp, bot_user, project, workspace
    ):
        _scope(sp, ResourceType.WORK_ITEM, Action.UPDATE, project=project)
        _scope(sp, ResourceType.WORK_ITEM, Action.DELETE, project=project)
        _grant(sp, project, role_cap=15)

        perms = effective_actions(
            ServicePrincipal_(service_principal=sp),
            ResourceType.WORK_ITEM,
            project=project,
            workspace=workspace,
        )
        by_action = {p.action: p for p in perms}
        assert by_action[Action.UPDATE].allowed is True, by_action[Action.UPDATE]
        assert by_action[Action.UPDATE].reason == ALLOWED
        assert by_action[Action.DELETE].allowed is True, by_action[Action.DELETE]
        assert by_action[Action.DELETE].reason == ALLOWED

    def test_probe_is_permissive_but_real_write_on_foreign_row_denies(
        self, sp, bot_user, project, workspace, other_user
    ):
        _scope(sp, ResourceType.WORK_ITEM, Action.UPDATE, project=project)
        _grant(sp, project, role_cap=15)

        perms = effective_actions(
            ServicePrincipal_(service_principal=sp),
            ResourceType.WORK_ITEM,
            project=project,
            workspace=workspace,
        )
        update = {p.action: p for p in perms}[Action.UPDATE]
        assert update.allowed is True

        foreign = _issue(project, workspace, other_user)
        d = authorize(
            ServicePrincipal_(service_principal=sp),
            Action.UPDATE,
            ResourceType.WORK_ITEM,
            resource=foreign,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(project.id),
            ),
        )
        assert not d.allowed
        assert d.reason == DENY_OWNER_MISMATCH
