# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: SP foundation models and token prefix.

Pure-model checks that do not need the endpoint surface: SP tables exist and
shape correctly, APIToken gained the new columns, service tokens carry the
``plane_svc_`` prefix.
"""

import pytest

from plane.db.models import APIToken
from plane.db.models.api import generate_service_token
from plane.service_principals.models import (
    ProjectGrant,
    ServicePrincipal,
    ServiceScope,
    WorkspaceSPSettings,
)
from plane.service_principals.constants import (
    ACTION_CHOICES,
    PrincipalType,
    RESOURCE_CHOICES,
    SERVICE_TOKEN_PREFIX,
)


@pytest.fixture
def project(db, workspace, create_user):
    from plane.db.models import Project, ProjectMember

    project = Project.objects.create(
        name="Test Project",
        identifier="TP",
        workspace=workspace,
        created_by=create_user,
    )
    ProjectMember.objects.create(
        project=project, member=create_user, role=20, is_active=True
    )
    return project


@pytest.mark.contract
@pytest.mark.django_db
class TestServicePrincipalModels:
    def test_service_principal_columns(self, workspace, create_user):
        sp = ServicePrincipal.objects.create(
            workspace=workspace, owner=create_user, name="review-bot"
        )
        # Round-trip every field
        sp.refresh_from_db()
        assert sp.workspace_id == workspace.id
        assert sp.owner_id == create_user.id
        assert sp.name == "review-bot"
        assert sp.description == ""
        assert sp.avatar == ""
        assert sp.is_active is True
        assert sp.id is not None

    def test_service_principal_optional_metadata(self, workspace, create_user):
        sp = ServicePrincipal.objects.create(
            workspace=workspace,
            owner=create_user,
            name="bot",
            description="does things",
            avatar="https://example.com/a.png",
            is_active=False,
        )
        sp.refresh_from_db()
        assert sp.description == "does things"
        assert sp.avatar == "https://example.com/a.png"
        assert sp.is_active is False

    def test_service_principal_not_in_workspace_member(self, workspace, create_user):
        """SPs are not User rows; they must not appear in WorkspaceMember."""
        from plane.db.models import WorkspaceMember

        sp = ServicePrincipal.objects.create(
            workspace=workspace, owner=create_user, name="bot-x"
        )
        # WorkspaceMember is keyed by User.id; SPs have no User backing row,
        # so an SP id can never appear as a WorkspaceMember.member_id.
        assert not WorkspaceMember.objects.filter(workspace=workspace, member_id=sp.id).exists()

    def test_service_scope_soft_delete_frees_slot(self, workspace, create_user):
        """After soft-deleting a scope row, the same shape can be re-created."""
        sp = ServicePrincipal.objects.create(workspace=workspace, owner=create_user, name="sp1")
        scope = ServiceScope.objects.create(
            service_principal=sp, project=None, resource_type="work_item", action="read"
        )
        # Soft-delete (the default Manager's delete() sets deleted_at)
        scope.delete()
        # A new active row with the same shape is now allowed
        replacement = ServiceScope.objects.create(
            service_principal=sp, project=None, resource_type="work_item", action="read"
        )
        assert replacement.id != scope.id
        assert replacement.deleted_at is None

    def test_project_grant_soft_delete_frees_slot(
        self, workspace, create_user, project
    ):
        sp = ServicePrincipal.objects.create(workspace=workspace, owner=create_user, name="sp2")
        grant = ProjectGrant.objects.create(
            service_principal=sp, project=project, role_cap=15, is_active=True
        )
        grant.delete()
        # Re-create with a different role_cap
        replacement = ProjectGrant.objects.create(
            service_principal=sp, project=project, role_cap=20, is_active=True
        )
        assert replacement.id != grant.id
        assert replacement.role_cap == 20

    def test_workspace_sp_settings_default(self, workspace):
        # First row created lazily — sp_assignable defaults to False
        settings = WorkspaceSPSettings.objects.create(workspace=workspace)
        assert settings.sp_assignable is False

    def test_workspace_sp_settings_unique(self, workspace):
        """One row per workspace."""
        from django.db import IntegrityError

        WorkspaceSPSettings.objects.create(workspace=workspace)
        with pytest.raises(IntegrityError):
            WorkspaceSPSettings.objects.create(workspace=workspace)

    def test_scope_and_grant_cascade_with_sp_hard_delete(
        self, workspace, create_user, project
    ):
        """Hard-deleting an SP removes its scope and grant rows via FK CASCADE."""
        sp = ServicePrincipal.objects.create(workspace=workspace, owner=create_user, name="sp3")
        ServiceScope.objects.create(
            service_principal=sp, project=None, resource_type="comment", action="read"
        )
        ProjectGrant.objects.create(
            service_principal=sp, project=project, role_cap=15, is_active=True
        )
        sp_id = sp.id
        # Hard-delete bypasses the soft-delete manager
        ServicePrincipal.all_objects.filter(pk=sp_id).delete()
        assert not ServiceScope.objects.filter(service_principal_id=sp_id).exists()
        assert not ProjectGrant.objects.filter(service_principal_id=sp_id).exists()


@pytest.mark.contract
@pytest.mark.django_db
class TestAPITokenPrincipalFields:
    def test_apitoken_has_principal_type_default(self, workspace, create_user):
        token = APIToken.objects.create(user=create_user, label="user-token")
        token.refresh_from_db()
        assert token.principal_type == PrincipalType.USER
        assert token.service_principal_id is None

    def test_apitoken_links_to_service_principal(self, workspace, create_user):
        sp = ServicePrincipal.objects.create(workspace=workspace, owner=create_user, name="bot-t")
        token = APIToken.objects.create(
            user=create_user,
            label="svc:bot-t",
            principal_type=PrincipalType.SERVICE,
            service_principal=sp,
            workspace=workspace,
        )
        token.refresh_from_db()
        assert token.principal_type == PrincipalType.SERVICE
        assert token.service_principal_id == sp.id

    def test_service_token_prefix(self):
        """``plane_svc_`` is the credential-layer prefix for SP tokens."""
        token = generate_service_token()
        assert token.startswith(SERVICE_TOKEN_PREFIX)
        assert token.startswith("plane_svc_")

    def test_user_token_prefix_unchanged(self):
        """User tokens keep the legacy ``plane_api_`` prefix."""
        from plane.db.models.api import generate_token

        token = generate_token()
        assert token.startswith("plane_api_")

    def test_apitoken_cascade_with_service_principal_hard_delete(
        self, workspace, create_user
    ):
        sp = ServicePrincipal.objects.create(workspace=workspace, owner=create_user, name="bot-c")
        APIToken.objects.create(
            user=create_user,
            label="svc:bot-c",
            principal_type=PrincipalType.SERVICE,
            service_principal=sp,
            workspace=workspace,
        )
        sp_id = sp.id
        # Hard-delete to exercise FK CASCADE
        ServicePrincipal.all_objects.filter(pk=sp_id).delete()
        # Tokens attached to the SP are removed via CASCADE
        assert not APIToken.objects.filter(service_principal_id=sp_id).exists()


@pytest.mark.contract
@pytest.mark.django_db
class TestChoices:
    def test_scope_resource_choices_include_work_item(self):
        assert ("work_item", "Work Item") in RESOURCE_CHOICES

    def test_action_choices_include_read_create_update_delete(self):
        labels = [c[0] for c in ACTION_CHOICES]
        for required in ("read", "create", "update", "delete"):
            assert required in labels