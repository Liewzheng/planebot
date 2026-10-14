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

Scope note. The v1 **write views** used to stamp the owner for work items —
``api/views/issue.py`` overwrote ``created_by`` *after* the serializer saved.
M41 wired those actor sites to the SP's bot user, so the gap is closed and
``test_work_item_created_by_is_the_bot`` asserts the real view path. Everything
else asserted in this file is the attribution *layer*, which is what M40's
ownership engine keys on.
"""

from __future__ import annotations

import importlib
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.request import Request
from rest_framework.test import APIClient, APIRequestFactory

from plane.ai_accounts.models import AIAccount
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


def work_item_detail_url(slug, project_id, pk):
    return f"/api/v1/workspaces/{slug}/projects/{project_id}/work-items/{pk}/"


def issue_links_url(slug, project_id, issue_id):
    return f"/api/v1/workspaces/{slug}/projects/{project_id}/work-items/{issue_id}/links/"


def issue_comments_url(slug, project_id, issue_id):
    return f"/api/v1/workspaces/{slug}/projects/{project_id}/work-items/{issue_id}/comments/"


def issue_comment_detail_url(slug, project_id, issue_id, pk):
    return (
        f"/api/v1/workspaces/{slug}/projects/{project_id}/work-items/"
        f"{issue_id}/comments/{pk}/"
    )


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
        eagerly, so an SP made here never depends on the lazy path."""
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
        from plane.ai_accounts.models import AIAccountMigrationRecord

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

    def test_conversion_after_the_backfill_still_links_the_inherited_bot(
        self, workspace, create_user
    ):
        """The other half of the same invariant: an account converted *after*
        0005 ran must inherit its bot user at conversion time.

        Without the link the SP keeps ``bot_user = NULL`` until its first
        write, and the lazy path then mints a fresh bot — silently losing the
        attribution of everything the inherited bot wrote before the
        conversion, which is exactly what 0005's inherited branch preserves.
        """
        inherited = User.objects.create(
            username="bot_user_converted",
            display_name="Plane",
            email="bot_user_converted@plane.so",
            is_bot=True,
            bot_type="AI_AGENT",
        )
        account = AIAccount.objects.create(
            workspace=workspace, owner=create_user, bot_user=inherited, name="converted-now"
        )

        call_command(
            "migrate_ai_accounts_to_service_principals",
            stdout=StringIO(),
            stderr=StringIO(),
        )

        sp = ServicePrincipal.objects.get(id=account.id)
        assert sp.bot_user_id == inherited.pk
        # And the lazy path must not mint a second bot on top of it.
        assert ensure_bot_user(sp).pk == inherited.pk
        assert User.objects.filter(username=bot_username(sp)).count() == 0


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

    def test_human_user_token_lookup_cost_is_per_request_not_per_write(
        self, workspace, create_user
    ):
        """``BaseModel.save`` consults the resolver on **every** write, so the
        *negative* answer has to be memoised too: a v1 request driven by a
        human ``plane_api_`` token would otherwise run a
        ``service_principals ⋈ apitokens`` SELECT on every insert/update.

        Measured: 1 query on the first resolution, 0 for every later one on
        the same request (5 further calls below), versus 1 per call before the
        miss was cached.
        """
        token = APIToken.objects.create(
            user=create_user, label="human", token="plane_api_human_cached", workspace=workspace
        )
        request = type("_R", (), {"user": create_user, "auth": token.token})()

        with CaptureQueriesContext(connection) as first:
            assert sp_from_request(request) is None
        with CaptureQueriesContext(connection) as subsequent:
            for _ in range(5):
                assert sp_from_request(request) is None

        assert len(first.captured_queries) == 1
        assert len(subsequent.captured_queries) == 0

    def test_service_token_lookup_is_also_memoised(self, sp):
        """The positive answer is memoised as well — one lookup per request,
        not one per write."""
        request = type(
            "_R", (), {"user": sp.owner, "auth": token_of(sp)}
        )()

        with CaptureQueriesContext(connection) as first:
            assert sp_from_request(request) == sp
        with CaptureQueriesContext(connection) as subsequent:
            for _ in range(5):
                assert sp_from_request(request) == sp

        assert len(first.captured_queries) == 1
        assert len(subsequent.captured_queries) == 0


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

    def test_work_item_created_by_is_the_bot(self, workspace, project, create_user):
        """The user-visible case (PLANE-90): a work item the SP created must be
        signed by the SP's bot user, not by its owner.

        The v1 create view overwrites ``created_by`` *after* the serializer
        saved (``api/views/issue.py``), so this pins the view's actor site and
        not just the ORM choke point. It is also what the ownership step keys
        on: a row credited to the owner would be unwritable by the SP that
        created it.
        """
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


