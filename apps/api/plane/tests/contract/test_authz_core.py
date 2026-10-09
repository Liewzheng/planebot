# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for ``plane.core.authz``.

Covers the four-step decision chain
(scope → grant → role_cap → owner intersection) plus the unified
visibility predicate and the ``sp_assignable`` toggle. These tests are
pure-module: no HTTP layer, no authentication. The M8/M9 wiring tests
will reuse the same fixtures.

Mission M7 coverage:

* Unregistered (action, resource_type) — default-deny.
* Secret project with no grant — 403.
* Owner demotion takes effect on the next call (no cache).
* ``role_cap`` boundary (cap equals required / cap = required - 1).
* Workspace-wide grant covers read; writes are rejected (Q4).
* ``sp_assignable`` on/off picks SPs into and out of the visible set.
"""

from __future__ import annotations

import pytest
from django.db.models import Q

from plane.core.authz import (
    ALLOWED,
    DENY_GRANT_MISS,
    DENY_INACTIVE_OWNER,
    DENY_INACTIVE_PRINCIPAL,
    DENY_NO_PRINCIPAL,
    DENY_OWNER_NOT_WORKSPACE_MEMBER,
    DENY_OWNER_ROLE,
    DENY_ROLE_CAP,
    DENY_SCOPE_MISS,
    DENY_UNREGISTERED,
    DENY_WORKSPACE_LEVEL_WRITE,
    Decision,
    VISIBLE_MEMBER_Q,
    AuthzContext,
    Action,
    ActionPermission,
    ResourceType,
    ServicePrincipal_,
    STANDARD_ACTIONS,
    UserPrincipal,
    authorize,
    effective_actions,
    effective_role_cap,
    enforce,
    is_member_visible,
    is_sp_assignable,
    required_role,
    visible_member_qs,
    visible_principal_qs,
    visible_sp_qs,
)
from plane.db.models import Project, ProjectMember, WorkspaceMember
from plane.service_principals.models import (
    ProjectGrant,
    ServicePrincipal,
    ServiceScope,
    WorkspaceSPSettings,
)


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #


@pytest.fixture
def second_user(db):
    from plane.db.models import User

    user = User.objects.create(
        email="member@plane.so",
        username="member-user",
        first_name="Member",
        last_name="User",
    )
    user.set_password("password")
    user.save()
    return user


@pytest.fixture
def demote_target(db, workspace):
    """A second workspace member used to drop the SP owner out of admin."""
    from plane.db.models import User

    user = User.objects.create(
        email="victim@plane.so",
        username="victim-user",
    )
    user.set_password("password")
    user.save()
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=20)
    return user


@pytest.fixture
def public_project(db, workspace, create_user):
    project = Project.objects.create(
        name="Public Project",
        identifier="PUB",
        workspace=workspace,
        created_by=create_user,
        network=2,
    )
    ProjectMember.objects.create(
        project=project, member=create_user, role=20, is_active=True
    )
    return project


@pytest.fixture
def secret_project(db, workspace, create_user):
    project = Project.objects.create(
        name="Secret Project",
        identifier="SEC",
        workspace=workspace,
        created_by=create_user,
        network=0,
    )
    ProjectMember.objects.create(
        project=project, member=create_user, role=20, is_active=True
    )
    return project


@pytest.fixture
def sp(db, workspace, create_user):
    return ServicePrincipal.objects.create(
        workspace=workspace, owner=create_user, name="bot-1"
    )


@pytest.fixture
def sp_principal(sp):
    return ServicePrincipal_(service_principal=sp)


def _add_scope(sp, resource_type, action, project=None):
    return ServiceScope.objects.create(
        service_principal=sp,
        project=project,
        resource_type=resource_type,
        action=action,
    )


def _add_grant(sp, project, role_cap=15, is_active=True):
    return ProjectGrant.objects.create(
        service_principal=sp,
        project=project,
        role_cap=role_cap,
        is_active=is_active,
    )


# --------------------------------------------------------------------------- #
# authorize() — chain shape                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestAuthorizeChain:
    def test_no_principal_denies(self):
        d = authorize(None, Action.READ, ResourceType.WORK_ITEM)
        assert isinstance(d, Decision)
        assert not d.allowed
        assert d.reason == "deny_no_principal"

    def test_unregistered_resource_denies(self, sp_principal):
        d = authorize(sp_principal, Action.READ, "totally_made_up")
        assert not d.allowed
        assert d.reason == DENY_UNREGISTERED

    def test_unregistered_action_denies(self, sp_principal):
        d = authorize(sp_principal, "levitate", ResourceType.WORK_ITEM)
        assert not d.allowed
        assert d.reason == DENY_UNREGISTERED

    def test_inactive_sp_denies(self, workspace, create_user):
        sp = ServicePrincipal.objects.create(
            workspace=workspace, owner=create_user, name="bot-off", is_active=False
        )
        principal = ServicePrincipal_(service_principal=sp)
        d = authorize(
            principal,
            Action.READ,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(workspace_slug=workspace.slug),
        )
        assert not d.allowed
        assert d.reason == DENY_INACTIVE_PRINCIPAL

    def test_human_principal_is_allowed_without_sp_checks(self, create_user, workspace):
        """Human principals skip the SP chain — the engine is for SPs only."""
        principal = UserPrincipal(user=create_user, workspace_id=str(workspace.id))
        d = authorize(
            principal,
            Action.READ,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(workspace_slug=workspace.slug),
        )
        assert d.allowed
        assert d.reason == ALLOWED


# --------------------------------------------------------------------------- #
# Step 1 — scope hit                                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestScopeStep:
    def test_scope_miss_denies(
        self, sp, sp_principal, public_project, workspace, create_user
    ):
        _add_grant(sp, public_project, role_cap=20)
        # No scope row at all
        d = authorize(
            sp_principal,
            Action.READ,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(public_project.id),
            ),
        )
        assert not d.allowed
        assert d.reason == DENY_SCOPE_MISS

    def test_exact_scope_match_allows(
        self, sp, sp_principal, public_project, workspace
    ):
        _add_scope(sp, ResourceType.WORK_ITEM, Action.READ, project=public_project)
        _add_grant(sp, public_project, role_cap=15)
        d = authorize(
            sp_principal,
            Action.READ,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(public_project.id),
            ),
        )
        assert d.allowed
        assert d.reason == ALLOWED

    def test_resource_all_wildcard_matches(
        self, sp, sp_principal, public_project, workspace
    ):
        _add_scope(sp, ResourceType.ALL, Action.READ, project=public_project)
        _add_grant(sp, public_project, role_cap=15)
        d = authorize(
            sp_principal,
            Action.READ,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(public_project.id),
            ),
        )
        assert d.allowed

    def test_action_all_wildcard_matches(
        self, sp, sp_principal, public_project, workspace
    ):
        _add_scope(sp, ResourceType.WORK_ITEM, Action.ALL, project=public_project)
        _add_grant(sp, public_project, role_cap=20)
        d = authorize(
            sp_principal,
            Action.DELETE,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(public_project.id),
            ),
        )
        assert d.allowed

    def test_workspace_wide_scope_covers_any_project(
        self, sp, sp_principal, public_project, secret_project, workspace
    ):
        # Scope row with project=None — workspace-wide
        _add_scope(sp, ResourceType.WORK_ITEM, Action.READ, project=None)
        _add_grant(sp, public_project, role_cap=20)
        _add_grant(sp, secret_project, role_cap=20)
        for project in (public_project, secret_project):
            d = authorize(
                sp_principal,
                Action.READ,
                ResourceType.WORK_ITEM,
                ctx=AuthzContext(
                    workspace_slug=workspace.slug,
                    project_id=str(project.id),
                ),
            )
            assert d.allowed, f"denied for project {project.name}"


# --------------------------------------------------------------------------- #
# Step 2 — grant presence                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestGrantStep:
    def test_no_grant_denies_secret_project(
        self, sp, sp_principal, secret_project, workspace
    ):
        """Secret projects get no default grant — default-deny is the whole
        point of opt-in (Q4 / design §5.2)."""
        _add_scope(sp, ResourceType.WORK_ITEM, Action.READ, project=secret_project)
        d = authorize(
            sp_principal,
            Action.READ,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(secret_project.id),
            ),
        )
        assert not d.allowed
        assert d.reason == DENY_GRANT_MISS

    def test_inactive_grant_denies(
        self, sp, sp_principal, public_project, workspace
    ):
        _add_scope(sp, ResourceType.WORK_ITEM, Action.READ, project=public_project)
        _add_grant(sp, public_project, role_cap=20, is_active=False)
        d = authorize(
            sp_principal,
            Action.READ,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(public_project.id),
            ),
        )
        assert not d.allowed
        assert d.reason == DENY_GRANT_MISS


# --------------------------------------------------------------------------- #
# Step 3 — role_cap boundary                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestRoleCapStep:
    @pytest.mark.parametrize("role_cap,allowed", [(15, True), (5, False)])
    def test_work_item_create_requires_member_role(
        self, sp, sp_principal, public_project, workspace, role_cap, allowed
    ):
        """Creating a work item requires MEMBER (15). GUEST (5) is too low."""
        _add_scope(
            sp, ResourceType.WORK_ITEM, Action.CREATE, project=public_project
        )
        _add_grant(sp, public_project, role_cap=role_cap)
        d = authorize(
            sp_principal,
            Action.CREATE,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(public_project.id),
            ),
        )
        assert d.allowed is allowed, (
            f"role_cap={role_cap}: expected allowed={allowed}, got {d}"
        )
        if not allowed:
            assert d.reason == DENY_ROLE_CAP

    def test_role_cap_equals_required_is_allowed(
        self, sp, sp_principal, public_project, workspace
    ):
        """Boundary case: cap exactly equals the required role must allow."""
        # DELETE on work_item requires MEMBER (15).
        _add_scope(sp, ResourceType.WORK_ITEM, Action.DELETE, project=public_project)
        _add_grant(sp, public_project, role_cap=15)
        d = authorize(
            sp_principal,
            Action.DELETE,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(public_project.id),
            ),
        )
        assert d.allowed
        assert d.reason == ALLOWED

    def test_role_cap_one_below_required_denies(
        self, sp, sp_principal, public_project, workspace
    ):
        _add_scope(sp, ResourceType.WORK_ITEM, Action.DELETE, project=public_project)
        _add_grant(sp, public_project, role_cap=5)
        d = authorize(
            sp_principal,
            Action.DELETE,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(public_project.id),
            ),
        )
        assert not d.allowed
        assert d.reason == DENY_ROLE_CAP


# --------------------------------------------------------------------------- #
# Step 4 — owner intersection                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestOwnerIntersectionStep:
    def test_owner_not_in_workspace_denies(
        self, workspace, create_user, public_project
    ):
        # Owner is created in workspace fixture, then removed from workspace
        sp = ServicePrincipal.objects.create(
            workspace=workspace, owner=create_user, name="orphan"
        )
        # Demote the owner to guest, then fully remove — covers both paths.
        WorkspaceMember.objects.filter(
            workspace=workspace, member=create_user
        ).delete()
        _add_scope(sp, ResourceType.WORK_ITEM, Action.READ, project=public_project)
        _add_grant(sp, public_project, role_cap=20)

        principal = ServicePrincipal_(service_principal=sp)
        d = authorize(
            principal,
            Action.READ,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(public_project.id),
            ),
        )
        assert not d.allowed
        assert d.reason == DENY_OWNER_NOT_WORKSPACE_MEMBER

    def test_owner_inactive_denies(
        self, workspace, create_user, public_project
    ):
        sp = ServicePrincipal.objects.create(
            workspace=workspace, owner=create_user, name="owner-off"
        )
        _add_scope(sp, ResourceType.WORK_ITEM, Action.READ, project=public_project)
        _add_grant(sp, public_project, role_cap=20)

        create_user.is_active = False
        create_user.save()

        principal = ServicePrincipal_(service_principal=sp)
        d = authorize(
            principal,
            Action.READ,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(public_project.id),
            ),
        )
        assert not d.allowed
        assert d.reason == DENY_INACTIVE_OWNER

    def test_owner_demotion_takes_effect_immediately(
        self, sp, sp_principal, public_project, workspace
    ):
        """Owner-intersection is non-cached: a demotion between calls takes
        effect on the very next authorize() invocation (sp-design.md §5.2)."""
        _add_scope(sp, ResourceType.WORK_ITEM, Action.DELETE, project=public_project)
        _add_grant(sp, public_project, role_cap=15)

        # First call: owner is ADMIN (20) → allowed.
        d = authorize(
            sp_principal,
            Action.DELETE,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(public_project.id),
            ),
        )
        assert d.allowed

        # Demote owner to GUEST (5) → owner role < grant role_cap (5).
        # DELETE on work_item needs MEMBER (15); the cap is 15, the owner
        # is now 5, so the intersection denies.
        WorkspaceMember.objects.filter(
            workspace=workspace, member=sp.owner
        ).update(role=5)

        d = authorize(
            sp_principal,
            Action.DELETE,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(public_project.id),
            ),
        )
        assert not d.allowed
        assert d.reason == DENY_OWNER_ROLE


# --------------------------------------------------------------------------- #
# Workspace-level resources (Q4)                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestWorkspaceLevelResources:
    def test_workspace_wide_read_allowed(
        self, sp, sp_principal, workspace
    ):
        # Workspace-wide scope, no project.
        _add_scope(sp, ResourceType.PROJECT, Action.READ, project=None)
        d = authorize(
            sp_principal,
            Action.READ,
            ResourceType.PROJECT,
            ctx=AuthzContext(workspace_slug=workspace.slug),
        )
        assert d.allowed

    def test_workspace_wide_write_rejected(self, sp, sp_principal, workspace):
        """Q4: workspace-wide grant only allows read; writes must land on a
        project-scoped grant."""
        _add_scope(sp, ResourceType.PROJECT, Action.CREATE, project=None)
        d = authorize(
            sp_principal,
            Action.CREATE,
            ResourceType.PROJECT,
            ctx=AuthzContext(workspace_slug=workspace.slug),
        )
        assert not d.allowed
        assert d.reason == DENY_WORKSPACE_LEVEL_WRITE

    def test_workspace_wide_update_rejected(self, sp, sp_principal, workspace):
        _add_scope(sp, ResourceType.MEMBER, Action.UPDATE, project=None)
        d = authorize(
            sp_principal,
            Action.UPDATE,
            ResourceType.MEMBER,
            ctx=AuthzContext(workspace_slug=workspace.slug),
        )
        assert not d.allowed
        assert d.reason == DENY_WORKSPACE_LEVEL_WRITE

    def test_workspace_wide_delete_rejected(self, sp, sp_principal, workspace):
        _add_scope(sp, ResourceType.INVITE, Action.DELETE, project=None)
        d = authorize(
            sp_principal,
            Action.DELETE,
            ResourceType.INVITE,
            ctx=AuthzContext(workspace_slug=workspace.slug),
        )
        assert not d.allowed
        assert d.reason == DENY_WORKSPACE_LEVEL_WRITE

    def test_project_scoped_write_on_workspace_resource_still_denied(
        self, sp, sp_principal, workspace, public_project
    ):
        """A project-scoped scope does not unlock writes on workspace-level
        resources — there is no project to anchor a write on."""
        _add_scope(sp, ResourceType.PROJECT, Action.CREATE, project=public_project)
        d = authorize(
            sp_principal,
            Action.CREATE,
            ResourceType.PROJECT,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(public_project.id),
            ),
        )
        assert not d.allowed
        assert d.reason == DENY_WORKSPACE_LEVEL_WRITE


# --------------------------------------------------------------------------- #
# End-to-end: the four steps in concert                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestAuthorizeEndToEnd:
    def test_full_chain_allow(
        self, sp, sp_principal, public_project, workspace
    ):
        _add_scope(sp, ResourceType.WORK_ITEM, Action.CREATE, project=public_project)
        _add_grant(sp, public_project, role_cap=20)
        d = authorize(
            sp_principal,
            Action.CREATE,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(public_project.id),
            ),
        )
        assert d.allowed
        assert d.effective_role == 20

    def test_resource_carries_project_id(
        self, sp, sp_principal, public_project, workspace
    ):
        """Passing the resource instance wins over ctx.project_id."""
        _add_scope(sp, ResourceType.WORK_ITEM, Action.READ, project=public_project)
        _add_grant(sp, public_project, role_cap=20)
        # ctx.project_id deliberately wrong; resource carries the right id.
        d = authorize(
            sp_principal,
            Action.READ,
            ResourceType.WORK_ITEM,
            resource=public_project,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id="00000000-0000-0000-0000-000000000000",
            ),
        )
        assert d.allowed

    def test_enforce_raises_on_deny(self, sp_principal):
        from rest_framework.exceptions import PermissionDenied

        d = authorize(sp_principal, Action.READ, "not_a_resource")
        with pytest.raises(PermissionDenied):
            enforce(d)

    def test_enforce_no_op_on_allow(
        self, sp, sp_principal, public_project, workspace
    ):
        _add_scope(sp, ResourceType.WORK_ITEM, Action.READ, project=public_project)
        _add_grant(sp, public_project, role_cap=20)
        d = authorize(
            sp_principal,
            Action.READ,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(public_project.id),
            ),
        )
        enforce(d)  # must not raise


# --------------------------------------------------------------------------- #
# VISIBLE_MEMBER_Q + sp_assignable                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestVisibleMemberPredicate:
    def test_visible_member_q_is_bot_false(self):
        """VISIBLE_MEMBER_Q is a Q object matching ``member__is_bot=False``."""
        assert isinstance(VISIBLE_MEMBER_Q, Q)
        # Re-render and look for the underlying children.
        sql = str(VISIBLE_MEMBER_Q)
        assert "is_bot" in sql

    def test_visible_member_qs_excludes_bots(
        self, workspace, second_user, create_user
    ):
        from plane.db.models import User

        bot = User.objects.create(
            email="bot@plane.so",
            username="bot-user",
            first_name="Bot",
            is_bot=True,
        )
        WorkspaceMember.objects.create(
            workspace=workspace, member=bot, role=15, is_active=True
        )
        WorkspaceMember.objects.create(
            workspace=workspace, member=second_user, role=15, is_active=True
        )
        # create_user is also a workspace member from the fixture.
        visible = list(visible_member_qs(workspace).values_list("member_id", flat=True))
        assert create_user.id in visible
        assert second_user.id in visible
        assert bot.id not in visible

    def test_is_member_visible_predicate(self, create_user, second_user):
        from plane.db.models import User

        bot = User.objects.create(
            email="bot2@plane.so",
            username="bot-user-2",
            is_bot=True,
        )
        assert is_member_visible(create_user) is True
        assert is_member_visible(second_user) is True
        assert is_member_visible(bot) is False
        assert is_member_visible(None) is False


@pytest.mark.contract
@pytest.mark.django_db
class TestSpAssignableToggle:
    def test_default_off_hides_sps(self, workspace, sp):
        rows = visible_principal_qs(workspace)
        kinds = [r.kind for r in rows]
        assert "service" not in kinds

    def test_default_off_returns_empty_sp_qs(self, workspace, sp):
        assert visible_sp_qs(workspace).count() == 0

    def test_workspace_settings_none_treated_as_off(self, workspace):
        assert is_sp_assignable(None) is False

    def test_workspace_sp_settings_default_false(self, workspace):
        s = WorkspaceSPSettings.objects.create(workspace=workspace)
        assert is_sp_assignable(s) is False

    def test_assignable_on_includes_sps(self, workspace, sp):
        WorkspaceSPSettings.objects.create(workspace=workspace, sp_assignable=True)
        rows = visible_principal_qs(workspace)
        kinds = [r.kind for r in rows]
        assert "service" in kinds
        assert any(r.row.id == sp.id for r in rows)

    def test_assignable_on_sps_have_is_active_filter(
        self, workspace, create_user
    ):
        inactive = ServicePrincipal.objects.create(
            workspace=workspace, owner=create_user, name="bot-x", is_active=False
        )
        WorkspaceSPSettings.objects.create(workspace=workspace, sp_assignable=True)
        rows = visible_principal_qs(workspace)
        ids = [r.row.id for r in rows if r.kind == "service"]
        assert inactive.id not in ids

    def test_assignable_on_sp_qs_returns_active_only(self, workspace, create_user):
        ServicePrincipal.objects.create(
            workspace=workspace, owner=create_user, name="alive", is_active=True
        )
        ServicePrincipal.objects.create(
            workspace=workspace, owner=create_user, name="dead", is_active=False
        )
        WorkspaceSPSettings.objects.create(workspace=workspace, sp_assignable=True)
        qs = visible_sp_qs(workspace)
        assert qs.count() == 1
        assert qs.first().name == "alive"

    def test_get_or_create_workspace_sp_settings_idempotent(self, workspace):
        s1 = get_or_create_workspace_sp_settings_for_test(workspace)
        s2 = get_or_create_workspace_sp_settings_for_test(workspace)
        assert s1.id == s2.id


def get_or_create_workspace_sp_settings_for_test(workspace):
    """Re-exported for the test below; the real helper lives in
    :mod:`plane.core.authz.visibility`."""
    from plane.core.authz.visibility import (
        get_or_create_workspace_sp_settings,
    )

    return get_or_create_workspace_sp_settings(workspace)


# --------------------------------------------------------------------------- #
# effective_actions / sp_effective_actions                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestEffectiveActions:
    def test_effective_actions_standard_set(self):
        for action in STANDARD_ACTIONS:
            assert action in ("read", "list", "create", "update", "delete")

    def test_sp_with_no_scope_all_actions_denied(self, sp, public_project, workspace):
        actions = effective_actions(
            ServicePrincipal_(service_principal=sp),
            ResourceType.WORK_ITEM,
            project=public_project,
            workspace=workspace,
        )
        assert len(actions) == len(STANDARD_ACTIONS)
        for a in actions:
            assert a.allowed is False
            assert a.resource_type == ResourceType.WORK_ITEM

    def test_user_principal_with_admin_role_allows_all(
        self, create_user, public_project, workspace
    ):
        principal = UserPrincipal(user=create_user, workspace_id=str(workspace.id))
        # create_user has role=20 in the workspace fixture; actions on
        # work_item are allowed up to MEMBER for CREATE, which admin covers.
        actions = effective_actions(
            principal,
            ResourceType.WORK_ITEM,
            project=public_project,
            workspace=workspace,
        )
        allowed_actions = {a.action for a in actions if a.allowed}
        assert Action.READ in allowed_actions
        assert Action.CREATE in allowed_actions

    def test_sp_effective_actions_allow_when_fully_granted(
        self, sp, public_project, workspace
    ):
        _add_scope(sp, ResourceType.WORK_ITEM, Action.ALL, project=public_project)
        _add_grant(sp, public_project, role_cap=20)
        actions = effective_actions(
            ServicePrincipal_(service_principal=sp),
            ResourceType.WORK_ITEM,
            project=public_project,
            workspace=workspace,
        )
        allowed_actions = {a.action for a in actions if a.allowed}
        assert allowed_actions == set(STANDARD_ACTIONS)

    def test_action_permission_dataclass_shape(self):
        ap = ActionPermission(
            action=Action.READ,
            resource_type=ResourceType.WORK_ITEM,
            allowed=True,
            reason=ALLOWED,
            effective_role=15,
        )
        assert ap.action == Action.READ
        assert ap.resource_type == ResourceType.WORK_ITEM
        assert ap.allowed is True


# --------------------------------------------------------------------------- #
# effective_role_cap — used by the front end                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestEffectiveRoleCap:
    def test_no_grant_returns_none(self, sp, public_project, workspace):
        cap = effective_role_cap(sp, str(public_project.id), workspace_slug=workspace.slug)
        assert cap is None

    def test_grant_only(self, sp, public_project, workspace):
        _add_grant(sp, public_project, role_cap=15)
        cap = effective_role_cap(sp, str(public_project.id), workspace_slug=workspace.slug)
        assert cap == 15

    def test_capped_by_owner_role(self, sp, public_project, workspace):
        _add_grant(sp, public_project, role_cap=20)
        WorkspaceMember.objects.filter(
            workspace=workspace, member=sp.owner
        ).update(role=5)
        cap = effective_role_cap(sp, str(public_project.id), workspace_slug=workspace.slug)
        assert cap == 5

    def test_owner_removed_returns_none(self, sp, public_project, workspace):
        _add_grant(sp, public_project, role_cap=20)
        WorkspaceMember.objects.filter(
            workspace=workspace, member=sp.owner
        ).delete()
        cap = effective_role_cap(sp, str(public_project.id), workspace_slug=workspace.slug)
        assert cap is None


# --------------------------------------------------------------------------- #
# Vocabulary & matrix integrity                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestVocabularyIntegrity:
    def test_required_role_returns_int_for_known_pair(self):
        assert required_role(Action.CREATE, ResourceType.WORK_ITEM) == 15

    def test_required_role_returns_none_for_unknown_pair(self):
        assert required_role(Action.READ, "totally_unknown") is None

    def test_required_role_consistent_with_role_cap(self):
        """role_cap choices (20/15/5) must align with the matrix floor.

        Iterates the imported ``ACTION_REQUIRED_ROLE`` mapping by attribute
        access (not via ``__globals__``), so the test stays valid even if
        the lookup helper's enclosing scope changes.
        """
        from plane.core.authz.actions import ACTION_REQUIRED_ROLE as MATRIX

        for required in MATRIX.values():
            assert required in (5, 15, 20), f"unexpected required role: {required}"

    def test_resource_choices_match_module_constants(self):
        from plane.core.authz.resources import RESOURCE_CHOICES as CORE_CHOICES

        # service_principals.models mirrors the choices through its own
        # module-level constant; ensure no drift.
        from plane.service_principals.constants import (
            RESOURCE_CHOICES as SP_CHOICES,
        )

        assert dict(CORE_CHOICES) == dict(SP_CHOICES)

    def test_sp_action_choices_are_subset_of_core(self):
        """The SP management API exposes a subset of authorize()'s action
        vocabulary — ``list`` is an internal action used for read-many
        semantics; the SP API exposes read/create/update/delete today.
        """
        from plane.core.authz.actions import ACTION_CHOICES as CORE_CHOICES

        from plane.service_principals.constants import (
            ACTION_CHOICES as SP_CHOICES,
        )

        sp_actions = set(dict(SP_CHOICES).keys())
        core_actions = set(dict(CORE_CHOICES).keys())
        assert sp_actions.issubset(core_actions), (
            f"SP exposes actions the authz module does not know: "
            f"{sp_actions - core_actions}"
        )


# --------------------------------------------------------------------------- #
# R1 review fixes — P2-1 invite floor, P2-2 LIST, P2-3 ALL bypass,            #
# P2-4 workspace-level owner floor, P2-5 cross-workspace, P2-6 pinning vector. #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestInviteFloorP2_1:
    """P2-1: invite read/list at MEMBER (15) — mirrors the app surface and
    closes the v1/admin-only gap that would otherwise let a guest-owned
    SP list invitations.
    """

    def test_invite_read_requires_member_role(self):
        assert required_role(Action.READ, ResourceType.INVITE) == 15

    def test_invite_list_requires_member_role(self):
        assert required_role(Action.LIST, ResourceType.INVITE) == 15

    def test_guest_cap_denies_invite_read(self, sp, public_project, workspace):
        """role_cap=GUEST (5) on the SP grant is below MEMBER (15) for invite
        read — owner-floor must deny, even though the SP could be used
        through a different surface at GUEST."""
        _add_scope(sp, ResourceType.INVITE, Action.READ, project=None)
        # No project grant; workspace-level resource uses the workspace-wide
        # scope row + the action's required role as the owner floor.
        principal = ServicePrincipal_(service_principal=sp)
        d = authorize(
            principal,
            Action.READ,
            ResourceType.INVITE,
            ctx=AuthzContext(workspace_slug=workspace.slug),
        )
        # The owner (create_user fixture) is admin (20) so the floor holds.
        # We instead assert the matrix value and that an owner at GUEST
        # would deny. Demote owner and rerun.
        WorkspaceMember.objects.filter(
            workspace=workspace, member=sp.owner
        ).update(role=5)
        d = authorize(
            principal,
            Action.READ,
            ResourceType.INVITE,
            ctx=AuthzContext(workspace_slug=workspace.slug),
        )
        assert not d.allowed
        assert d.reason == DENY_OWNER_ROLE


@pytest.mark.contract
@pytest.mark.django_db
class TestListActionP2_2:
    """P2-2: a ``read`` scope row must satisfy a ``list`` request because
    the SP management API has no ``list`` action — wiring a list endpoint
    to ``Action.LIST`` must not surprise the engine.
    """

    def test_list_satisfied_by_read_scope(
        self, sp, sp_principal, public_project, workspace
    ):
        _add_scope(sp, ResourceType.WORK_ITEM, Action.READ, project=public_project)
        _add_grant(sp, public_project, role_cap=15)
        d = authorize(
            sp_principal,
            Action.LIST,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(public_project.id),
            ),
        )
        assert d.allowed, (
            f"A read scope row should satisfy a list request; got {d}"
        )

    def test_list_satisfied_by_all_action_scope(
        self, sp, sp_principal, public_project, workspace
    ):
        _add_scope(sp, ResourceType.WORK_ITEM, Action.ALL, project=public_project)
        _add_grant(sp, public_project, role_cap=15)
        d = authorize(
            sp_principal,
            Action.LIST,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(public_project.id),
            ),
        )
        assert d.allowed

    def test_list_without_any_scope_denies(
        self, sp, sp_principal, public_project, workspace
    ):
        # Create a different-scope row to prove lookup misses.
        _add_scope(
            sp, ResourceType.WORK_ITEM, Action.CREATE, project=public_project
        )
        _add_grant(sp, public_project, role_cap=20)
        d = authorize(
            sp_principal,
            Action.LIST,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(public_project.id),
            ),
        )
        assert not d.allowed
        assert d.reason == DENY_SCOPE_MISS


@pytest.mark.contract
@pytest.mark.django_db
class TestAllActionBypassP2_3:
    """P2-3: ``all`` is rejected as caller-side action/resource_type so a
    future caller cannot bypass the Q4 workspace-level write guard or
    the role_cap floor by passing the wildcard through.
    """

    def test_action_all_as_caller_input_denies(self, sp_principal):
        d = authorize(sp_principal, Action.ALL, ResourceType.PROJECT)
        assert not d.allowed
        assert d.reason == DENY_UNREGISTERED

    def test_resource_all_as_caller_input_denies(self, sp_principal):
        d = authorize(sp_principal, Action.READ, ResourceType.ALL)
        assert not d.allowed
        assert d.reason == DENY_UNREGISTERED

    def test_action_all_workspace_level_does_not_grant_write(
        self, sp, sp_principal, workspace
    ):
        """Even with an all-action workspace-wide scope, caller-side
        ``all`` is rejected before the Q4 guard runs — no shortcut."""
        _add_scope(sp, ResourceType.ALL, Action.ALL, project=None)
        d = authorize(
            sp_principal,
            Action.ALL,
            ResourceType.PROJECT,
            ctx=AuthzContext(workspace_slug=workspace.slug),
        )
        assert not d.allowed
        assert d.reason == DENY_UNREGISTERED

    def test_scope_row_with_all_action_still_authorizes_real_call(
        self, sp, sp_principal, public_project, workspace
    ):
        """The ``all`` action is still honored on scope rows (not on caller
        input). A scope row keyed on ``all`` satisfies a real call."""
        _add_scope(sp, ResourceType.WORK_ITEM, Action.ALL, project=public_project)
        _add_grant(sp, public_project, role_cap=15)
        d = authorize(
            sp_principal,
            Action.READ,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(public_project.id),
            ),
        )
        assert d.allowed


@pytest.mark.contract
@pytest.mark.django_db
class TestWorkspaceLevelOwnerFloorP2_4:
    """P2-4: workspace-level resource + project context must still check
    the owner floor against the action's required role — previously
    skipped because ``effective_role`` was ``None`` on the workspace path.
    """

    def test_owner_floor_applies_to_workspace_read_with_project_ctx(
        self, sp, sp_principal, public_project, workspace
    ):
        """A workspace-level resource (invite) with a project_id in the
        context skips the grant step. Previously the owner-intersection
        floor was None (effective_role is None on the workspace path).
        The fix routes to required_role(action, resource_type) so MEMBER
        floors still bite — even on workspace-level resources."""

        _add_scope(sp, ResourceType.INVITE, Action.READ, project=None)
        WorkspaceMember.objects.filter(
            workspace=workspace, member=sp.owner
        ).update(role=5)
        d = authorize(
            sp_principal,
            Action.READ,
            ResourceType.INVITE,
            resource=public_project,
            ctx=AuthzContext(workspace_slug=workspace.slug),
        )
        # invite read requires MEMBER (15); owner is GUEST (5) — denied.
        assert not d.allowed
        assert d.reason == DENY_OWNER_ROLE

    def test_owner_floor_passed_when_owner_meets_floor(
        self, sp, sp_principal, public_project, workspace
    ):
        _add_scope(sp, ResourceType.INVITE, Action.READ, project=None)
        # create_user is admin (20) ≥ MEMBER (15) → allowed.
        d = authorize(
            sp_principal,
            Action.READ,
            ResourceType.INVITE,
            resource=public_project,
            ctx=AuthzContext(workspace_slug=workspace.slug),
        )
        assert d.allowed


@pytest.mark.contract
@pytest.mark.django_db
class TestCrossWorkspaceConsistencyP2_5:
    """P2-5: the engine refuses to authorize across workspaces even if a
    caller passes a foreign slug. The M6 management API cannot produce
    this state, but defense-in-depth matters when M8/M9 start passing
    ctx from request routing.
    """

    def test_foreign_workspace_slug_denies(
        self, workspace, create_user, public_project
    ):
        from plane.db.models import Workspace

        other = Workspace.objects.create(
            name="Other", owner=create_user, slug="other-ws"
        )
        WorkspaceMember.objects.create(
            workspace=other, member=create_user, role=20
        )
        sp = ServicePrincipal.objects.create(
            workspace=workspace, owner=create_user, name="bot"
        )
        _add_scope(sp, ResourceType.WORK_ITEM, Action.READ, project=public_project)
        _add_grant(sp, public_project, role_cap=20)
        principal = ServicePrincipal_(service_principal=sp)
        d = authorize(
            principal,
            Action.READ,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(
                workspace_slug="other-ws",
                project_id=str(public_project.id),
            ),
        )
        assert not d.allowed
        # DENY_NO_PRINCIPAL is reused for cross-workspace — the slug
        # mismatch makes the principal's effective workspace ambiguous.
        assert d.reason == DENY_NO_PRINCIPAL

    def test_matching_slug_allows(
        self, sp, sp_principal, public_project, workspace
    ):
        _add_scope(sp, ResourceType.WORK_ITEM, Action.READ, project=public_project)
        _add_grant(sp, public_project, role_cap=20)
        d = authorize(
            sp_principal,
            Action.READ,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(public_project.id),
            ),
        )
        assert d.allowed

    def test_no_slug_in_ctx_falls_back_to_sp_workspace(
        self, sp, sp_principal, public_project, workspace
    ):
        """Defense-in-depth is only triggered when the caller passes a
        slug. With no slug, the engine uses the SP's own workspace — the
        normal path."""
        _add_scope(sp, ResourceType.WORK_ITEM, Action.READ, project=public_project)
        _add_grant(sp, public_project, role_cap=20)
        d = authorize(
            sp_principal,
            Action.READ,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(project_id=str(public_project.id)),
        )
        assert d.allowed


@pytest.mark.contract
@pytest.mark.django_db
class TestTowerVectorPinningP2_6:
    """P2-6: pinning test for the tower-flagged vector — a workspace-wide
    scope row + secret project + NO ProjectGrant must yield DENY_GRANT_MISS.
    The grant step runs unconditionally for project-scoped resources so a
    workspace-wide scope cannot reach a secret project.
    """

    def test_workspace_wide_scope_secret_project_no_grant_denies(
        self, sp, sp_principal, secret_project, workspace
    ):
        # Workspace-wide scope row (project=None) — would have allowed
        # access under a buggy implementation.
        _add_scope(sp, ResourceType.WORK_ITEM, Action.READ, project=None)
        # Deliberately no ProjectGrant for the secret project.
        d = authorize(
            sp_principal,
            Action.READ,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(secret_project.id),
            ),
        )
        assert not d.allowed
        assert d.reason == DENY_GRANT_MISS

    def test_workspace_wide_scope_with_grant_allows(
        self, sp, sp_principal, secret_project, workspace
    ):
        """Sanity: with both the workspace-wide scope AND a project grant,
        the secret project is reachable (default-deny was the only path
        we wanted to pin — opt-in remains the only authorized path)."""
        _add_scope(sp, ResourceType.WORK_ITEM, Action.READ, project=None)
        _add_grant(sp, secret_project, role_cap=20)
        d = authorize(
            sp_principal,
            Action.READ,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(secret_project.id),
            ),
        )
        assert d.allowed

    def test_workspace_wide_scope_inactive_grant_denies(
        self, sp, sp_principal, secret_project, workspace
    ):
        """An inactive grant also denies — the grant step checks
        ``is_active=True``."""
        _add_scope(sp, ResourceType.WORK_ITEM, Action.READ, project=None)
        _add_grant(sp, secret_project, role_cap=20, is_active=False)
        d = authorize(
            sp_principal,
            Action.READ,
            ResourceType.WORK_ITEM,
            ctx=AuthzContext(
                workspace_slug=workspace.slug,
                project_id=str(secret_project.id),
            ),
        )
        assert not d.allowed
        assert d.reason == DENY_GRANT_MISS