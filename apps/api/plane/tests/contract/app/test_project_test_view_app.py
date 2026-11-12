# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for the project-level ``test_view`` feature flag (PLANE-130).

The project "Tests" surface is gated by a per-project switch, exactly like
``module_view`` / ``cycle_view`` / ``page_view`` / ``intake_view``.  These
tests pin the backend contract:

* the flag defaults to ``True`` — a project created without it keeps its
  tests reachable, and a project row that already existed when the
  migration ran comes out of the migration with ``test_view = True``
  (a ``False`` default would make live test cases and batches disappear);
* an admin can turn the flag off and on again through the app surface and
  the value read back is the value written;
* flipping the flag on one project leaves every other project untouched;
* a non-admin member cannot flip it.

The v1 surface is exercised here as well: the mission scope allows new
contract tests under ``plane/tests/contract/app/`` only, and the v1
project serializer carries the same ``*_view`` switches.
"""

import importlib

import pytest
from django.db import connection
from django.db.migrations import AddField
from rest_framework import status

from plane.db.models import Project, ProjectMember, User, WorkspaceMember

MIGRATION_MODULE = "plane.db.migrations.0134_project_test_view"
PREVIOUS_MIGRATION = "0133_testcase_related_feature_testcase_remarks_and_more"


def load_migration():
    return importlib.import_module(MIGRATION_MODULE)


@pytest.fixture
def project(db, workspace, create_user):
    """A project the session user administers."""
    proj = Project.objects.create(
        name="Test Flag Project",
        identifier="TFP",
        workspace=workspace,
        created_by=create_user,
        network=2,
    )
    ProjectMember.objects.create(workspace=workspace, project=proj, member=create_user, role=20, is_active=True)
    return proj


@pytest.fixture
def other_project(db, workspace, create_user):
    """A second project in the same workspace, also administered by the session user."""
    proj = Project.objects.create(
        name="Untouched Project",
        identifier="UTP",
        workspace=workspace,
        created_by=create_user,
        network=2,
    )
    ProjectMember.objects.create(workspace=workspace, project=proj, member=create_user, role=20, is_active=True)
    return proj


@pytest.fixture
def member_user(db, workspace):
    """A plain workspace member (role 15) — no admin seat anywhere."""
    user = User.objects.create_user(email="member@example.com", username="member")
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=15, is_active=True)
    return user


class TestProjectTestFlagBase:
    def get_project_url(self, workspace_slug: str, pk=None) -> str:
        base_url = f"/api/workspaces/{workspace_slug}/projects/"
        return f"{base_url}{pk}/" if pk else base_url


@pytest.mark.contract
class TestProjectTestViewApp(TestProjectTestFlagBase):
    """The app surface (``/api/workspaces/...``) must read and write the flag."""

    @pytest.mark.django_db
    def test_new_project_defaults_to_test_view_true(self, session_client, workspace):
        """A project created without the flag keeps it on."""
        response = session_client.post(
            self.get_project_url(workspace.slug),
            {"name": "Fresh Project", "identifier": "FRP"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["test_view"] is True

        project = Project.objects.get(identifier="FRP")
        assert project.test_view is True

    @pytest.mark.django_db
    def test_patch_test_view_false_is_read_back(self, session_client, workspace, project):
        """Turning the flag off is persisted and read back as off."""
        url = self.get_project_url(workspace.slug, pk=project.id)

        response = session_client.patch(url, {"test_view": False}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["test_view"] is False

        project.refresh_from_db()
        assert project.test_view is False

        read_back = session_client.get(url)
        assert read_back.status_code == status.HTTP_200_OK
        assert read_back.json()["test_view"] is False

    @pytest.mark.django_db
    def test_patch_test_view_true_restores_the_flag(self, session_client, workspace, project):
        """Turning the flag back on restores the previous state."""
        url = self.get_project_url(workspace.slug, pk=project.id)

        session_client.patch(url, {"test_view": False}, format="json")
        response = session_client.patch(url, {"test_view": True}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["test_view"] is True

        project.refresh_from_db()
        assert project.test_view is True

        read_back = session_client.get(url)
        assert read_back.status_code == status.HTTP_200_OK
        assert read_back.json()["test_view"] is True

    @pytest.mark.django_db
    def test_toggle_does_not_affect_other_projects(self, session_client, workspace, project, other_project):
        """One project's flag is its own — siblings keep the default."""
        url = self.get_project_url(workspace.slug, pk=project.id)

        response = session_client.patch(url, {"test_view": False}, format="json")
        assert response.status_code == status.HTTP_200_OK

        project.refresh_from_db()
        other_project.refresh_from_db()
        assert project.test_view is False
        assert other_project.test_view is True

    @pytest.mark.django_db
    def test_non_admin_cannot_change_test_view(self, session_client, workspace, project, member_user):
        """A plain member is refused — same gate as the other feature switches."""
        ProjectMember.objects.create(workspace=workspace, project=project, member=member_user, role=15, is_active=True)
        session_client.force_authenticate(user=member_user)

        url = self.get_project_url(workspace.slug, pk=project.id)
        response = session_client.patch(url, {"test_view": False}, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN

        project.refresh_from_db()
        assert project.test_view is True

    @pytest.mark.django_db
    def test_project_list_exposes_test_view(self, session_client, workspace, project):
        """The project list the UI reads must carry the flag."""
        response = session_client.get(self.get_project_url(workspace.slug))

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]["test_view"] is True


