# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: service-principal write attribution (M39 / PLANE-90).

A service principal is not a ``User`` row, but every attribution column in
Plane is a FK to ``users``. Before M39 an SP-authenticated write was credited
to the SP's **owner** — the human admin the service token is filed under — so
nobody could tell which work items a bot had created. M39 gives every SP a
*shadow bot user* (``ServicePrincipal.bot_user``, ``is_bot=True``,
``bot_type=SERVICE_PRINCIPAL``, ``svc-<name> (bot)``) and routes attribution
through it.

What this file pins:

* the bot user's identity: how it is provisioned (eagerly on SP creation,
  lazily by ``ensure_bot_user``, and by the 0005 backfill — including the
  branch that re-uses the bot user of an SP converted from an ``AIAccount``),
  and that it is **not** a member and **cannot** log in;
* the resolver: ``actor_for_request`` / ``actor_id_for_request`` return the
  bot user for both SP authentication surfaces (the v1 DRF request and the
  app-API proxy) and leave human requests byte-for-byte alone;
* the ORM choke point: a write performed while an SP request is in flight is
  credited to the bot user, never to the owner.

Scope note (tower ruling B). The v1 **write views** still stamp the owner for
work items — ``api/views/issue.py`` overwrites ``created_by`` *after* the
serializer saved — and those actor sites belong to M41. That gap is measured
here by a single ``xfail`` (``test_work_item_created_by_is_the_bot``): drop the
marker when M41 wires the actor sites. Everything asserted green in this file
is the attribution *layer*, which is what M40's ownership engine keys on.
"""

from __future__ import annotations

import importlib

import pytest
from rest_framework import status
from rest_framework.request import Request
from rest_framework.test import APIClient, APIRequestFactory

from plane.db.models import (
    APIToken,
    Issue,
    Label,
    Project,
    ProjectMember,
    User,
    WorkspaceMember,
)
from plane.service_principals.attribution import (
    actor_for_request,
    actor_id_for_request,
    bot_display_name,
    bot_username,
    ensure_bot_user,
    sp_from_request,
)
from plane.service_principals.constants import (
    BOT_TYPE_SERVICE_PRINCIPAL,
    PrincipalType,
)
from plane.service_principals.models import (
    ProjectGrant,
    ServicePrincipal,
    ServiceScope,
)

SP_NAME = "attr-bot"


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="Attribution Project",
        identifier="ATTR",
        workspace=workspace,
        created_by=create_user,
        network=2,
    )
    ProjectMember.objects.create(
        project=project, member=create_user, role=20, is_active=True
    )
    return project


def make_sp(workspace, owner, name=SP_NAME):
    """An SP with one service token, no scopes and no grants.

    The token string is derived from the name so two SPs in one test never
    collide on ``APIToken.token``'s unique index.
    """
    sp = ServicePrincipal.objects.create(workspace=workspace, owner=owner, name=name)
    APIToken.objects.create(
        user=owner,
        label=f"svc:{name}",
        token=f"plane_svc_{name}",
        user_type=1,
        is_service=True,
        principal_type=PrincipalType.SERVICE,
        service_principal=sp,
        workspace=workspace,
    )
    return sp


def token_of(sp):
    return sp.tokens.get(principal_type=PrincipalType.SERVICE).token


def add_scope(sp, resource_type, action, project=None):
    return ServiceScope.objects.create(
        service_principal=sp,
        project=project,
        resource_type=resource_type,
        action=action,
    )


def add_grant(sp, project, role_cap=15):
    return ProjectGrant.objects.create(
        service_principal=sp, project=project, role_cap=role_cap
    )


def client_for(sp):
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=token_of(sp))
    return client


@pytest.fixture
def sp(db, workspace, create_user):
    return make_sp(workspace, create_user)


@pytest.fixture
def sp_client(sp):
    return client_for(sp)


@pytest.fixture
def granted_sp(workspace, create_user, project):
    """An SP allowed to create and update labels in ``project``.

    Labels are the probe surface for the ORM choke point because no v1 view
    hardcodes an actor for them — whatever lands in ``created_by`` came from
    the layer under test (unlike work items, see the module docstring).
    """
    sp = make_sp(workspace, create_user, name="label-bot")
    add_scope(sp, "label", "create", project=project)
    add_scope(sp, "label", "update", project=project)
    add_grant(sp, project)
    return sp


def label_url(slug, project_id):
    return f"/api/v1/workspaces/{slug}/projects/{project_id}/labels/"


def label_detail_url(slug, project_id, pk):
    return f"/api/v1/workspaces/{slug}/projects/{project_id}/labels/{pk}/"


def work_item_url(slug, project_id):
    return f"/api/v1/workspaces/{slug}/projects/{project_id}/work-items/"


def authenticated_v1_request(token):
    """Drive M8's real auth class and return ``(drf_request, django_request)``.

    Using the production authenticator through DRF's own ``Request`` (rather
    than a hand-built stub) is what makes the assertions below evidence: DRF's
    ``user``/``auth`` setters mirror both onto the underlying Django request,
    which is exactly the state ``crum`` hands the ORM in production.
    """
    from plane.api.middleware.api_authentication import APIKeyAuthentication

    django_request = APIRequestFactory().post(
        "/api/v1/workspaces/test-workspace/projects/"
        "00000000-0000-0000-0000-000000000000/labels/",
        data={},
        format="json",
        HTTP_X_API_KEY=token,
    )
    drf_request = Request(django_request, authenticators=[APIKeyAuthentication()])
    assert drf_request.user is not None
    assert drf_request.auth is not None
    return drf_request, django_request


class _StubSchemaEditor:
    """The 0005 data migration only reads ``connection.alias`` off it."""

    @property
    def connection(self):
        from django.db import connection

        return connection


def run_bot_user_backfill():
    migration = importlib.import_module(
        "plane.service_principals.migrations.0005_backfill_service_principal_bot_users"
    )
    from django.apps import apps as django_apps

    migration.backfill_bot_users(django_apps, _StubSchemaEditor())


# --------------------------------------------------------------------------- #
# Shadow bot user identity                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestShadowBotUserIdentity:
    def test_api_created_sp_provisions_bot_user(self, session_client, workspace):
        """Creating an SP through the management API provisions its bot user
        eagerly, so ``bot_user`` is populated for every live SP."""
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/service-principals/",
            {"name": "deploy-bot", "description": ""},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        sp = ServicePrincipal.objects.get(pk=response.data["id"])
        bot = sp.bot_user
        assert bot is not None
        assert bot.is_bot is True
        assert bot.bot_type == BOT_TYPE_SERVICE_PRINCIPAL
        assert bot.display_name == "svc-deploy-bot (bot)"
        # The web client renders a bot actor as ``first_name + " Bot"`` in the
        # comment/activity rows and as ``display_name`` elsewhere; both spell
        # the same signature.
        assert bot.first_name == "svc-deploy-bot"
        assert bot.last_name == "Bot"
        assert bot.username == bot_username(sp)
        # The address follows the deployment's own host, same convention as
        # the workspace seed bot — it can never receive or send mail.
        assert bot.email.startswith(f"bot_user_{sp.id}@")
        assert bot.is_active is True

    def test_ensure_bot_user_is_idempotent(self, workspace, create_user):
        """Lazy provisioning never grows a second bot for the same SP."""
        sp = make_sp(workspace, create_user, name="lazy-bot")
        assert sp.bot_user_id is None

        first = ensure_bot_user(sp)
        second = ensure_bot_user(sp)
        sp.refresh_from_db()

        assert first.pk == second.pk
        assert sp.bot_user_id == first.pk
        assert User.objects.filter(username=bot_username(sp)).count() == 1

    def test_bot_user_is_not_a_member(self, workspace, project, sp):
        """The invariant the mission's red line rests on: attribution points
        at the bot, authorization never resolves it as a member."""
        bot = ensure_bot_user(sp)

        assert not WorkspaceMember.objects.filter(member=bot).exists()
        assert not ProjectMember.objects.filter(member=bot).exists()

    def test_bot_user_cannot_log_in(self, sp):
        """``is_bot=True`` is the guard the login adapter rejects before any
        credential is checked, and the password is a random throw-away."""
        bot = ensure_bot_user(sp)

        assert bot.is_bot is True
        assert bot.is_password_autoset is True
        assert bot.check_password("") is False
        assert bot.check_password("password") is False
        assert bot.has_usable_password() is True

    def test_rename_keeps_bot_signature_in_step(self, session_client, workspace, sp):
        """The bot signs SP writes, so its name follows the SP's."""
        sp.ensure_bot_user()

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/service-principals/{sp.id}/",
            {"name": "renamed-bot"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        sp.refresh_from_db()
        assert sp.bot_user.display_name == bot_display_name("renamed-bot")
        # The handle stays stable — attribution history must not move.
        assert sp.bot_user.username == bot_username(sp)

    def test_backfill_provisions_bot_for_existing_sps(self, workspace, create_user):
        """0005 backfills pre-existing rows, so an ownership check can rely on
        ``bot_user`` being set for every live SP."""
        legacy = make_sp(workspace, create_user, name="legacy-bot")
        assert legacy.bot_user_id is None

        run_bot_user_backfill()

        legacy.refresh_from_db()
        assert legacy.bot_user_id is not None
        assert legacy.bot_user.is_bot is True
        assert legacy.bot_user.bot_type == BOT_TYPE_SERVICE_PRINCIPAL
        assert legacy.bot_user.display_name == "svc-legacy-bot (bot)"

    def test_backfill_reuses_the_inherited_ai_account_bot(self, workspace, create_user, sp):
        """An SP converted from an AIAccount keeps the bot user whose earlier
        writes are already attributed to it, so its history stays continuous."""
        from plane.ai_accounts.models import AIAccount, AIAccountMigrationRecord

        inherited = User.objects.create(
            username="bot_user_inherited",
            display_name="Plane",
            email="bot_user_inherited@plane.so",
            is_bot=True,
            bot_type="AI_AGENT",
        )
        account = AIAccount.objects.create(
            workspace=workspace,
            owner=create_user,
            bot_user=inherited,
            name="converted",
        )
        AIAccountMigrationRecord.objects.create(ai_account=account, service_principal=sp)

        run_bot_user_backfill()

        sp.refresh_from_db()
        assert sp.bot_user_id == inherited.pk
        # Only the signature is refreshed; bot_type is left alone so the row
        # keeps moving through the visibility predicates it already did.
        assert sp.bot_user.display_name == f"svc-{sp.name} (bot)"
        assert sp.bot_user.bot_type == "AI_AGENT"


# --------------------------------------------------------------------------- #
# Resolver                                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestActorResolver:
    def test_v1_request_resolves_to_the_bot_user(self, sp):
        """Both entry points must agree for a v1 request: the DRF wrapper
        carries ``_sp_principal``, while the ORM (``crum``'s thread-local) only
        ever sees the *Django* request, where the credential itself is
        reachable as ``request.auth``."""
        drf_request, django_request = authenticated_v1_request(token_of(sp))
        bot = ensure_bot_user(sp)

        assert sp_from_request(drf_request) == sp
        assert sp_from_request(django_request) == sp
        assert actor_for_request(drf_request) == bot
        assert actor_id_for_request(drf_request) == bot.pk
        assert actor_id_for_request(django_request) == bot.pk

    def test_app_proxy_request_resolves_to_the_bot_user(self, sp):
        """The internal app API authenticates as a marker proxy, not a User
        row; the proxy's SP id is enough to find the bot."""
        from plane.app.middleware.api_authentication import _ServicePrincipalProxy

        request = type("_R", (), {"user": _ServicePrincipalProxy(sp=sp)})()

        assert sp_from_request(request) == sp
        assert actor_id_for_request(request) == ensure_bot_user(sp).pk

    def test_human_requests_are_untouched(self, workspace, create_user):
        """Control: a session request and a plain user-token request keep
        ``request.user`` as the actor — the change is SP-only."""
        session_request = type("_R", (), {"user": create_user, "auth": None})()
        assert sp_from_request(session_request) is None
        assert actor_for_request(session_request) == create_user

        user_token = APIToken.objects.create(
            user=create_user, label="human", token="plane_api_human_token", workspace=workspace
        )
        user_token_request = type("_R", (), {"user": create_user, "auth": user_token.token})()
        assert sp_from_request(user_token_request) is None
        assert actor_id_for_request(user_token_request) == create_user.pk

    def test_anonymous_and_absent_requests_resolve_to_nothing(self):
        """Fail-closed: no request (celery, shell) or an anonymous one never
        mints a bot-user attribution."""
        anonymous = type("_R", (), {"user": type("_U", (), {"is_anonymous": True})()})()
        assert sp_from_request(anonymous) is None
        assert sp_from_request(None) is None

    def test_deactivated_service_token_resolves_to_nothing(self, sp):
        """A token that is no longer live cannot mint an attribution either:
        the resolver falls through to the plain request user."""
        token = token_of(sp)
        APIToken.objects.filter(token=token).update(is_active=False)

        django_request = APIRequestFactory().post("/api/v1/x/", data={})
        django_request.auth = token
        django_request.user = sp.owner

        assert sp_from_request(django_request) is None
        assert actor_for_request(django_request) == sp.owner


# --------------------------------------------------------------------------- #
# ORM-level attribution                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.contract
@pytest.mark.django_db
class TestOrmAttribution:
    def test_sp_created_row_is_credited_to_the_bot(
        self, workspace, project, create_user, granted_sp
    ):
        """The mission's rule, measured on a real v1 write: the row an SP
        creates carries the SP's bot user, not the owner."""
        response = client_for(granted_sp).post(
            label_url(workspace.slug, project.id),
            {"name": "bot-label", "color": "#abcdef", "description": ""},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        bot = granted_sp.ensure_bot_user()
        label = Label.objects.get(project=project, name="bot-label")
        assert label.created_by_id == bot.pk
        assert label.created_by.is_bot is True
        assert label.created_by_id != create_user.pk

    def test_sp_update_is_credited_to_the_bot(
        self, workspace, project, create_user, granted_sp
    ):
        """``updated_by`` follows the same rule: the owner is not the signature
        of an SP action, on creates or on updates."""
        client = client_for(granted_sp)
        created = client.post(
            label_url(workspace.slug, project.id),
            {"name": "bot-label-2", "color": "#abcdef", "description": ""},
            format="json",
        )
        assert created.status_code == status.HTTP_201_CREATED
        label = Label.objects.get(project=project, name="bot-label-2")

        response = client.patch(
            label_detail_url(workspace.slug, project.id, label.pk),
            {"description": "touched by the bot"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        label.refresh_from_db()
        bot = granted_sp.ensure_bot_user()
        assert label.updated_by_id == bot.pk
        assert label.created_by_id == bot.pk

    def test_human_write_is_credited_to_the_human(
        self, workspace, project, create_user, session_client
    ):
        """Control: a session-authenticated write is unchanged."""
        response = session_client.post(
            label_url(workspace.slug, project.id),
            {"name": "human-label", "color": "#123456", "description": ""},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        label = Label.objects.get(project=project, name="human-label")
        assert label.created_by_id == create_user.pk
        assert label.created_by.is_bot is False

    @pytest.mark.xfail(
        reason="M41 wires the v1 view actor sites: api/views/issue.py:551 sets "
        "created_by to request.user.id after the serializer saved. Drop this "
        "marker when that lands.",
        strict=False,
    )
    def test_work_item_created_by_is_the_bot(self, workspace, project, create_user):
        """The user-visible case (PLANE-90): a work item the SP created must be
        signed by the SP's bot user, not by its owner."""
        sp = make_sp(workspace, create_user, name="work-item-bot")
        add_scope(sp, "work_item", "create", project=project)
        add_grant(sp, project)

        response = client_for(sp).post(
            work_item_url(workspace.slug, project.id),
            {"name": "bot-work-item"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        issue = Issue.objects.get(project=project, name="bot-work-item")
        assert issue.created_by_id != create_user.pk
        assert issue.created_by_id == sp.ensure_bot_user().pk
