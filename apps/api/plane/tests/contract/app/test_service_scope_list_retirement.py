# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: retire the legacy ``action='list'`` SP scope rows (M38).

Two halves, pinning both ends of the fix:

* ``service_principals/migrations/0003_retire_list_scope_rows.py`` deletes
  active ``list`` scope rows — but only where a live ``read`` row on the same
  ``(service_principal_id, resource_type, project_id)`` makes the deletion
  permission-equivalent. A ``list`` row without such a sibling is the
  account's only grant for that resource and must survive.
* ``migrate_ai_accounts_to_service_principals`` stops producing them in the
  first place: a legacy ``list`` policy whose resource already has a ``read``
  policy is skipped, otherwise it is widened to ``read`` with a warning.
"""

import importlib
from io import StringIO
from uuid import uuid4

import pytest
from django.apps import apps as django_apps
from django.contrib.auth.hashers import make_password
from django.core.management import call_command
from django.db import connection
from django.db.migrations.state import ModelState, ProjectState
from django.db.models import Manager
from django.utils import timezone

from plane.ai_accounts.models import AIAccount, AIAccountMigrationRecord, AIScopePolicy
from plane.db.models import Project, ProjectMember, User
from plane.service_principals.models import ServicePrincipal, ServiceScope

# ``importlib`` because the migration module name starts with a digit.
LISTRETIRE = importlib.import_module(
    "plane.service_principals.migrations.0003_retire_list_scope_rows"
)


def _make_bot_user():
    local = f"ai_bot_{uuid4().hex[:12]}"
    return User.objects.create(
        username=local,
        email=f"{local}@plane.so",
        display_name=local,
        first_name=local,
        last_name="",
        is_bot=True,
        bot_type="AI_AGENT",
        password=make_password(uuid4().hex),
        is_password_autoset=True,
    )


def _make_ai_account(workspace, owner, bot_user, name="list-bot"):
    return AIAccount.objects.create(
        workspace=workspace, owner=owner, bot_user=bot_user, name=name
    )


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


@pytest.fixture
def historical_apps():
    """The app registry a migration receives.

    ``ProjectState.from_apps`` renders the same shimmed models Django builds
    for migration state, so the models it hands back carry a plain
    ``Manager``: no soft-delete filtering on reads and ``delete()`` issues a
    real ``DELETE``. Running the migration function against it keeps these
    tests faithful to the deployed path instead of hand-waving the manager
    difference away.
    """
    return ProjectState.from_apps(django_apps).apps


@pytest.mark.contract
class TestHistoricalModelSemantics:
    def test_migrations_see_a_plain_manager(self, historical_apps):
        """Why the migration's ``delete()`` is a hard DELETE.

        Django shims a custom default manager named ``objects`` to a plain
        ``Manager`` and then drops it outright when it is the only manager, so
        migration state carries no ``SoftDeletionManager``. This test fails if
        that ever changes, which would silently turn the retirement into a
        soft delete (and drop the ``deleted_at`` filtering the migration does
        by hand).
        """
        state = ModelState.from_model(ServiceScope)
        assert state.managers == []

        historical = historical_apps.get_model("service_principals", "ServiceScope")
        assert type(historical.objects) is Manager

        # The real model's plain manager is the exact behavioural match.
        assert type(ServiceScope.all_objects) is Manager


@pytest.mark.contract
class TestListScopeRetirementMigration:
    """The data migration, executed the way ``migrate`` executes it."""

    def _run_migration(self, historical_apps):
        with connection.schema_editor(atomic=False) as schema_editor:
            LISTRETIRE.retire_unreachable_list_scope_rows(
                historical_apps, schema_editor
            )

    def test_deletes_list_rows_with_a_read_sibling(
        self, db, workspace, create_user, project, historical_apps
    ):
        sp = ServicePrincipal.objects.create(
            workspace=workspace, owner=create_user, name="legacy-sp"
        )
        # Workspace-wide (project_id IS NULL) — the shape every one of the 28
        # rows seen in practice has — and project-scoped, because the sibling
        # match has to handle both.
        ServiceScope.objects.create(
            service_principal=sp, project=None, resource_type="work_item", action="list"
        )
        ServiceScope.objects.create(
            service_principal=sp, project=None, resource_type="work_item", action="read"
        )
        ServiceScope.objects.create(
            service_principal=sp, project=project, resource_type="cycle", action="list"
        )
        ServiceScope.objects.create(
            service_principal=sp, project=project, resource_type="cycle", action="read"
        )

        self._run_migration(historical_apps)

        # Gone from the table, not tombstoned.
        assert ServiceScope.all_objects.filter(service_principal=sp, action="list").count() == 0
        read_rows = ServiceScope.objects.filter(service_principal=sp, action="read")
        assert read_rows.count() == 2
        assert set(read_rows.values_list("project_id", "resource_type")) == {
            (None, "work_item"),
            (project.id, "cycle"),
        }

    def test_keeps_list_rows_without_a_read_sibling(
        self, db, workspace, create_user, project, historical_apps
    ):
        sp = ServicePrincipal.objects.create(
            workspace=workspace, owner=create_user, name="orphan-sp"
        )
        orphan = ServiceScope.objects.create(
            service_principal=sp, project=None, resource_type="cycle", action="list"
        )
        # A different resource type is not a sibling.
        ServiceScope.objects.create(
            service_principal=sp, project=None, resource_type="work_item", action="read"
        )
        # A different project is not a sibling either.
        project_orphan = ServiceScope.objects.create(
            service_principal=sp, project=project, resource_type="cycle", action="list"
        )
        # Neither is a *deleted* read row: the account has no live read grant.
        ServiceScope.objects.create(
            service_principal=sp,
            project=None,
            resource_type="stage",
            action="read",
            deleted_at=timezone.now(),
        )
        deleted_read_sibling = ServiceScope.objects.create(
            service_principal=sp, project=None, resource_type="stage", action="list"
        )

        self._run_migration(historical_apps)

        assert ServiceScope.objects.get(id=orphan.id).action == "list"
        assert ServiceScope.objects.get(id=project_orphan.id).action == "list"
        assert ServiceScope.objects.get(id=deleted_read_sibling.id).action == "list"

    def test_deletes_only_the_list_rows_that_have_a_read_sibling(
        self, db, workspace, create_user, historical_apps
    ):
        sp = ServicePrincipal.objects.create(
            workspace=workspace, owner=create_user, name="mixed-sp"
        )
        retirable = ServiceScope.objects.create(
            service_principal=sp, project=None, resource_type="work_item", action="list"
        )
        ServiceScope.objects.create(
            service_principal=sp, project=None, resource_type="work_item", action="read"
        )
        orphan = ServiceScope.objects.create(
            service_principal=sp, project=None, resource_type="cycle", action="list"
        )

        self._run_migration(historical_apps)

        assert not ServiceScope.all_objects.filter(id=retirable.id).exists()
        assert ServiceScope.objects.get(id=orphan.id).action == "list"

    def test_no_list_rows_is_a_noop(self, db, workspace, create_user, historical_apps):
        sp = ServicePrincipal.objects.create(
            workspace=workspace, owner=create_user, name="clean-sp"
        )
        kept = ServiceScope.objects.create(
            service_principal=sp, project=None, resource_type="work_item", action="create"
        )

        self._run_migration(historical_apps)

        assert ServiceScope.objects.get(id=kept.id).action == "create"


@pytest.mark.contract
class TestMigrationCommandStopsWritingListScopes:
    """The conversion no longer emits ``list`` rows (the source of the 28)."""

    def test_list_policy_with_read_sibling_is_skipped(
        self, db, workspace, create_user, project
    ):
        bot = _make_bot_user()
        account = _make_ai_account(workspace, create_user, bot)
        # Workspace-wide pair, list policy first in *creation* order (the
        # queryset orders by ``-created_at``, so it is last in iteration
        # order)...
        AIScopePolicy.objects.create(
            ai_account=account, project=None, resource_type="work_item", action="list"
        )
        AIScopePolicy.objects.create(
            ai_account=account, project=None, resource_type="work_item", action="read"
        )
        # ...and a project-scoped pair in the opposite order. The decision is
        # taken over the whole policy set, so neither order may leak a
        # ``list`` row.
        AIScopePolicy.objects.create(
            ai_account=account, project=project, resource_type="cycle", action="read"
        )
        AIScopePolicy.objects.create(
            ai_account=account, project=project, resource_type="cycle", action="list"
        )

        out, err = StringIO(), StringIO()
        call_command(
            "migrate_ai_accounts_to_service_principals", stdout=out, stderr=err
        )

        sp = ServicePrincipal.objects.get(id=account.id)
        assert ServiceScope.objects.filter(action="list").count() == 0
        rows = set(
            ServiceScope.objects.filter(service_principal=sp).values_list(
                "project_id", "resource_type", "action"
            )
        )
        assert rows == {
            (None, "work_item", "read"),
            (project.id, "cycle", "read"),
        }
        assert "2 skipped" in err.getvalue()
        record = AIAccountMigrationRecord.objects.get(ai_account=account)
        assert record.scopes_migrated == 2

    def test_list_policy_without_read_sibling_is_widened_to_read(
        self, db, workspace, create_user
    ):
        bot = _make_bot_user()
        account = _make_ai_account(workspace, create_user, bot)
        AIScopePolicy.objects.create(
            ai_account=account, project=None, resource_type="cycle", action="list"
        )

        out, err = StringIO(), StringIO()
        call_command(
            "migrate_ai_accounts_to_service_principals", stdout=out, stderr=err
        )

        sp = ServicePrincipal.objects.get(id=account.id)
        rows = list(
            ServiceScope.objects.filter(service_principal=sp).values_list(
                "resource_type", "action"
            )
        )
        assert rows == [("cycle", "read")]
        assert "1 widened" in err.getvalue()
        assert AIAccountMigrationRecord.objects.get(ai_account=account).scopes_migrated == 1

    def test_dry_run_agrees_with_apply_on_list_policies(
        self, db, workspace, create_user
    ):
        bot = _make_bot_user()
        account = _make_ai_account(workspace, create_user, bot)
        AIScopePolicy.objects.create(
            ai_account=account, project=None, resource_type="work_item", action="list"
        )
        AIScopePolicy.objects.create(
            ai_account=account, project=None, resource_type="work_item", action="read"
        )
        AIScopePolicy.objects.create(
            ai_account=account, project=None, resource_type="cycle", action="list"
        )

        out, err = StringIO(), StringIO()
        call_command(
            "migrate_ai_accounts_to_service_principals",
            "--dry-run",
            stdout=out,
            stderr=err,
        )

        # Same counts the apply path writes: 2 rows (read + widened read).
        assert "scopes=2" in out.getvalue()
        assert "1 skipped" in err.getvalue()
        assert "1 widened" in err.getvalue()
        assert ServicePrincipal.objects.count() == 0
        assert ServiceScope.objects.count() == 0
