# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Bridge from the Django/DRF request to the :mod:`plane.core.authz` Principal.

The authz engine is transport-agnostic: it consumes a
:class:`plane.core.authz.Principal`, not a ``request.user``. This module is
the one place that knows how a request resolves to a Principal. DRF views
and decorators import :func:`principal_from_request`; HTTP routing changes
should not need to touch individual views.

Two flavors of identity land here:

* **Human user** — established by ``SessionAuthentication`` (login cookies)
  or by :class:`APIKeyAuthentication` carrying an ``APIToken`` with
  ``principal_type=USER``. Either way, ``request.user`` is the Django
  ``User`` row and ``request.auth`` is the ``APIToken`` (or ``None`` for
  session). ``UserPrincipal`` is returned with the user's id as the
  workspace anchor; the engine short-circuits human principals to
  ``Decision.allow`` because the existing role-based permission classes
  still own the human path.

* **Service principal** — established by :class:`APIKeyAuthentication`
  carrying an ``APIToken`` with ``principal_type=SERVICE``. The
  ``service_principal`` row is the actor. ``ServicePrincipal_`` is
  returned; the engine runs the four-step decision chain (scope → grant
  → role_cap → owner intersection).

Closing PLANE-82 — the validation gap that lets a service token slip
through as the SP owner — depends on this module plus the auth
middleware. As long as the middleware constructs an ``APIToken`` with the
right ``principal_type`` and the resolver reads from ``request.auth``,
no caller needs to special-case SPs at the view layer.
"""

from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

from plane.db.models import APIToken
from plane.service_principals.constants import PrincipalType

from .principal import Principal, ServicePrincipalAuthz, UserPrincipal


if TYPE_CHECKING:
    from django.http import HttpRequest


def _is_human_user(user: Any) -> bool:
    """Best-effort duck-type check for a real Django ``User``.

    AnonymousUser and the service-principal proxy both return False here;
    the SP path is taken before the user check via ``request.auth``.
    """
    if user is None:
        return False
    if getattr(user, "is_authenticated", False) is False:
        return False
    # ``_service_principal`` is the marker on the SP proxy; if present, the
    # request is service-token-driven and ``request.auth`` carries the truth.
    if getattr(user, "_is_service_principal_proxy", False):
        return False
    return True


def principal_from_request(request: "HttpRequest") -> Optional[Principal]:
    """Return the :class:`Principal` for ``request`` or ``None``.

    Resolution order:

    1. ``request.auth`` is an :class:`APIToken` with
       ``principal_type == PrincipalType.SERVICE`` and a live
       ``service_principal`` — return a
       :class:`ServicePrincipalAuthz` (alias of :class:`ServicePrincipal_`).
    2. ``request.user`` is an authenticated Django ``User`` — return a
       :class:`UserPrincipal` with ``workspace_id`` resolved from the URL
       (``view`` injected) or, as a fallback, the user's primary key.
    3. Anything else (``AnonymousUser``, missing ``request.user``) —
       return ``None``. The caller treats ``None`` as a deny.
    """
    if request is None:
        return None

    auth = getattr(request, "auth", None)
    if isinstance(auth, APIToken) and getattr(auth, "principal_type", 0) == PrincipalType.SERVICE:
        sp = getattr(auth, "service_principal", None)
        if sp is None or not getattr(sp, "is_active", False):
            return None
        return ServicePrincipalAuthz(service_principal=sp)

    user = getattr(request, "user", None)
    if _is_human_user(user):
        workspace_id = _resolve_workspace_id(request, user)
        return UserPrincipal(user=user, workspace_id=workspace_id)

    return None


def _resolve_workspace_id(request: "HttpRequest", user: Any) -> str:
    """Workspace anchor for a UserPrincipal.

    The engine short-circuits human principals to ``Decision.allow`` so the
    actual value here is a placeholder — the user's primary key keeps the
    principal constructible across every URL shape.
    """
    return str(getattr(user, "id", "") or "")


__all__ = ["principal_from_request"]
