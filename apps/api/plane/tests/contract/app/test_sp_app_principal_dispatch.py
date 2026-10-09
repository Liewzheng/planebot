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
    """A workspace MEMBER (role=15)."""
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
def guest_user(db, workspace):
    """A workspace GUEST (role=5)."""
    user = User.objects.create(
        email="guest@plane.so", username="dispatch-guest"
    )
    user.set_password("password")
    user.save()
    WorkspaceMember.objects.create(
        workspace=workspace, member=user, role=5, is_active=True
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

    def test_sp_assignable_on_advertises_toggle(
        self, session_client, workspace, sp
    ):
        """``sp_assignable=True`` flips the toggle flag exposed to the
        front end. The members block does NOT pre-list every SP — the
        human caller's view shows the human roster with email; the SP
        roster is consumed only by SP callers (see
        ``TestDispatchMembersExposure``)."""
        WorkspaceSPSettings.objects.create(
            workspace=workspace, sp_assignable=True
        )
        response = session_client.get(dispatch_url(workspace.slug))
        body = response.data
        assert body["sp_assignable"] is True
        # Human caller's view: only human rows; SPs are not in the
        # human-visible member list.
        kinds = {row["kind"] for row in body["members"]}
        assert kinds == {"user"}

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
    def test_admin_user_allowed_on_registered_actions(
        self, session_client, workspace, create_user
    ):
        """create_user is admin (role=20) — every registered (action,
        resource_type) pair is allowed. The matrix only lists pairs that
        map to a real Plane action; unlisted pairs are intentionally
        deny. See the matrix comment in
        ``plane.core.authz.actions.ACTION_REQUIRED_ROLE`` for the
        rationale on which pairs are registered."""
        from plane.core.authz import required_role as _required_role
        from plane.core.authz import STANDARD_ACTIONS

        response = session_client.get(dispatch_url(workspace.slug))
        body = response.data
        for resource_type, perms in body["permissions"].items():
            for action in STANDARD_ACTIONS:
                if _required_role(action, resource_type) is None:
                    continue
                assert perms[action]["allowed"] is True, (
                    f"admin expected allow: {resource_type}.{action} = "
                    f"{perms[action]}"
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


# --------------------------------------------------------------------------- #
# P1-2 — human permission matrix is role-true, not blanket-allow              #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestDispatchHumanPermissionMatrix:
    """The dispatch endpoint must NOT blanket-allow humans. The authz
    engine short-circuits non-``ServicePrincipal_`` principals; the
    dispatch endpoint derives per-action allow from the caller's actual
    workspace role against ``ACTION_REQUIRED_ROLE`` so a guest doesn't
    see ``delete.allowed=true`` on work_items.
    """

    def test_guest_sees_read_allowed_but_delete_denied_on_work_item(
        self, db, workspace, guest_user
    ):
        """guest_user is a workspace GUEST (role=5). DELETE on work_item
        needs MEMBER (15) — the dispatch endpoint must report deny."""
        from rest_framework.test import APIClient as _Client

        client = _Client()
        client.force_authenticate(user=guest_user)
        response = client.get(dispatch_url(workspace.slug))
        assert response.status_code == status.HTTP_200_OK
        body = response.data

        assert body["principal"]["kind"] == "user"
        assert body["principal"]["workspace_role"] == 5
        perms = body["permissions"]["work_item"]
        # READ on work_item needs GUEST — allowed.
        assert perms["read"]["allowed"] is True
        # LIST also GUEST — allowed.
        assert perms["list"]["allowed"] is True
        # DELETE on work_item needs MEMBER — denied.
        assert perms["delete"]["allowed"] is False
        # CREATE on work_item needs MEMBER — denied.
        assert perms["create"]["allowed"] is False
        # UPDATE on work_item needs MEMBER — denied.
        assert perms["update"]["allowed"] is False

    def test_member_role_can_update_but_cannot_delete(
        self, db, workspace, second_user
    ):
        """second_user is MEMBER (role=15). The matrix requires MEMBER
        for both UPDATE and DELETE on work_item (both 15); a MEMBER is
        allowed for both. The real denials show up on resources where
        DELETE needs ADMIN — e.g. project (DELETE → ADMIN)."""
        from rest_framework.test import APIClient as _Client

        client = _Client()
        client.force_authenticate(user=second_user)
        response = client.get(dispatch_url(workspace.slug))
        body = response.data
        assert body["principal"]["workspace_role"] == 15, body
        perms = body["permissions"]["work_item"]
        # MEMBER (15) >= MEMBER (15) for both update and delete.
        assert perms["update"]["allowed"] is True
        assert perms["delete"]["allowed"] is True
        # project resource: DELETE needs ADMIN (20), so a MEMBER cannot
        # delete projects.
        assert body["permissions"]["project"]["delete"]["allowed"] is False

    def test_admin_sees_all_registered_actions_allowed(
        self, session_client, workspace, create_user
    ):
        """create_user is admin (role=20) — every registered (action,
        resource) pair is allowed. The matrix only lists pairs that map
        to a real Plane action; unlisted pairs are intentionally deny
        (not every resource supports every action)."""
        from plane.core.authz import required_role as _required_role
        from plane.core.authz import STANDARD_ACTIONS

        response = session_client.get(dispatch_url(workspace.slug))
        body = response.data
        for resource_type, perms in body["permissions"].items():
            for action in STANDARD_ACTIONS:
                if action == "list":
                    # LIST is engine-internal; if READ is registered, LIST
                    # is allowed too.
                    if _required_role("read", resource_type) is not None:
                        assert perms[action]["allowed"] is True, (
                            f"admin expected allow: {resource_type}.{action}"
                        )
                    continue
                if _required_role(action, resource_type) is None:
                    # Unregistered pair — deny is intentional.
                    continue
                assert perms[action]["allowed"] is True, (
                    f"admin expected allow: {resource_type}.{action}"
                )


# --------------------------------------------------------------------------- #
# P1-3 — members block: SPs see nothing, guests lose email, admins keep it    #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestDispatchMembersExposure:
    """Privacy contract for the members block:

    * Service principals: empty list (default-deny on roster). When
      ``sp_assignable`` is on, the SP sees its own row only — not the
      roster of human members.
    * Human guests: roster present, ``email=None`` for every row.
    * Human admins / members: roster with real email.

    Regression test for P1-3.
    """

    def test_sp_caller_sees_empty_members_when_sp_assignable_off(
        self, sp, workspace
    ):
        token = APIToken.objects.create(
            user=sp.owner,
            label="svc:exposure-test",
            user_type=1,
            is_service=True,
            principal_type=PrincipalType.SERVICE,
            service_principal=sp,
            workspace=workspace,
            token="plane_svc_exposure_off",
        )
        client = APIClient()
        client.credentials(HTTP_X_API_KEY=token.token)
        response = client.get(dispatch_url(workspace.slug))
        body = response.data
        # SP caller: empty members list (default contract).
        assert body["members"] == [], (
            f"SP caller must see empty members, got {body['members']!r}"
        )

    def test_sp_caller_sees_only_own_row_when_sp_assignable_on(
        self, sp, workspace, second_user
    ):
        """With ``sp_assignable=True`` the SP caller sees its own row —
        not the human roster, not other SPs."""
        WorkspaceSPSettings.objects.create(
            workspace=workspace, sp_assignable=True
        )
        # Add a second SP — must NOT appear in the caller's view.
        ServicePrincipal.objects.create(
            workspace=workspace, owner=workspace.owner, name="other-bot"
        )
        token = APIToken.objects.create(
            user=sp.owner,
            label="svc:exposure-on",
            user_type=1,
            is_service=True,
            principal_type=PrincipalType.SERVICE,
            service_principal=sp,
            workspace=workspace,
            token="plane_svc_exposure_on",
        )
        client = APIClient()
        client.credentials(HTTP_X_API_KEY=token.token)
        response = client.get(dispatch_url(workspace.slug))
        body = response.data
        # Only the calling SP itself.
        assert len(body["members"]) == 1
        assert body["members"][0]["kind"] == "service"
        assert body["members"][0]["id"] == str(sp.id)
        # No human members leak into an SP caller's view.
        assert all(row["kind"] == "service" for row in body["members"])

    def test_human_guest_sees_no_email(
        self, db, workspace, guest_user
    ):
        """A guest sees the roster but every ``email`` field is None —
        mirrors ``WorkSpaceMemberViewSet.list`` which withholds email at
        guest level."""
        from rest_framework.test import APIClient as _Client

        client = _Client()
        client.force_authenticate(user=guest_user)
        response = client.get(dispatch_url(workspace.slug))
        body = response.data
        assert body["principal"]["workspace_role"] == 5
        # Every member row has email=None.
        for row in body["members"]:
            assert row["email"] is None, (
                f"guest must not see email: {row['email']!r}"
            )
        # But the roster itself is non-empty.
        assert len(body["members"]) >= 1

    def test_human_admin_sees_email(
        self, session_client, workspace, create_user
    ):
        """Admin sees the roster WITH email — mirrors the role-aware
        split of ``WorkSpaceMemberViewSet.list``."""
        response = session_client.get(dispatch_url(workspace.slug))
        body = response.data
        email_rows = [r for r in body["members"] if r["email"]]
        # Admin sees at least one real email (the owner is a member).
        assert any(
            r["email"] for r in body["members"]
        ), f"admin must see email: {body['members']!r}"
        # And the admin themselves is present.
        assert any(r["id"] == str(create_user.id) for r in body["members"])
