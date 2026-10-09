# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Principal abstraction.

A Principal is whatever the request resolved to at the authentication
layer. Two flavors:

* :class:`UserPrincipal` — the historical path: a Django ``User`` row, with
  ProjectMember / WorkspaceMember backing the role computation.
* :class:`ServicePrincipal_` — a ``ServicePrincipal`` row from
  ``service_principals.models``. No WorkspaceMember / ProjectMember rows;
  every authorization decision is answered by
  :func:`plane.core.authz.authorize`.

The authentication layer branches on ``APIToken.principal_type`` to produce
one of the two. Authorization code only ever sees ``Principal``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from plane.db.models import User
    from plane.service_principals.models import ServicePrincipal


class Principal(Protocol):
    """Read-only view of an authenticated actor.

    The authz module never mutates a principal; it only consumes identity
    information to make a decision. Implementations must be cheap to
    construct (the authenticate layer hands one to every request).
    """

    @property
    def kind(self) -> str:
        """Stable string identifier: ``"user"`` or ``"service"``."""
        ...

    @property
    def workspace_id(self) -> str:
        """Workspace this principal is acting within.

        For human users this is the slug/workspace of the URL. For SPs it is
        the SP's owning workspace — tokens never cross workspaces.
        """
        ...


@dataclass(frozen=True)
class UserPrincipal:
    """A request authenticated as a Django ``User``.

    Carries the user row so downstream code can run membership queries
    without re-resolving. ``workspace_id`` is set by the URL routing
    (slug → workspace) for human users.
    """

    user: "User"
    workspace_id: str

    @property
    def kind(self) -> str:
        return "user"


@dataclass(frozen=True)
class ServicePrincipal_:
    """A request authenticated as a :class:`ServicePrincipal`.

    The trailing underscore avoids shadowing the import name. The
    dataclass holds the SP model so the authorize() chain can reuse the
    manager caches for scopes / grants. ``workspace_id`` is fixed by the
    SP itself — tokens cannot cross workspaces.
    """

    service_principal: "ServicePrincipal"

    @property
    def kind(self) -> str:
        return "service"

    @property
    def workspace_id(self) -> str:
        return str(self.service_principal.workspace_id)


# Friendly aliases for callers that don't want the trailing underscore.
ServicePrincipalAuthz = ServicePrincipal_