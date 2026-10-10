# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: ``GET /api/v1/users/me/`` returns a guarded self-identity
payload to service principals — never the human owner's profile.

Closes reviewer-m8 P2-4: under SP wiring the endpoint would otherwise
"print the human owner's profile to whatever bot holds the SP token"
(see ``apps/api/plane/api/views/base.py:95-102``). The view is annotated
``resource_type = "user"`` so the authorize() chain gates the request;
the matrix's existing ``(READ, "user") = ROLE_GUEST`` entry (M7) is the
only engine-side ground this relies on — the SP payload is hand-built
inside the view precisely so an unguarded "return the owner row" ``fix``
cannot accidentally reintroduce owner leakage.
"""

import pytest
from rest_framework import status

from plane.db.models.api import APIToken
from plane.service_principals.constants import PrincipalType
from plane.service_principals.models import ServicePrincipal, ServiceScope


USERS_ME_URL = "/api/v1/users/me/"

# Fields the human ``UserLiteSerializer`` exposes that are semantically
# owner-only. Banning these as keys anywhere in an SP response — nested
# or top-level — is what makes the owner-leak guard.
_OWNER_ONLY_KEYS = frozenset(
    {"first_name", "last_name", "email", "avatar", "avatar_url", "display_name"}
)


def _make_sp(workspace, owner, name="bot-self", is_active=True):
    """Create a ``ServicePrincipal`` and a matching ``plane_svc_`` token.

    Mirrors the helpers in ``test_sp_v1_default_deny.py`` and
    ``test_sp_v1_authentication.py`` so the credential layer is exercised
    identically across the SP test set.
    """
    sp = ServicePrincipal.objects.create(
        workspace=workspace,
        owner=owner,
        name=name,
        is_active=is_active,
    )
    APIToken.objects.create(
        label=f"svc:{name}",
        token=f"plane_svc_token_{name}",
        user=owner,
        principal_type=PrincipalType.SERVICE,
        service_principal=sp,
        workspace=workspace,
    )
    return sp


def _grant_user_read_scope(sp):
    """Provision the only scope row ``users/me/`` will accept for an SP.

    ``user`` is a workspace-level resource (Q4 guard) so the scope row is
    workspace-wide (``project=None``); the engine skips the ProjectGrant
    step for workspace-level resources and only requires an active
    workspace membership on the owner.
    """
    return ServiceScope.objects.create(
        service_principal=sp,
        project=None,
        resource_type="user",
        action="read",
    )


def _assert_no_owner_data(body, owner):
    """Recursive owner-leak guard.

    Walks the response (including any nested dict / list values) and
    fails on:

    * any ``_OWNER_ONLY_KEYS`` key being present anywhere (so the
      endpoint can never grow the ``UserLiteSerializer`` fields back into
      an SP payload by accident);
    * the owner's primary key, email, first name, or last name appearing
      as a string value anywhere (so a future serializer refactor that
      inlines ``owner.id`` under another key — e.g. ``"owner_id": ...``
      — also trips the guard).
    """
    owner_id_str = str(owner.id)
    owner_markers = (
        owner_id_str,
        owner.email,
        owner.first_name,
        owner.last_name,
    )

    def _walk(node, path=()):
        if isinstance(node, dict):
            for k, v in node.items():
                here = path + (k,)
                assert k not in _OWNER_ONLY_KEYS, (
                    f"Owner-only field {k!r} leaked into SP self-identity "
                    f"response at path '{'/'.join(map(str, here))}'."
                )
                if isinstance(v, str):
                    for marker in owner_markers:
                        if marker and v == marker:
                            raise AssertionError(
                                f"Owner marker {marker!r} appeared as a "
                                f"value under "
                                f"'{'/'.join(map(str, here))}'."
                            )
                _walk(v, here)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _walk(v, path + (i,))

    _walk(body)


@pytest.mark.contract
@pytest.mark.django_db
class TestSPSelfIdentityEndpoint:
    """``/api/v1/users/me/`` for service principals returns the SP's own
    identity (id, name, type) and nothing from the human owner's row.
    """

    def test_sp_with_user_read_scope_sees_self(
        self, workspace, create_user, api_client
    ):
        """Happy path: a provisioned SP gets its OWN identity, never the
        owner's profile. This is the central regression the mission closes."""
        sp = _make_sp(workspace, create_user, name="bot-self-ok")
        _grant_user_read_scope(sp)

        api_client.credentials(HTTP_X_API_KEY="plane_svc_token_bot-self-ok")
        response = api_client.get(USERS_ME_URL)

        assert response.status_code == status.HTTP_200_OK
        body = response.json()

        # SP self-identity, by design:
        assert body.get("id") == str(sp.id), (
            f"Expected SP id {sp.id!r}, got {body.get('id')!r}"
        )
        assert body.get("name") == "bot-self-ok"
        assert body.get("type") == "service_principal"

        # Owner-leak guard: no owner-only fields, no owner markers,
        # anywhere in the response body.
        _assert_no_owner_data(body, create_user)

    def test_sp_without_user_read_scope_is_denied(
        self, workspace, create_user, api_client
    ):
        """An unprovisioned SP still default-denies at the engine — the
        endpoint is reachable but only with the right scope row.

        Catches a regression where someone marks the view reachable
        without checking the gate: an SP with NO scope row at all would
        otherwise pass the engine (all SP branches do step 0 / step 4
        unconditionally) and return the owner's row through this view.
        """
        _make_sp(workspace, create_user, name="bot-self-no-scope")
        # No ServiceScope row at all — engine should DENY_SCOPE_MISS.

        api_client.credentials(
            HTTP_X_API_KEY="plane_svc_token_bot-self-no-scope"
        )
        response = api_client.get(USERS_ME_URL)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_sp_with_unrelated_scope_is_denied(
        self, workspace, create_user, api_client
    ):
        """A scope row keyed on a different resource type does not unlock
        ``users/me/`` — the engine matches on (resource_type, action)
        exactly. ``work_item:read`` cannot satisfy ``user:read``.
        """
        from plane.service_principals.models import ServiceScope as SS

        sp = _make_sp(workspace, create_user, name="bot-self-other-scope")
        SS.objects.create(
            service_principal=sp,
            project=None,
            resource_type="work_item",
            action="read",
        )

        api_client.credentials(
            HTTP_X_API_KEY="plane_svc_token_bot-self-other-scope"
        )
        response = api_client.get(USERS_ME_URL)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_human_caller_path_unaffected(
        self, api_key_client, create_user
    ):
        """Non-SP callers keep the legacy ``UserLiteSerializer`` payload
        byte-for-byte: id/first_name/last_name/email/avatar/avatar_url/
        display_name, with no service-principal type marker.
        """
        response = api_key_client.get(USERS_ME_URL)

        assert response.status_code == status.HTTP_200_OK
        body = response.json()

        assert body.get("id") == str(create_user.id)
        for field in (
            "first_name",
            "last_name",
            "email",
            "avatar",
            "avatar_url",
            "display_name",
        ):
            assert field in body, (
                f"Human path lost field {field!r} — this endpoint "
                "must remain byte-for-byte unchanged for non-SP callers."
            )

        # No service-principal marker on a human response.
        assert body.get("type") != "service_principal"
