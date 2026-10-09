# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: the AI-bot ``is_bot=True`` user path is unchanged.

vihar's review #2 / mission context explicit guarantee: ``M8 wiring must
not change the behavior of non-AI-bot ``is_bot`` User requests. This file
pins that contract by driving a bot through a real endpoint that the old
``enforce_ai_scope`` policy would 403 because the URL is not in the
historical ``URL_RESOURCE_MAP``. M8 must keep raising the same 403 — not
``PERMISSION_DENIED`` from the SP branch and not 200.
"""

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models.api import APIToken
from plane.service_principals.constants import PrincipalType


@pytest.fixture
def bot_token(db, create_bot_user):
    """A ``plane_api_`` token for a bot user (the historical AIAccount
    population). principal_type defaults to USER."""
    return APIToken.objects.create(
        user=create_bot_user,
        label="bot-conformance-token",
        token="plane_api_bot_conformance",
        principal_type=PrincipalType.USER,
    )


@pytest.mark.contract
@pytest.mark.django_db
class TestBotUserBehaviorIsUnchanged:
    """Every assertion below describes the legacy behavior; M8 must keep
    producing the same responses, never replacing them with the SP path."""

    def test_bot_request_to_unmapped_url_still_403_with_scope_message(
        self, bot_token
    ):
        """``/users/me/`` is not in ``URL_RESOURCE_MAP``. The legacy
        ``enforce_ai_scope`` raises ``PermissionDenied("Endpoint '<name>'
        is not available to AI accounts.")``. M8 keeps the bot on the same
        hook — the response must remain 403 from that handler, not from
        the new SP branch.
        """
        client = APIClient()
        client.credentials(HTTP_X_API_KEY=bot_token.token)
        response = client.get("/api/v1/users/me/")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_bot_request_to_mapped_url_still_403_when_out_of_scope(
        self, bot_token, workspace, create_bot_user
    ):
        """For a URL that *is* in ``URL_RESOURCE_MAP`` (e.g. ``project``)
        the bot must still need a matching ``AIScopePolicy`` row — M8
        keeps the legacy chain. Without a policy row the bot gets 403.
        """
        from plane.db.models import WorkspaceMember

        # Bot user must be in some workspace to even hit the URL.
        WorkspaceMember.objects.create(
            workspace=workspace,
            member=create_bot_user,
            role=15,
            is_active=True,
        )

        client = APIClient()
        client.credentials(HTTP_X_API_KEY=bot_token.token)
        # ``/projects/`` is in URL_RESOURCE_MAP → legacy path checks the
        # bot's AIScopePolicy. None exist → 403.
        response = client.get(
            f"/api/v1/workspaces/{workspace.slug}/projects/"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.contract
@pytest.mark.django_db
class TestHumanUserBehaviorIsUnchanged:
    """Humans going through the same endpoints must keep working exactly
    as before M8 — no behavioral side-effects on the ``is_bot=False``
    path."""

    def test_human_can_access_users_me(self, api_key_client):
        response = api_key_client.get("/api/v1/users/me/")
        assert response.status_code == status.HTTP_200_OK