# --------------------------------------------------------------------------- #
# Client-supplied ``created_by`` is ignored (M48)                             #
# --------------------------------------------------------------------------- #
#
# M41 wired the **default** at every v1 write site to ``actor_id_for_request``,
# so an SP that omits ``created_by`` is now stamped with its bot user. M48
# closes the remaining half: a client that *supplies* a ``created_by`` cannot
# move the row under a different actor's ownership. The five sites that
# previously read ``request.data.get("created_by", actor_id_for_request(...))``
# now read ``actor_id_for_request(request)`` unconditionally.
#
# Why the change matters even after M40: ``created_by`` is an authorization
# input (member-tier SPs may only update/delete rows their own bot created).
# Letting a caller pick the value lets them pick who owns it.
#
# The tests below are differential. Each one is checked against the
# pre-fix source: with the override restored, the assertions read the
# spoofed uuid the client sent and the patch tests flip — every assertion
# is recorded, the file turns red, then the fix is restored and the file
# goes green again.


@pytest.fixture
def other_human(db):
    """A second human user — useful as the spoof target so the test row's
    real actor and the spoof target are both real ``User`` pks.

    ``username`` is on a unique index in ``plane.db.models.user``; the
    ``create_user`` fixture in the shared ``conftest`` leaves it empty, so a
    second row with ``username=""`` would collide on insert. We pin a unique
    handle here.
    """
    return User.objects.create(
        username="spoof-target",
        email="spoof-target@plane.so",
        first_name="Spoof",
        last_name="Target",
    )


def _sp_with_work_item_scope(workspace, owner, project, name):
    sp = make_sp(workspace, owner, name=name)
    for action in ("read", "create", "update", "delete"):
        add_scope(sp, "work_item", action, project=project)
    add_grant(sp, project, role_cap=15)
    return sp


