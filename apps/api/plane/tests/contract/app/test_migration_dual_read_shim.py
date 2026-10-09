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
        # No ProjectGrant for workspace-wide scopes
        assert not ProjectGrant.objects.filter(service_principal=sp).exists()


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
