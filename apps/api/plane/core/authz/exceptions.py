# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Bridge from :class:`Decision` to DRF exceptions.

HTTP layers call :func:`enforce` at the top of every SP-authenticated view;
on a deny it raises :class:`PermissionDenied` with a stable, log-friendly
message. The M8/M9 wiring imports this; the unit tests use it to assert
that a deny short-circuits as expected.
"""

from __future__ import annotations

from rest_framework.exceptions import PermissionDenied

from .decision import Decision


def enforce(decision: Decision) -> None:
    """Raise :class:`PermissionDenied` if ``decision`` is a deny.

    Returns ``None`` on an allow. The exception message carries the reason
    and the human-readable detail; both are surfaced in the 403 body and
    in the server logs.
    """
    if not decision.allowed:
        raise PermissionDenied(detail=decision.detail or decision.reason)
    return None


def ensure_allowed(decision: Decision) -> None:
    """Same as :func:`enforce`, kept as a friendlier name for callers that
    prefer the imperative form.
    """
    enforce(decision)