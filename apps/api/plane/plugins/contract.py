# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The kernel contract: version, canonical hashing, version-range matching.

Design §6: ``kernel_contract`` in a manifest is a version *range* that the
kernel intersects with ``KERNEL_CONTRACT_VERSION``; ``contract_hash`` is
written by the kernel at install time and is never trusted from the manifest.

``KERNEL_CONTRACT_HASH`` is derived from ``KERNEL_CONTRACT_DESCRIPTOR`` at
import time rather than hardcoded, so it cannot drift from the contract it
claims to identify: touching any part of the contract surface (kinds,
states, audit actions, contribution kinds/runtimes, granted scopes, error
codes, manifest fields) changes the hash and is thereby detectable on every
installed plugin.
"""

from __future__ import annotations

import hashlib
import json
import re

from .constants import (
    AUDIT_ACTION_CHOICES,
    ACTOR_KIND_CHOICES,
    CONTRIBUTION_KINDS,
    CONTRIBUTION_RUNTIMES,
    ERROR_CODES,
    KIND_CHOICES,
    KERNEL_CONTRACT_VERSION,
    MANIFEST_SCHEMA_VERSION,
    STATE_CHOICES,
    WORKSPACE_GRANTED_SCOPES,
)

#: Top-level members a manifest v0 may carry. Anything else is
#: ``manifest_invalid`` — this is what makes "import: manifest only, no
#: code" a property of the schema instead of a convention: there is no
#: member that can carry code.
MANIFEST_FIELDS = frozenset(
    {
        "manifest_version",
        "key",
        "name",
        "description",
        "version",
        "kind",
        "kernel_contract",
        "publisher",
        "scopes",
        "contributions",
        "config_schema",
        "limits",
    }
)

#: The contract surface as data. Every member of it is part of what
#: ``contract_hash`` identifies.
KERNEL_CONTRACT_DESCRIPTOR = {
    "kernel_contract_version": KERNEL_CONTRACT_VERSION,
    "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
    "manifest_fields": sorted(MANIFEST_FIELDS),
    "plugin_kinds": [choice for choice, _label in KIND_CHOICES],
    "plugin_states": [choice for choice, _label in STATE_CHOICES],
    "audit_actions": [choice for choice, _label in AUDIT_ACTION_CHOICES],
    "actor_kinds": [choice for choice, _label in ACTOR_KIND_CHOICES],
    "contribution_kinds": list(CONTRIBUTION_KINDS),
    "contribution_runtimes": list(CONTRIBUTION_RUNTIMES),
    "granted_scopes": sorted(WORKSPACE_GRANTED_SCOPES),
    "error_codes": sorted(ERROR_CODES.values()),
}


def canonical_json(payload) -> str:
    """Deterministic JSON rendering used for every hash in this app.

    Sorted keys, no insignificant whitespace, UTF-8 preserved as-is so a
    non-ASCII plugin name hashes the same wherever it is written.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_manifest(payload) -> str:
    """sha256 of ``payload``'s canonical JSON.

    Used both for ``Plugin.manifest_hash`` (manifest normalised per design
    §4.1) and for ``KERNEL_CONTRACT_HASH`` (the contract descriptor).
    """
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


KERNEL_CONTRACT_HASH = hash_manifest(KERNEL_CONTRACT_DESCRIPTOR)


_COMPARATOR = re.compile(r"^(>=|<=|>|<|==|=)?\s*(\d+(?:\.\d+)*)$")


def version_tuple(version: str) -> tuple[int, ...]:
    """Parse ``0.1`` / ``0.1.2`` into a comparable tuple."""
    return tuple(int(part) for part in version.split("."))


def parse_requirement(requirement: str) -> list[tuple[str, tuple[int, ...]]]:
    """Parse a whitespace-separated comparator set, e.g. ``">=0.1 <0.2"``.

    A bare version (``"0.1"``) means equality. Raises ``ValueError`` for
    anything the v0 grammar does not cover, so the caller can answer
    ``manifest_invalid`` instead of silently accepting a range the kernel
    cannot evaluate.
    """
    if not isinstance(requirement, str) or not requirement.strip():
        raise ValueError("kernel_contract must be a non-empty version range")
    comparators = []
    for part in requirement.split():
        match = _COMPARATOR.match(part)
        if match is None:
            raise ValueError(f"unsupported kernel_contract comparator: {part!r}")
        operator = match.group(1) or "="
        if operator == "==":
            operator = "="
        comparators.append((operator, version_tuple(match.group(2))))
    return comparators


def requirement_satisfied(requirement: str, version: str) -> bool:
    """True when ``version`` falls inside ``requirement``."""
    candidate = version_tuple(version)
    for operator, bound in parse_requirement(requirement):
        if operator == "=" and candidate != bound:
            return False
        if operator == ">=" and candidate < bound:
            return False
        if operator == ">" and candidate <= bound:
            return False
        if operator == "<=" and candidate > bound:
            return False
        if operator == "<" and candidate >= bound:
            return False
    return True


def kernel_contract_allows(requirement: str) -> bool:
    """True when the manifest's ``kernel_contract`` covers this kernel.

    Raises ``ValueError`` when the range is unparseable (``manifest_invalid``).
    """
    return requirement_satisfied(requirement, KERNEL_CONTRACT_VERSION)
