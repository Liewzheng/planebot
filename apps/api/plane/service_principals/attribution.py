# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Attribution: which ``User`` a service-principal write is credited to.

Every attribution column in Plane is a FK to ``users`` — ``created_by``,
``updated_by``, ``IssueActivity.actor``, ``Comment.actor``. A service principal
is *not* a User row, so before this module an SP-authenticated write was
credited to whoever the token was filed under: the SP's **owner**, the human
admin who happens to own the bot. That is the bug behind PLANE-90 — the work
items a bot created all came out stamped with the owner's name, so "what the
SP created" could not be told apart from "what the owner created".

The fix is a *shadow bot user*: one ``User`` row per SP (``is_bot=True``,
``bot_type=SERVICE_PRINCIPAL``, ``display_name`` ``svc-<name> (bot)``, an
unusable ``bot_user_<spid>@<host>`` address), referenced by
``ServicePrincipal.bot_user``. Attribution columns then point at a real
``User`` row, so the activity log, notifications and every existing FK path
work unchanged — no new ownership column, no view rewrite.

Three properties are load-bearing and pinned by
``tests/contract/app/test_service_principal_attribution.py``:

* the bot user is **never a member**: no ``WorkspaceMember`` / ``ProjectMember``
  row is created for it, so every legacy membership-based permission check
  keeps failing closed for SP requests (the bot cannot be "a real workspace
  member" a permission class could resolve);
* the bot can **never authenticate**: ``is_bot=True`` is rejected by the login
  adapter before a credential is even checked, and its password is a random
  throw-away;
* **authorization never reads this module.** The ``authorize()`` chain decides
  on the SP principal (``request._sp_principal``); the bot user is not a
  member and carries no scopes. Only *credit* flows through here.

Callers:

* ``BaseModel.save`` — the ORM choke point. Every write that relies on
  auto-attribution is credited to the in-flight SP's bot user instead of the
  owner, with no view changes (``current_sp_actor()``).
* explicit-actor sites (``created_by=request.user``, ``actor_id=request.user.id``)
  — use :func:`actor_for_request` / :func:`actor_id_for_request` instead of
  reading ``request.user`` directly. Attribution must be resolved where the
  actor is chosen, because several v1 write views overwrite ``created_by``
  *after* the serializer saved.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import IntegrityError

from .constants import BOT_TYPE_SERVICE_PRINCIPAL, PrincipalType

if TYPE_CHECKING:  # pragma: no cover — typing only
    from plane.db.models import User

    from .models import ServicePrincipal

# Cache slot for the resolved SP on the request object, so the token → SP
# lookup runs at most once per request even though ``BaseModel.save`` consults
# it on every single write.
_SP_CACHE_ATTR = "_sp_attribution_principal"


def bot_display_name(sp_name: str) -> str:
    """The name an SP's writes are signed with, e.g. ``svc-deploy-bot (bot)``."""
    return f"svc-{sp_name} (bot)"


def bot_first_name(sp_name: str) -> str:
    """``first_name`` for the shadow bot user.

    The web client renders a bot actor as ``first_name + " Bot"`` (comments,
    activity, link rows) while ``display_name`` is used elsewhere, so the
    first name carries the bare ``svc-<name>`` handle and the "Bot" marker is
    added by whichever renderer wants it. Set both consistently:
    ``svc-deploy-bot Bot`` / ``svc-deploy-bot (bot)``.
    """
    return f"svc-{sp_name}"


def bot_username(service_principal: "ServicePrincipal") -> str:
    return f"bot_user_{service_principal.id}"


def bot_email(service_principal: "ServicePrincipal") -> str:
    """An address that can never receive or originate a login.

    Follows the ``bot_user_<id>@<host>`` convention
    ``bgtasks.workspace_seed_task`` already uses for its seed bot. The id is
    the SP's own UUID, so the address is unique and unguessable from outside.
    """
    host = urlparse(settings.WEB_URL or "https://plane.so").hostname or "plane.so"
    return f"{bot_username(service_principal)}@{host}"


def ensure_bot_user(service_principal: "ServicePrincipal") -> "User":
    """Return (creating on first use) the shadow bot user of ``service_principal``.

    Idempotent and race-tolerant: the username is unique, so a concurrent
    first-use loses the insert and re-reads the winner rather than failing.
    No ``WorkspaceMember`` / ``ProjectMember`` row is ever created here — that
    is the invariant that keeps the bot out of permission decisions.
    """
    from plane.db.models import User

    bot = service_principal.bot_user
    if bot is not None:
        return bot

    username = bot_username(service_principal)
    try:
        bot = User.objects.get(username=username)
    except User.DoesNotExist:
        try:
            bot = User.objects.create(
                username=username,
                display_name=bot_display_name(service_principal.name),
                first_name=bot_first_name(service_principal.name),
                last_name="Bot",
                email=bot_email(service_principal),
                is_bot=True,
                bot_type=BOT_TYPE_SERVICE_PRINCIPAL,
                is_active=True,
                password=make_password(uuid.uuid4().hex),
                is_password_autoset=True,
            )
        except IntegrityError:
            # Lost a race against a concurrent writer (username or email).
            bot = User.objects.get(username=username)

    # Link it back without clobbering a link another request just made.
    type(service_principal).objects.filter(
        pk=service_principal.pk, bot_user__isnull=True
    ).update(bot_user=bot)
    service_principal.bot_user = bot
    return bot


def _resolve_sp(request) -> Optional["ServicePrincipal"]:
    from .models import ServicePrincipal

    # v1 API: ``plane.api.middleware.APIKeyAuthentication`` stashes the
    # authz principal on the DRF request (both the ``plane_svc_`` branch and
    # the legacy-token dual-read shim reach here).
    principal = getattr(request, "_sp_principal", None)
    if principal is not None:
        return getattr(principal, "service_principal", None)

    # Internal app API: ``request.user`` is the marker proxy, never a User
    # row. It carries the SP id and nothing else.
    user = getattr(request, "user", None)
    if getattr(user, "_is_service_principal_proxy", False):
        sp_id = getattr(user, "service_principal_id", None)
        if sp_id is None:
            return None
        return ServicePrincipal.objects.filter(pk=sp_id).first()

    # ORM path: ``crum.get_current_request`` only holds the *Django* request,
    # where the SP marker is not reachable (the middleware sets it on DRF's
    # wrapper). DRF does propagate the credential itself onto the Django
    # request as ``request.auth``, so resolve the principal from the token.
    # App-API requests land here too, with the APIToken object rather than a
    # string — those are already covered by the proxy branch above.
    auth = getattr(request, "auth", None)
    if isinstance(auth, str) and auth:
        return ServicePrincipal.objects.filter(
            tokens__token=auth,
            tokens__is_active=True,
            tokens__principal_type=PrincipalType.SERVICE,
        ).first()
    return None


def sp_from_request(request) -> Optional["ServicePrincipal"]:
    """The :class:`ServicePrincipal` behind ``request``; ``None`` for humans.

    ``None`` means "this request is not a service principal" — a session, a
    user API token, an anonymous call, or no request at all (celery, shell).
    """
    if request is None:
        return None

    cached = getattr(request, _SP_CACHE_ATTR, None)
    if cached is not None:
        return cached

    sp = _resolve_sp(request)
    if sp is not None:
        # Best effort: request objects that forbid attribute writes simply
        # skip the cache and pay the lookup again.
        try:
            setattr(request, _SP_CACHE_ATTR, sp)
        except (AttributeError, TypeError):  # pragma: no cover — defensive
            pass
    return sp


def actor_for_request(request) -> Optional["User"]:
    """The ``User`` a write made through ``request`` must be credited to.

    An SP request resolves to the SP's shadow bot user — *never* to the owner
    the token is filed under. Anything else returns ``request.user``
    unchanged, so human attribution is byte-for-byte what it was.

    Use this at every explicit-actor site instead of reading ``request.user``:
    ``created_by=actor_for_request(request)``,
    ``actor_id=str(actor_id_for_request(request))``.
    """
    sp = sp_from_request(request)
    if sp is None:
        return getattr(request, "user", None)
    return ensure_bot_user(sp)


def actor_id_for_request(request):
    """:func:`actor_for_request` reduced to the id an ``actor_id`` column wants."""
    actor = actor_for_request(request)
    return getattr(actor, "id", None)


def current_sp_actor() -> Optional["User"]:
    """The bot user to credit for the write happening right now, else ``None``.

    Called from ``BaseModel.save`` (the one place every ORM write passes
    through) and therefore must stay cheap for human requests: it reads a
    thread-local and does attribute checks, and only touches the database
    when the in-flight request really is SP-authenticated.
    """
    from crum import get_current_request

    sp = sp_from_request(get_current_request())
    if sp is None:
        return None
    return ensure_bot_user(sp)
