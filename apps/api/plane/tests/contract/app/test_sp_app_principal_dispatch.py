# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: unified principal-dispatch endpoint.

The dispatch endpoint is the SP-aware replacement for the front end's
client-side authorization predicates. It returns:

* a ``principal`` block — kind (user/service/anonymous) + id + workspace role;
* a ``members`` list — visible humans + (optionally) SPs depending on
  ``sp_assignable``;
* ``sp_assignable`` — the workspace toggle, surfaced so the picker UI
  can hide the SP rows without a second round-trip;
* ``permissions`` — per-resource-type effective permission matrix.

These tests pin the shape M10's web client will consume.
"""

from __future__ import annotations

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import APIToken, Project, User, WorkspaceMember
from plane.service_principals.constants import PrincipalType
from plane.service_principals.models import (
    ServicePrincipal,
    ServiceScope,
    WorkspaceSPSettings,
)


@pytest.fixture
def sp(db, workspace, create_user):
    """A real SP used for the dispatch endpoint tests."""
    return ServicePrincipal.objects.create(
        workspace=workspace,
        owner=create_user,
        name="dispatch-bot",
    )


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #


@pytest.fixture
def second_user(db, workspace):
    user = User.objects.create(
        email="member@plane.so", username="dispatch-member"
    )
    user.set_password("password")
    user.save()
    WorkspaceMember.objects.create(
        workspace=workspace, member=user, role=15, is_active=True
    )
    return user


@pytest.fixture
def bot_user(db, workspace):
    """Bot user — must NEVER appear in the visible members list."""
    from plane.db.models import User as UserModel

    bot = UserModel.objects.create(
        email="bot@plane.so", username="dispatch-bot", is_bot=True
    )
    bot.set_password("password")
    bot.save()
    WorkspaceMember.objects.create(
        workspace=workspace, member=bot, role=15, is_active=True
    )
    return bot


def dispatch_url(slug):
    return f"/api/workspaces/{slug}/principal/permissions/"


# --------------------------------------------------------------------------- #
# Visible-member predicate                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestDispatchVisibleMembers:
    def test_default_sp_assignable_hides_sps(
        self, session_client, workspace, sp
    ):
        """``sp_assignable`` defaults to False — the SP rows do not appear."""
        # ``sp`` fixture already created an SP for the workspace owner.
        response = session_client.get(dispatch_url(workspace.slug))

        response = session_client.get(dispatch_url(workspace.slug))
        assert response.status_code == status.HTTP_200_OK
        body = response.data

        assert body["sp_assignable"] is False
        kinds = {row["kind"] for row in body["members"]}
        assert kinds == {"user"}  # no "service" rows

    def test_sp_assignable_on_includes_sps(
        self, session_client, workspace, sp
    ):
        WorkspaceSPSettings.objects.create(
            workspace=workspace, sp_assignable=True
        )
        response = session_client.get(dispatch_url(workspace.slug))
        body = response.data
        assert body["sp_assignable"] is True
        kinds = [row["kind"] for row in body["members"]]
        assert "service" in kinds

    def test_bot_users_excluded(
        self, session_client, workspace, bot_user
    ):
        response = session_client.get(dispatch_url(workspace.slug))
        body = response.data
        bot_ids = {bot_user.id}
        member_ids = {row["id"] for row in body["members"] if row["kind"] == "user"}
        assert bot_ids.isdisjoint(member_ids), (
            f"Bot user must not appear in visible members: {member_ids}"
        )

    def test_inactive_sp_skipped_when_assignable(
        self, session_client, workspace, create_user
    ):
        """An inactive SP must not surface even with sp_assignable=True —
        the active flag is the source of truth for picker visibility."""
        WorkspaceSPSettings.objects.create(
            workspace=workspace, sp_assignable=True
        )
        inactive = ServicePrincipal.objects.create(
            workspace=workspace,
            owner=create_user,
            name="quiet-bot",
            is_active=False,
        )
        response = session_client.get(dispatch_url(workspace.slug))
        body = response.data
        sp_ids = {row["id"] for row in body["members"] if row["kind"] == "service"}
        assert str(inactive.id) not in sp_ids


# --------------------------------------------------------------------------- #
# Effective permissions                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestDispatchPermissions:
    def test_admin_user_allowed_on_everything(
        self, session_client, workspace, create_user
    ):
        """create_user is admin (20) of the workspace — every standard
        action passes."""
        response = session_client.get(dispatch_url(workspace.slug))
        body = response.data
        for resource_type, perms in body["permissions"].items():
            for action, info in perms.items():
                assert info["allowed"] is True, (
                    f"admin expected allow: {resource_type}.{action} = {info}"
                )

    def test_permissions_block_covers_standard_resource_types(
        self, session_client, workspace
    ):
        response = session_client.get(dispatch_url(workspace.slug))
        body = response.data
        expected = {
            "project",
            "work_item",
            "cycle",
            "module",
            "page",
            "state",
            "label",
            "estimate",
            "intake",
            "comment",
            "asset",
            "sticky",
            "member",
            "user",
        }
        assert set(body["permissions"].keys()) == expected

    def test_sp_permissions_reflect_scope_rows(
        self, workspace, create_user
    ):
        sp = ServicePrincipal.objects.create(
            workspace=workspace, owner=create_user, name="scope-bot"
        )
        ServiceScope.objects.create(
            service_principal=sp,
            project=None,
            resource_type="work_item",
            action="read",
        )
        token = APIToken.objects.create(
            user=create_user,
            label="svc:scope-bot",
            user_type=1,
            is_service=True,
            principal_type=PrincipalType.SERVICE,
            service_principal=sp,
            workspace=workspace,
            token="plane_svc_dispatch_test",
        )
        client = APIClient()
        client.credentials(HTTP_X_API_KEY=token.token)

        response = client.get(dispatch_url(workspace.slug))
        assert response.status_code == status.HTTP_200_OK
        body = response.data
        assert body["principal"]["kind"] == "service"
        # Workspace-level read of work_item allowed via the workspace-wide
        # scope row. Engine still applies the role cap & owner floor; the
        # only signal the front end needs is the ``allowed`` flag.
        assert body["permissions"]["work_item"]["read"]["allowed"] is True