@pytest.mark.contract
class TestProjectTestViewV1(TestProjectTestFlagBase):
    """The v1 surface exposes the same ``*_view`` switches — it must carry the
    new one too.  These tests live under ``contract/app/`` because the
    mission scope allows new contract files there only."""

    def get_v1_project_url(self, workspace_slug: str, pk=None) -> str:
        base_url = f"/api/v1/workspaces/{workspace_slug}/projects/"
        return f"{base_url}{pk}/" if pk else base_url

    @pytest.mark.django_db
    def test_v1_reads_and_writes_test_view(self, api_key_client, workspace, project):
        url = self.get_v1_project_url(workspace.slug, pk=project.id)

        response = api_key_client.patch(url, {"test_view": False}, format="json")

        assert response.status_code == status.HTTP_200_OK, f"Got {response.status_code}: {response.data!r}"
        assert response.json()["test_view"] is False

        project.refresh_from_db()
        assert project.test_view is False

        read_back = api_key_client.get(url)
        assert read_back.status_code == status.HTTP_200_OK
        assert read_back.json()["test_view"] is False


@pytest.mark.contract
class TestProjectTestViewMigration:
    """The migration must add the column with ``default=True`` so that the
    projects that already existed keep their tests visible."""

    def test_migration_adds_test_view_with_default_true(self):
        migration = load_migration()

        add_field_ops = [op for op in migration.Migration.operations if isinstance(op, AddField)]
        assert len(add_field_ops) == 1, migration.Migration.operations

        operation = add_field_ops[0]
        assert operation.model_name == "project"
        assert operation.name == "test_view"
        assert operation.field.default is True
        assert operation.field.null is False

        assert ("db", PREVIOUS_MIGRATION) in migration.Migration.dependencies

    @pytest.mark.django_db(transaction=True)
    def test_row_that_predates_the_column_reads_true_after_the_migration(self, workspace, create_user):
        """A project row that already existed when the migration ran must come
        out of it with ``test_view = True``.

        The row is created first, the column is then dropped (which is what
        the pre-migration table looks like) and the migration's own
        ``AddField`` is applied to the real table — exactly the sequence a
        deployment performs.  A ``default=False`` migration would leave the
        live project without its tests.
        """
        legacy_project = Project.objects.create(
            name="Legacy Project",
            identifier="LEG",
            workspace=workspace,
            created_by=create_user,
            network=2,
        )

        operation = [op for op in load_migration().Migration.operations if isinstance(op, AddField)][0]
        field = operation.field.clone()
        field.set_attributes_from_name("test_view")

        table = Project._meta.db_table

        with connection.cursor() as cursor:
            cursor.execute(f'ALTER TABLE "{table}" DROP COLUMN "test_view"')

        with connection.schema_editor() as editor:
            editor.add_field(Project, field)

        legacy_project.refresh_from_db()
        assert legacy_project.test_view is True
