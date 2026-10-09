# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Decision object returned by :func:`authorize`.

The chain returns a :class:`Decision` rather than raising so that callers can
distinguish "this is allowed" from "this is allowed but only because the
owner intersection was tight". Tests assert on the reason; HTTP layers map
the reason to a 403/404 the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# Stable reason codes — callers may switch on these.
ALLOWED = "allowed"
DENY_NO_PRINCIPAL = "deny_no_principal"
DENY_UNREGISTERED = "deny_unregistered"
DENY_INACTIVE_PRINCIPAL = "deny_inactive_principal"
DENY_INACTIVE_OWNER = "deny_inactive_owner"
DENY_SCOPE_MISS = "deny_scope_miss"
DENY_GRANT_MISS = "deny_grant_miss"
DENY_WORKSPACE_LEVEL_WRITE = "deny_workspace_level_write"
DENY_ROLE_CAP = "deny_role_cap"
DENY_OWNER_ROLE = "deny_owner_role"
DENY_OWNER_NOT_WORKSPACE_MEMBER = "deny_owner_not_workspace_member"


@dataclass(frozen=True)
class Decision:
    """The outcome of one authorization call.

    Attributes:
        allowed: True when every step of the chain passed.
        reason: One of the ``DENY_*`` / ``ALLOWED`` constants above.
        detail: Human-readable string suitable for logs and 403 messages.
        effective_role: The role the principal will act with, computed at the
            owner-intersection step. ``None`` on a deny.
    """

    allowed: bool
    reason: str
    detail: str = ""
    effective_role: Optional[int] = None

    @classmethod
    def allow(cls, effective_role: Optional[int] = None) -> "Decision":
        return cls(allowed=True, reason=ALLOWED, effective_role=effective_role)

    @classmethod
    def deny(cls, reason: str, detail: str = "") -> "Decision":
        return cls(allowed=False, reason=reason, detail=detail)

    def __bool__(self) -> bool:  # pragma: no cover — convenience only
        return self.allowed