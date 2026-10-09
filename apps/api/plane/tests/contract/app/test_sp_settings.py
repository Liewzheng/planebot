# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: WorkspaceSPSettings endpoint (GET / PATCH sp-settings).

The endpoint backs M11's sp_assignable toggle UI. The lazy GET (no row →
``{"sp_assignable": false}``) and the idempotent upsert PATCH are part of the
contract M11's frontend depends on; the admin-only / SP-rejected guards close
the two authz holes a future wire-up would otherwise expose.
"""

import pytest
from rest_framework import status

from plane.db.models import APIToken, User, WorkspaceMember
from plane.service_principals.constants import PrincipalType
from plane.service_principals.models import ServicePrincipal, WorkspaceSPSettings


def sp_settings_url(slug):
    return f"/api/workspaces/{slug}/sp-settings/"


@pytest.fixture
def member_user(db):
    user = User.objects.create(email="member@plane.so", username="member-user")
    user.set_password("password")
    user.save()
    return user


@pytest.mark.contract
@pytest.mark.django_db
class TestWorkspaceSPSettingsEndpoint:
    def test_get_returns_default_when_no_row(self, session_client, workspace):
        """GET on a fresh workspace returns the model default without writing."""
        assert not WorkspaceSPSettings.objects.filter(workspace=workspace).exists()

        response = session_client.get(sp_settings_url(workspace.slug))

        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"sp_assignable": False}
        # GET must NOT materialize the row — the lazy default is part of the contract.
        assert not WorkspaceSPSettings.objects.filter(workspace=workspace).exists()

    def test_patch_roundtrip_persists(self, session_client, workspace):
        """PATCH upserts and the GET reflects the new value."""
        assert WorkspaceSPSettings.objects.count() == 0

        response = session_client.patch(
            sp_settings_url(workspace.slug),
            {"sp_assignable": True},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["sp_assignable"] is True

        # Exactly one row, pinned to this workspace.
        rows = WorkspaceSPSettings.objects.filter(workspace=workspace)
        assert rows.count() == 1
        assert rows.first().sp_assignable is True

        # Subsequent GET reads the same flag.
        get_response = session_client.get(sp_settings_url(workspace.slug))
        assert get_response.status_code == status.HTTP_200_OK
        assert get_response.data["sp_assignable"] is True

    def test_repeated_patch_is_idempotent(self, session_client, workspace):
        """Repeated PATCH with the same value is idempotent — one row, no extras."""
        for _ in range(3):
            response = session_client.patch(
                sp_settings_url(workspace.slug),
                {"sp_assignable": True},
                format="json",
            )
            assert response.status_code == status.HTTP_200_OK
            assert response.data["sp_assignable"] is True

        # The row is unique per workspace — no duplicates materialize.
        assert WorkspaceSPSettings.objects.filter(workspace=workspace).count() == 1

        # Toggling back and forth is also a single row.
        response = session_client.patch(
            sp_settings_url(workspace.slug),
            {"sp_assignable": False},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert WorkspaceSPSettings.objects.filter(workspace=workspace).count() == 1

    def test_get_and_patch_rejected_for_non_admin(
        self, session_client, api_client, workspace, member_user
    ):
        """Workspace members (non-admin) are forbidden from both verbs."""
        WorkspaceMember.objects.create(
            workspace=workspace, member=member_user, role=15, is_active=True
        )
        api_client.force_authenticate(user=member_user)

        get_response = api_client.get(sp_settings_url(workspace.slug))
        assert get_response.status_code == status.HTTP_403_FORBIDDEN

        patch_response = api_client.patch(
            sp_settings_url(workspace.slug),
            {"sp_assignable": True},
            format="json",
        )
        assert patch_response.status_code == status.HTTP_403_FORBIDDEN

    def test_sp_token_rejected_on_get_and_patch(
        self, session_client, api_client, workspace, create_user
    ):
        """An SP-issued token must not be allowed to read or toggle this flag.

        Without the ``_reject_if_sp_token`` guard the SP would slip past
        ``allow_permission(ADMIN)`` because its owner is the admin — so this
        regression locks the SP-rejected boundary in place.
        """
        sp = ServicePrincipal.objects.create(
            workspace=workspace,
            owner=create_user,
            name="self-toggle-bot",
        )
        svc_token = APIToken.objects.create(
            user=create_user,
            label=f"svc:{sp.name}",
            user_type=1,
            is_service=True,
            principal_type=PrincipalType.SERVICE,
            service_principal=sp,
            workspace=workspace,
            token="plane_svc_test_reject_aaaaaaaa",
        )

        # An admin session + an SP X-Api-Key header is the worst-case path:
        # allow_permission(ADMIN) would pass because the user is admin, so the
        # SP-token guard is what must catch this.
        api_client.force_authenticate(user=create_user)
        api_client.credentials(HTTP_X_API_KEY=svc_token.token)

        get_response = api_client.get(sp_settings_url(workspace.slug))
        assert get_response.status_code == status.HTTP_403_FORBIDDEN
        assert "service principals" in get_response.data["error"].lower()

        patch_response = api_client.patch(
            sp_settings_url(workspace.slug),
            {"sp_assignable": True},
            format="json",
        )
        assert patch_response.status_code == status.HTTP_403_FORBIDDEN
        assert "service principals" in patch_response.data["error"].lower()

        # No row was created by either rejected call.
        assert not WorkspaceSPSettings.objects.filter(workspace=workspace).exists()
