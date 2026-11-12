# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Registry errors and their HTTP rendering.

One exception type carries the machine-readable error code from design §10
so the service layer stays transport-agnostic and the views only translate.
"""

from __future__ import annotations

from .constants import ERROR_HTTP_STATUS, ErrorCode


class PluginRegistryError(Exception):
    """A refusal from the registry, carrying a design §10 error code."""

    def __init__(self, code: str, message: str = "", *, details=None):
        self.code = code
        self.message = message or code
        self.details = details
        super().__init__(f"{code}: {self.message}")

    @property
    def http_status(self) -> int:
        return ERROR_HTTP_STATUS.get(self.code, 400)

    def as_body(self) -> dict:
        body = {"error": self.message, "code": self.code}
        if self.details:
            body["details"] = self.details
        return body


def not_found(key: str) -> PluginRegistryError:
    return PluginRegistryError(
        ErrorCode.NOT_FOUND, f"Plugin '{key}' is not installed in this workspace"
    )