@pytest.mark.contract
@pytest.mark.django_db
class TestClientSuppliedCreatedByIgnored:
    """A client cannot move a freshly-created v1 row under another actor by
    sending ``created_by`` in the request body — the view stamps the row
    with ``actor_id_for_request(request)`` and never the spoofed value.

    Pre-fix (the override was active), every assertion below read the
    spoofed uuid the client sent. The tests are designed to flip
    individually, not all at once, so a future regression at any one site
    shows up as a localized failure.
    """

    def test_human_work_item_post_ignores_client_supplied_created_by(
        self, workspace, project, create_user, other_human, session_client
    ):
        """Human path: the row is credited to the requesting human even when
        the client names someone else in the payload."""
        response = session_client.post(
            work_item_url(workspace.slug, project.id),
            {
                "name": "spoof-as-other-human",
                "created_by": str(other_human.pk),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data

        issue = Issue.objects.get(project=project, name="spoof-as-other-human")
        assert issue.created_by_id == create_user.pk, (
            "v1 stamped the spoof target (other_human.pk) instead of the "
            "requesting human — the request.data.get('created_by') override "
            "is back."
        )
        assert issue.created_by_id != other_human.pk

    def test_sp_work_item_post_with_created_by_pointing_at_owner_stamps_bot(
        self, workspace, project, create_user
    ):
        """SP path: the bot user is the actor; the spoofed ``created_by`` (the
        human owner) must not leak into the row."""
        sp = _sp_with_work_item_scope(
            workspace, create_user, project, name="bot-claims-owner"
        )

        response = client_for(sp).post(
            work_item_url(workspace.slug, project.id),
            {
                "name": "sp-tries-to-blame-owner",
                "created_by": str(sp.owner.pk),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data

        bot = sp.ensure_bot_user()
        issue = Issue.objects.get(project=project, name="sp-tries-to-blame-owner")
        assert issue.created_by_id == bot.pk, (
            "v1 stamped the owner — the SP successfully forged attribution "
            "back onto the human that filed its token."
        )
        assert issue.created_by_id != create_user.pk
        assert issue.created_by_id != sp.owner.pk

    def test_sp_a_work_item_post_with_created_by_pointing_to_sp_b_stamps_sp_a(
        self, workspace, project, create_user
    ):
        """SP ↔ SP: the row is owned by whichever SP actually wrote it, not by
        whoever the payload named — and the authz step on the subsequent
        PATCH matches the writer, not the spoofed name."""
        sp_a = _sp_with_work_item_scope(
            workspace, create_user, project, name="forge-a"
        )
        sp_b = _sp_with_work_item_scope(
            workspace, create_user, project, name="forge-b"
        )
        sp_b_bot = sp_b.ensure_bot_user()

        # The payload names SP-B's bot; the row must be owned by SP-A.
        created_response = client_for(sp_a).post(
            work_item_url(workspace.slug, project.id),
            {
                "name": "sp-a-forgeries-sp-b",
                "created_by": str(sp_b_bot.pk),
            },
            format="json",
        )
        assert created_response.status_code == status.HTTP_201_CREATED, (
            created_response.data
        )

        issue = Issue.objects.get(project=project, name="sp-a-forgeries-sp-b")
        sp_a_bot = sp_a.ensure_bot_user()
        assert issue.created_by_id == sp_a_bot.pk, (
            "v1 stamped the spoofed uuid (sp_b's bot) instead of the actual "
            "writer (sp_a's bot) — the ownership step on the next PATCH will "
            "see the wrong owner."
        )
        assert issue.created_by_id != sp_b_bot.pk

        # PATCH as the real owner succeeds (member tier).
        own_patch = client_for(sp_a).patch(
            work_item_detail_url(workspace.slug, project.id, issue.pk),
            data={"name": "renamed-by-owner"},
            format="json",
        )
        assert own_patch.status_code == status.HTTP_200_OK, (
            f"SP-A (the writer) was denied a PATCH on its own row: "
            f"{own_patch.data!r}. The ownership step now sees SP-A's bot, "
            f"not the spoofed SP-B value."
        )
        issue.refresh_from_db()
        assert issue.name == "renamed-by-owner"

        # PATCH as the named-forger is denied by the ownership step.
        other_patch = client_for(sp_b).patch(
            work_item_detail_url(workspace.slug, project.id, issue.pk),
            data={"name": "renamed-by-spoofer"},
            format="json",
        )
        assert other_patch.status_code == status.HTTP_403_FORBIDDEN, (
            f"SP-B (the spoof target) was allowed a PATCH on SP-A's row: "
            f"{other_patch.data!r}."
        )
        assert "may only update a work_item it created" in other_patch.json()[
            "detail"
        ]

        # The denied write changed nothing.
        issue.refresh_from_db()
        assert issue.name == "renamed-by-owner"

    def test_human_link_post_ignores_client_supplied_created_by(
        self, workspace, project, create_user, other_human, session_client
    ):
        """Link path variant: a human attaching a link cannot set
        ``created_by`` on it either."""
        issue = Issue.objects.create(
            name="link-target",
            project_id=project.id,
            workspace_id=project.workspace_id,
            created_by_id=create_user.pk,
        )

        response = session_client.post(
            issue_links_url(workspace.slug, project.id, issue.id),
            {
                "title": "T",
                "url": "https://example.com/spec",
                "issue_id": str(issue.id),
                "created_by": str(other_human.pk),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data

        from plane.db.models import IssueLink

        link = IssueLink.objects.get(
            issue=issue, url="https://example.com/spec"
        )
        assert link.created_by_id == create_user.pk, (
            "v1 stamped the spoof target on the link instead of the "
            "requesting human — the request.data.get('created_by') override "
            "is back at the link-create site."
        )
        assert link.created_by_id != other_human.pk

    def test_human_comment_post_ignores_client_supplied_created_by(
        self, workspace, project, create_user, other_human, session_client
    ):
        """Comment path variant: the row's ``created_by`` *and* its
        ``actor_id`` (the second attribution column the comment view writes
        from the same ``request.data.get('created_by')`` site) both belong
        to the requesting human, never to the spoofed uuid."""
        issue = Issue.objects.create(
            name="comment-target",
            project_id=project.id,
            workspace_id=project.workspace_id,
            created_by_id=create_user.pk,
        )

        response = session_client.post(
            issue_comments_url(workspace.slug, project.id, issue.id),
            {
                "comment_html": "<p>hi</p>",
                "created_by": str(other_human.pk),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data

        from plane.db.models import IssueComment

        comment = IssueComment.objects.get(issue=issue, comment_html="<p>hi</p>")
        assert comment.created_by_id == create_user.pk, (
            "v1 stamped the spoof target on the comment instead of the "
            "requesting human — the request.data.get('created_by') override "
            "is back at the comment-create site."
        )
        assert comment.actor_id == create_user.pk, (
            "v1 stamped the spoof target on the comment's actor_id — the "
            "second attribution column read from request.data.get('created_by') "
            "is back."
        )
        assert comment.created_by_id != other_human.pk
        assert comment.actor_id != other_human.pk
