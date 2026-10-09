# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: SP rotate-token endpoint.

Rotation revokes every existing service token and issues a fresh one; the
new secret is returned exactly once in the response body.
"""

import pytest
from rest_framework import status

from plane.db.models import APIToken, Project, ProjectMember, User, WorkspaceMember
from plane.service_principals.constants import PrincipalType
from plane.service_principals.models import ServicePrincipal


@pytest.fixture
def project(db, workspace, create_user):
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


@pytest.fixture
def member_user(db):
    user = User.objects.create(email="member@plane.so", username="member-user")
    user.set_password("password")
    user.save()
    return user


def sp_url(slug):
    return f"/api/workspaces/{slug}/service-principals/"


def rotate_url(slug, pk):
    return f"{sp_url(slug)}{pk}/rotate-token/"


@pytest.mark.contract
@pytest.mark.django_db
class TestServicePrincipalRotateToken:
    def test_rotate_returns_new_token_and_revokes_old(self, session_client, workspace):
        create = session_client.post(
            sp_url(workspace.slug), {"name": "rotate-bot"}, format="json"
        )
        sp = ServicePrincipal.objects.get(pk=create.data["id"])
        old_token = create.data["token"]
        assert old_token.startswith("plane_svc_")

        response = session_client.post(rotate_url(workspace.slug, sp.id))
        assert response.status_code == status.HTTP_200_OK
        new_token = response.data["token"]
        assert new_token.startswith("plane_svc_")
        assert new_token != old_token

        # Old token revoked, exactly one active service token remains
        tokens = APIToken.objects.filter(
            service_principal=sp, principal_type=PrincipalType.SERVICE
        )
        assert tokens.filter(token=old_token, is_active=False).exists()
        assert tokens.filter(is_active=True).count() == 1

    def test_rotate_rejected_for_inactive_sp(self, session_client, workspace):
        create = session_client.post(
            sp_url(workspace.slug), {"name": "inactive-bot"}, format="json"
        )
        sp_id = create.data["id"]
        session_client.patch(
            f"{sp_url(workspace.slug)}{sp_id}/",
            {"is_active": False},
            format="json",
        )
        response = session_client.post(rotate_url(workspace.slug, sp_id))
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_rotate_forbidden_for_non_admin(
        self, session_client, api_client, workspace, member_user
    ):
        create = session_client.post(
            sp_url(workspace.slug), {"name": "forbid-rotate"}, format="json"
        )
        WorkspaceMember.objects.create(
            workspace=workspace, member=member_user, role=15, is_active=True
        )
        api_client.force_authenticate(user=member_user)
        response = api_client.post(rotate_url(workspace.slug, create.data["id"]))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_rotate_preserves_scopes_and_grants(self, session_client, workspace, project):
        from plane.service_principals.models import ProjectGrant, ServiceScope

        create = session_client.post(
            sp_url(workspace.slug), {"name": "preserve-bot"}, format="json"
        )
        sp = ServicePrincipal.objects.get(pk=create.data["id"])
        # Add a scope and a grant
        session_client.put(
            f"/api/workspaces/{workspace.slug}/service-principals/{sp.id}/scopes/",
            {
                "scopes": [
                    {"project": None, "resource_type": "user", "action": "read"}
                ]
            },
            format="json",
        )
        session_client.put(
            f"/api/workspaces/{workspace.slug}/service-principals/{sp.id}/grants/",
            {
                "grants": [
                    {"project": str(project.id), "role_cap": 15, "is_active": True}
                ]
            },
            format="json",
        )

        response = session_client.post(rotate_url(workspace.slug, sp.id))
        assert response.status_code == status.HTTP_200_OK
        # Scope and grant rows are still there
        assert ServiceScope.objects.filter(service_principal=sp).count() == 1
        assert ProjectGrant.objects.filter(service_principal=sp).count() == 1

    def test_rotate_replaces_only_service_tokens(
        self, session_client, workspace, create_user
    ):
        """Rotation must not affect non-service tokens on the same user."""
        create = session_client.post(
            sp_url(workspace.slug), {"name": "isolation-bot"}, format="json"
        )
        sp = ServicePrincipal.objects.get(pk=create.data["id"])
        # A plain user token on the same owner — should not be touched
        personal = APIToken.objects.create(
            user=create_user, label="personal", principal_type=PrincipalType.USER
        )

        response = session_client.post(rotate_url(workspace.slug, sp.id))
        assert response.status_code == status.HTTP_200_OK
        personal.refresh_from_db()
        assert personal.is_active is True