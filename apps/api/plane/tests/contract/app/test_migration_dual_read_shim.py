# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: M12 AIAccount → ServicePrincipal migration command +
dual-read shim in ``APIKeyAuthentication``.

These tests pin the M12 contract:

* The conversion command is idempotent — re-running it on a converted
  database is a no-op for converted accounts and picks up any that
  arrive later.
* Original ``plane_api_`` tokens keep authenticating through the
  dual-read shim after the command flips them to
  ``principal_type=SERVICE``; the bot's old ``request.user.is_bot``
  path is no longer reached.
* The kill-switch setting closes the legacy door cleanly.
* Conversion semantics match the mission spec: name / description /
  owner / avatar / is_active propagate; workspace-wide scopes become
  workspace-wide ServiceScope rows; project scopes produce
  ``ServiceScope`` + ``ProjectGrant(role_cap=<bot's PM role>)``; bot
  memberships are soft-deleted; tokens are reassigned.
"""

from io import StringIO
from uuid import uuid4

import pytest
from django.contrib.auth.hashers import make_password
from django.core.management import call_command
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIClient

from plane.ai_accounts.models import (
    AIAccount,
    AIAccountMigrationRecord,
    AIScopePolicy,
)
from plane.db.models import (
    APIToken,
    Project,
    ProjectMember,
    User,
    WorkspaceMember,
)
from plane.service_principals.constants import PrincipalType
from plane.service_principals.models import (
    ProjectGrant,
    ServicePrincipal,
    ServiceScope,
)


def _make_ai_account(workspace, owner, bot_user, name="bot-1", description=""):
    account = AIAccount.objects.create(
        workspace=workspace,
        owner=owner,
        bot_user=bot_user,
        name=name,
        description=description,
    )
    return account


def _issue_token(bot_user, workspace, *, label="ai:bot-1"):
    return APIToken.objects.create(
        user=bot_user,
        label=label,
        token=f"plane_api_{uuid4().hex}",
        user_type=1,
        is_service=True,
        workspace=workspace,
    )


@pytest.fixture
def bot_factory(db):
    """Factory that mirrors ``ai_accounts/views.py`` bot creation."""

    def _make(email=None, bot_type="AI_AGENT"):
        local = (email or f"ai_bot_{uuid4().hex[:12]}@plane.so").split("@")[0]
        return User.objects.create(
            username=local,
            email=email or f"{local}@plane.so",
            display_name=local,
            first_name=local,
            last_name="",
            is_bot=True,
            bot_type=bot_type,
            password=make_password(uuid4().hex),
            is_password_autoset=True,
        )

    return _make


@pytest.fixture
def project(db, workspace, create_user):
    proj = Project.objects.create(
        name="Test Project",
        identifier="TP",
        workspace=workspace,
        created_by=create_user,
        network=2,
    )
    ProjectMember.objects.create(
        workspace=workspace, project=proj, member=create_user, role=20, is_active=True
    )
    return proj


@pytest.mark.contract
class TestMigrationCommand:
    def test_dry_run_creates_no_rows(self, workspace, create_user, bot_factory, project):
        bot = bot_factory()
        WorkspaceMember.objects.create(
            workspace=workspace, member=bot, role=15, is_active=True
        )
        ProjectMember.objects.create(
            workspace=workspace, project=project, member=bot, role=15, is_active=True
        )
        account = _make_ai_account(
            workspace, create_user, bot, name="dry-run-bot"
        )
        AIScopePolicy.objects.create(
            ai_account=account, project=project, resource_type="work_item", action="read"
        )

        out = StringIO()
        call_command(
            "migrate_ai_accounts_to_service_principals", "--dry-run", stdout=out
        )

        assert "Would convert 1" in out.getvalue()
        assert ServicePrincipal.objects.count() == 0
        assert AIScopePolicy.objects.filter(ai_account=account).exists()
        assert WorkspaceMember.objects.filter(member=bot, is_active=True).exists()

    def test_first_run_creates_sp_and_scopes(
        self, workspace, create_user, bot_factory, project
    ):
        bot = bot_factory()
        WorkspaceMember.objects.create(
            workspace=workspace, member=bot, role=15, is_active=True
        )
        ProjectMember.objects.create(
            workspace=workspace, project=project, member=bot, role=15, is_active=True
        )
        account = _make_ai_account(
            workspace, create_user, bot, name="live-bot"
        )
        AIScopePolicy.objects.create(
            ai_account=account, project=project, resource_type="work_item", action="read"
        )
        AIScopePolicy.objects.create(
            ai_account=account, project=project, resource_type="comment", action="create"
        )
        AIScopePolicy.objects.create(
            ai_account=account, project=None, resource_type="project", action="read"
        )
        token = _issue_token(bot, workspace)

        call_command("migrate_ai_accounts_to_service_principals")

        sp = ServicePrincipal.objects.get(id=account.id)
        assert sp.workspace_id == workspace.id
        assert sp.owner_id == create_user.id
        assert sp.name == "live-bot"
        assert sp.is_active is True

        assert ServiceScope.objects.filter(service_principal=sp).count() == 3

        # Two project-scoped policies → one ProjectGrant (deduped by project)
        grants = list(ProjectGrant.objects.filter(service_principal=sp))
        assert len(grants) == 1
        assert grants[0].project_id == project.id
        assert grants[0].role_cap == 15
        assert grants[0].is_active is True

        # Bot memberships are soft-deleted
        assert not WorkspaceMember.objects.filter(member=bot, is_active=True).exists()
        assert not ProjectMember.objects.filter(member=bot, is_active=True).exists()

        # Token is reassigned to the SP, and ``user`` is repointed to the owner
        token.refresh_from_db()
        assert token.principal_type == PrincipalType.SERVICE
        assert token.service_principal_id == sp.id
        assert token.user_id == create_user.id

        # Migration record persists the metrics
        rec = AIAccountMigrationRecord.objects.get(ai_account=account)
        assert rec.service_principal_id == sp.id
        assert rec.scopes_migrated == 3
        assert rec.grants_migrated == 1
        assert rec.tokens_migrated == 1

    def test_idempotent_second_run_is_noop(
        self, workspace, create_user, bot_factory, project
    ):
        bot = bot_factory()
        WorkspaceMember.objects.create(
            workspace=workspace, member=bot, role=20, is_active=True
        )
        ProjectMember.objects.create(
            workspace=workspace, project=project, member=bot, role=15, is_active=True
        )
        account = _make_ai_account(
            workspace, create_user, bot, name="idem-bot"
        )
        AIScopePolicy.objects.create(
            ai_account=account, project=project, resource_type="work_item", action="read"
        )
        call_command("migrate_ai_accounts_to_service_principals")

        # Snapshot ServicePrincipal count and grant count
        sp_count_1 = ServicePrincipal.objects.count()
        grant_count_1 = ProjectGrant.objects.count()
        scope_count_1 = ServiceScope.objects.count()
        token_count_1 = APIToken.objects.count()
        marker_count_1 = AIAccountMigrationRecord.objects.count()

        # Re-run
        call_command("migrate_ai_accounts_to_service_principals")

        assert ServicePrincipal.objects.count() == sp_count_1
        assert ProjectGrant.objects.count() == grant_count_1
        assert ServiceScope.objects.count() == scope_count_1
        assert APIToken.objects.count() == token_count_1
        assert AIAccountMigrationRecord.objects.count() == marker_count_1

    def test_picks_up_new_accounts_after_first_run(
        self, workspace, create_user, bot_factory, project
    ):
        bot1 = bot_factory()
        bot2 = bot_factory()
        for b in (bot1, bot2):
            WorkspaceMember.objects.create(
                workspace=workspace, member=b, role=15, is_active=True
            )
            ProjectMember.objects.create(
                workspace=workspace, project=project, member=b, role=15, is_active=True
            )

        a1 = _make_ai_account(workspace, create_user, bot1, name="old")
        a2 = _make_ai_account(workspace, create_user, bot2, name="new")
        AIScopePolicy.objects.create(
            ai_account=a1, project=project, resource_type="work_item", action="read"
        )
        AIScopePolicy.objects.create(
            ai_account=a2, project=project, resource_type="work_item", action="read"
        )

        call_command("migrate_ai_accounts_to_service_principals")
        converted_first = ServicePrincipal.objects.filter(
            id__in=[a1.id, a2.id]
        ).count()
        assert converted_first == 2

        # Simulate a fresh arrival: a third bot is created after the
        # first run; the second run picks it up.
        bot3 = bot_factory()
        WorkspaceMember.objects.create(
            workspace=workspace, member=bot3, role=15, is_active=True
        )
        ProjectMember.objects.create(
            workspace=workspace, project=project, member=bot3, role=15, is_active=True
        )
        a3 = _make_ai_account(workspace, create_user, bot3, name="late")
        AIScopePolicy.objects.create(
            ai_account=a3, project=project, resource_type="work_item", action="read"
        )

        call_command("migrate_ai_accounts_to_service_principals")

        assert ServicePrincipal.objects.filter(
            id__in=[a1.id, a2.id, a3.id]
        ).count() == 3
        assert AIAccountMigrationRecord.objects.filter(
            ai_account__in=[a1, a2, a3]
        ).count() == 3

    def test_workspace_wide_scope_stays_workspace_wide(
        self, workspace, create_user, bot_factory
    ):
        bot = bot_factory()
        WorkspaceMember.objects.create(
            workspace=workspace, member=bot, role=15, is_active=True
        )
        account = _make_ai_account(
            workspace, create_user, bot, name="ws-bot"
        )
        AIScopePolicy.objects.create(
            ai_account=account,
            project=None,
            resource_type="project",
            action="read",
        )

        call_command("migrate_ai_accounts_to_service_principals")

        sp = ServicePrincipal.objects.get(id=account.id)
        scope = ServiceScope.objects.get(service_principal=sp)
        assert scope.project_id is None
        assert scope.resource_type == "project"
        assert scope.action == "read"
        # Bot had no active ProjectMember rows at all, so no grants.
        assert not ProjectGrant.objects.filter(service_principal=sp).exists()

    # ------------------------------------------------------------------ #
    # M20 — workspace-global policies + active project memberships must    #
    # produce one ProjectGrant per project the bot is reachable in.        #
    # ------------------------------------------------------------------ #

    def test_workspace_global_policies_grant_for_each_project_membership(
        self, workspace, create_user, bot_factory, project
    ):
        """Repro of the M20 production incident.

        Accounts whose every ``AIScopePolicy`` is workspace-wide
        (``project_id IS NULL``) used to be converted with zero
        ``ProjectGrant`` rows while the same command soft-deleted the
        bot's ``ProjectMember`` rows — locking the bot out of every
        project at ``authorize()`` step 2 (``DENY_GRANT_MISS``). After
        the fix, the union of project-scoped policies and active
        ``ProjectMember`` projects must drive a grant per project, and
        ``authorize()`` must ALLOW a project-scoped read in each.
        """
        from plane.core.authz.actions import Action
        from plane.core.authz.engine import AuthzContext, authorize
        from plane.core.authz.principal import ServicePrincipal_
        from plane.core.authz.resources import ResourceType

        bot = bot_factory()
        WorkspaceMember.objects.create(
            workspace=workspace, member=bot, role=15, is_active=True
        )

        # Two extra projects alongside the existing ``project`` fixture so
        # the bot ends up reachable in three projects total — the exact
        # shape production deployed with.
        more_projects = []
        for i, (name, ident) in enumerate(
            [("Alpha", "AL"), ("Beta", "BE")], start=1
        ):
            p = Project.objects.create(
                name=name,
                identifier=ident,
                workspace=workspace,
                created_by=create_user,
                network=2,
            )
            ProjectMember.objects.create(
                workspace=workspace, project=p, member=create_user, role=20,
                is_active=True,
            )
            # Bot holds a ProjectMember seat with role=15 in each.
            ProjectMember.objects.create(
                workspace=workspace, project=p, member=bot, role=15,
                is_active=True,
            )
            more_projects.append(p)

        ProjectMember.objects.create(
            workspace=workspace, project=project, member=bot, role=20,
            is_active=True,
        )

        account = _make_ai_account(
            workspace, create_user, bot, name="global-only-bot"
        )
        # Workspace-global ``all``/``all`` only — the bug shape. No
        # project_id anywhere on the policy side.
        AIScopePolicy.objects.create(
            ai_account=account,
            project=None,
            resource_type=ResourceType.ALL,
            action=Action.ALL,
        )

        out = StringIO()
        call_command(
            "migrate_ai_accounts_to_service_principals", stdout=out
        )

        sp = ServicePrincipal.objects.get(id=account.id)
        grants = list(ProjectGrant.objects.filter(service_principal=sp))
        granted_pids = {g.project_id for g in grants}

        # One grant per project the bot was a member of: three total.
        expected_pids = {project.id, more_projects[0].id, more_projects[1].id}
        assert granted_pids == expected_pids, (
            f"expected grants for {expected_pids}, got {granted_pids}"
        )
        assert len(grants) == 3
        # role_cap mirrors the bot's ProjectMember role for each project.
        cap_by_pid = {g.project_id: g.role_cap for g in grants}
        assert cap_by_pid[project.id] == 20  # the fixture membership
        assert cap_by_pid[more_projects[0].id] == 15
        assert cap_by_pid[more_projects[1].id] == 15

        # Memberships are still soft-deleted — the grant is the only
        # forward path; ProjectMember rows must NOT remain active.
        assert not ProjectMember.objects.filter(
            member=bot, is_active=True
        ).exists()

        # And authorize() must ALLOW a work_item read in each project —
        # the exact request that 403'd in production.
        principal = ServicePrincipal_(service_principal=sp)
        ctx = AuthzContext(
            workspace_slug=workspace.slug, project_id=str(project.id)
        )
        decision = authorize(
            principal,
            Action.READ,
            ResourceType.WORK_ITEM,
            ctx=ctx,
        )
        assert decision.allowed, (
            f"authorize() denied a work_item read in project "
            f"{project.id}: {decision.reason} — {decision.detail}"
        )

        for p in more_projects:
            ctx_p = AuthzContext(
                workspace_slug=workspace.slug, project_id=str(p.id)
            )
            decision_p = authorize(
                principal,
                Action.READ,
                ResourceType.WORK_ITEM,
                ctx=ctx_p,
            )
            assert decision_p.allowed, (
                f"authorize() denied a work_item read in project "
                f"{p.id}: {decision_p.reason} — {decision_p.detail}"
            )

        # Migration record stores the real grant count, not the
        # misleading workspace-only count from the pre-fix dry-run.
        rec = AIAccountMigrationRecord.objects.get(ai_account=account)
        assert rec.grants_migrated == 3

    def test_dry_run_grant_count_matches_apply(
        self, workspace, create_user, bot_factory, project
    ):
        """``--dry-run`` and the apply path must agree on the grant count
        per account — M20 found dry-run printed ``projects_granted=10``
        while the apply path created ``grants=0`` for the same input.
        """
        bot = bot_factory()
        WorkspaceMember.objects.create(
            workspace=workspace, member=bot, role=15, is_active=True
        )
        ProjectMember.objects.create(
            workspace=workspace, project=project, member=bot, role=15,
            is_active=True,
        )
        account = _make_ai_account(
            workspace, create_user, bot, name="agreement-bot"
        )
        # Workspace-global policy only — the divergence case.
        AIScopePolicy.objects.create(
            ai_account=account,
            project=None,
            resource_type="work_item",
            action="read",
        )

        dry_out = StringIO()
        call_command(
            "migrate_ai_accounts_to_service_principals",
            "--dry-run",
            stdout=dry_out,
        )
        dry_text = dry_out.getvalue()
        # Lock the surface so a future label change does not silently
        # break the agreement. Pre-fix this would have read
        # ``projects_granted=1`` while apply reported ``grants=0``.
        assert "grants=1" in dry_text, dry_text

        apply_out = StringIO()
        call_command(
            "migrate_ai_accounts_to_service_principals", stdout=apply_out
        )
        apply_text = apply_out.getvalue()
        assert "grants=1" in apply_text, apply_text

        # Sanity: dry-run wrote nothing; apply wrote one SP and grant.
        assert ServicePrincipal.objects.count() == 1
        assert ProjectGrant.objects.count() == 1


@pytest.mark.contract
class TestDualReadShim:
    """``APIKeyAuthentication`` must route migrated tokens through the SP
    branch so original ``plane_api_`` secrets continue to work."""

    def _make_request(self):
        return APIClient()

    def _setup_migrated(self, workspace, create_user, bot_factory):
        bot = bot_factory()
        WorkspaceMember.objects.create(
            workspace=workspace, member=bot, role=15, is_active=True
        )
        account = _make_ai_account(workspace, create_user, bot, name="shim-bot")
        token = _issue_token(bot, workspace, label="ai:shim")
        call_command("migrate_ai_accounts_to_service_principals")
        token.refresh_from_db()
        return bot, account, token

    def test_migrated_token_routes_through_sp_branch(
        self, workspace, create_user, bot_factory
    ):
        from plane.api.middleware.api_authentication import APIKeyAuthentication

        bot, account, token = self._setup_migrated(
            workspace, create_user, bot_factory
        )

        # The token must now be in principal_type=SERVICE with service_principal set
        assert token.principal_type == PrincipalType.SERVICE
        sp = ServicePrincipal.objects.get(id=account.id)
        assert token.service_principal_id == sp.id

        auth = APIKeyAuthentication()
        sentinel = {"_sp_principal": None, "_sp_token": None}
        request = type(
            "R",
            (),
            {
                "headers": {"X-Api-Key": token.token},
                "user": None,
                "_sp_principal": sentinel["_sp_principal"],
                "_sp_token": sentinel["_sp_token"],
            },
        )()

        user, token_value = auth.authenticate(request)

        assert user.id == create_user.id  # request.user = owner
        assert request._sp_principal is not None
        assert request._sp_principal.service_principal.id == sp.id

    def test_unmigrated_token_keeps_legacy_path_with_default_settings(
        self, workspace, create_user, bot_factory
    ):
        """Without PLANE_APIKEY_DISABLE_LEGACY_DUAL_READ, an unmigrated
        ``plane_api_`` token continues to resolve to the bot User
        (no SP principal stashed)."""
        from plane.api.middleware.api_authentication import APIKeyAuthentication

        bot = bot_factory()
        WorkspaceMember.objects.create(
            workspace=workspace, member=bot, role=15, is_active=True
        )
        token = _issue_token(bot, workspace)
        # principal_type stays USER (no conversion command run)
        assert token.principal_type == PrincipalType.USER

        auth = APIKeyAuthentication()
        request = type(
            "R",
            (),
            {
                "headers": {"X-Api-Key": token.token},
                "user": None,
                "_sp_principal": None,
                "_sp_token": None,
            },
        )()

        user, _ = auth.authenticate(request)

        assert user.id == bot.id
        # No SP principal stashed — legacy path returned.
        assert request._sp_principal is None

    def test_kill_switch_blocks_unmigrated_legacy_token(
        self, workspace, create_user, bot_factory
    ):
        """With the kill switch ON, an unmigrated ``plane_api_`` token
        must fail closed with AuthenticationFailed, not leak the bot."""
        from rest_framework.exceptions import AuthenticationFailed

        from plane.api.middleware.api_authentication import APIKeyAuthentication

        bot = bot_factory()
        WorkspaceMember.objects.create(
            workspace=workspace, member=bot, role=15, is_active=True
        )
        token = _issue_token(bot, workspace)
        assert token.principal_type == PrincipalType.USER

        auth = APIKeyAuthentication()
        request = type(
            "R",
            (),
            {
                "headers": {"X-Api-Key": token.token},
                "user": None,
                "_sp_principal": None,
                "_sp_token": None,
            },
        )()

        with override_settings(PLANE_APIKEY_DISABLE_LEGACY_DUAL_READ=True):
            with pytest.raises(AuthenticationFailed):
                auth.authenticate(request)
