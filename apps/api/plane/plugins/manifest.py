# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Manifest v0 validation (design §6) and plugin config validation (§4.3).

The manifest is language-neutral JSON — the kernel validates data shapes,
never a Python class — so a future non-Python implementation is unaffected
by anything in this module.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError

from .constants import (
    CONTRIBUTION_KINDS,
    CONTRIBUTION_RUNTIMES,
    ErrorCode,
    MANIFEST_SCHEMA_VERSION,
    PluginKind,
    WORKSPACE_GRANTED_SCOPES,
)
from .contract import MANIFEST_FIELDS, kernel_contract_allows
from .errors import PluginRegistryError

#: Design §6: ``[a-z0-9-]``, 3–32 characters.
KEY_PATTERN = re.compile(r"^[a-z0-9-]{3,32}$")
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
SCOPE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*:(read|write)$")

DEFAULT_CONFIG_SCHEMA = {"type": "object", "properties": {}}


def _invalid(message: str) -> PluginRegistryError:
    return PluginRegistryError(ErrorCode.MANIFEST_INVALID, message)


def _validate_contributions(contributions) -> None:
    """Validate the contribution blocks.

    Every contribution declares ``runtime`` (§7.3): it is required, so a
    contribution kind added later cannot silently default to one side. The
    kernel stores the declaration in the manifest and executes nothing.
    """
    if not isinstance(contributions, dict):
        raise _invalid("manifest.contributions must be an object")
    for kind, entries in contributions.items():
        if kind not in CONTRIBUTION_KINDS:
            raise _invalid(f"unknown contribution kind '{kind}'")
        if not isinstance(entries, list):
            raise _invalid(f"contributions.{kind} must be a list")
        seen_ids: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise _invalid(f"contributions.{kind} entries must be objects")
            contribution_id = entry.get("id")
            if not isinstance(contribution_id, str) or not contribution_id:
                raise _invalid(f"contributions.{kind} entry needs a non-empty 'id'")
            if contribution_id in seen_ids:
                raise _invalid(f"duplicate contribution id '{contribution_id}' in {kind}")
            seen_ids.add(contribution_id)
            runtime = entry.get("runtime")
            if runtime is None:
                raise _invalid(
                    f"contributions.{kind} entry '{contribution_id}' must declare "
                    f"'runtime' ({'|'.join(CONTRIBUTION_RUNTIMES)})"
                )
            if runtime not in CONTRIBUTION_RUNTIMES:
                raise _invalid(
                    f"contributions.{kind} entry '{contribution_id}' has unknown "
                    f"runtime '{runtime}'"
                )


def validate_manifest(manifest) -> dict:
    """Validate a manifest v0 and return a normalised copy of it.

    Raises :class:`PluginRegistryError` with the design §10 code for the
    first rule that fails. The returned copy is what gets persisted — the
    caller never gets to store an unvalidated manifest.
    """
    if not isinstance(manifest, dict):
        raise _invalid("manifest must be an object")

    unknown = sorted(set(manifest) - MANIFEST_FIELDS)
    if unknown:
        # Also the reason "import: manifest only, no code" holds — there is
        # no member a code bundle could travel in.
        raise _invalid(f"unknown manifest field(s): {', '.join(unknown)}")

    if manifest.get("manifest_version") != MANIFEST_SCHEMA_VERSION:
        raise _invalid(
            f"manifest_version must be '{MANIFEST_SCHEMA_VERSION}' "
            f"(got {manifest.get('manifest_version')!r})"
        )

    key = manifest.get("key")
    if not isinstance(key, str) or not KEY_PATTERN.match(key):
        raise _invalid("key must be 3-32 characters of [a-z0-9-]")

    name = manifest.get("name")
    if not isinstance(name, str) or not name.strip():
        raise _invalid("name must be a non-empty string")

    version = manifest.get("version")
    if not isinstance(version, str) or not SEMVER_PATTERN.match(version):
        raise _invalid("version must be a semantic version like 1.0.0")

    kind = manifest.get("kind")
    if kind == PluginKind.PACKAGE:
        # v0 ships no runtime: a package would execute third-party code on the
        # server or in the browser, and both are out of scope until Gate 3.
        raise PluginRegistryError(
            ErrorCode.RUNTIME_UNAVAILABLE,
            "kind=package plugins cannot be installed: the plugin runtime is not "
            "available in v0 (registry and lifecycle only)",
        )
    if kind != PluginKind.BUILTIN:
        raise _invalid(f"kind must be '{PluginKind.BUILTIN}' in v0 (got {kind!r})")

    kernel_contract = manifest.get("kernel_contract")
    try:
        compatible = kernel_contract_allows(kernel_contract)
    except ValueError as exc:
        raise _invalid(f"kernel_contract is not a usable version range: {exc}") from exc
    if not compatible:
        raise PluginRegistryError(
            ErrorCode.CONTRACT_MISMATCH,
            f"kernel_contract '{kernel_contract}' does not cover this kernel",
        )

    scopes = manifest.get("scopes")
    if not isinstance(scopes, list):
        raise _invalid("scopes must be a list of scope strings")
    for scope in scopes:
        if not isinstance(scope, str) or not SCOPE_PATTERN.match(scope):
            raise _invalid(f"malformed scope {scope!r} (expected '<namespace>:read|write>')")
    not_granted = sorted(set(scopes) - WORKSPACE_GRANTED_SCOPES)
    if not_granted:
        raise PluginRegistryError(
            ErrorCode.SCOPE_NOT_GRANTED,
            f"scope(s) not granted by this workspace: {', '.join(not_granted)}",
        )

    _validate_contributions(manifest.get("contributions"))

    config_schema = manifest.get("config_schema", DEFAULT_CONFIG_SCHEMA)
    if not isinstance(config_schema, dict) or config_schema.get("type", "object") != "object":
        raise _invalid("config_schema must be an object schema with type 'object'")

    publisher = manifest.get("publisher")
    if publisher is not None and (
        not isinstance(publisher, dict) or not isinstance(publisher.get("name"), str)
    ):
        raise _invalid("publisher must be an object with a string 'name'")

    limits = manifest.get("limits")
    if limits is not None:
        if not isinstance(limits, dict):
            raise _invalid("limits must be an object")
        for limit_key, limit_value in limits.items():
            if isinstance(limit_value, bool) or not isinstance(limit_value, (int, float)):
                raise _invalid(f"limits.{limit_key} must be a number")
            if limit_value < 0:
                raise _invalid(f"limits.{limit_key} must not be negative")

    normalized = dict(manifest)
    normalized.setdefault("description", "")
    normalized.setdefault("config_schema", DEFAULT_CONFIG_SCHEMA)
    return normalized


_TYPE_CHECKS = {
    "string": lambda value: isinstance(value, str),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "array": lambda value: isinstance(value, list),
    "object": lambda value: isinstance(value, dict),
}


def validate_config(config, schema) -> None:
    """Validate a plugin config against its manifest ``config_schema``.

    v0 supports the JSON Schema subset the design's examples use: object
    ``type``, ``properties``, ``required``, ``additionalProperties``, per
    property ``type`` and ``enum``. Raises ``django.ValidationError`` so the
    model layer refuses to store a config the manifest does not describe.
    """
    if not isinstance(schema, dict) or schema.get("type", "object") != "object":
        raise ValidationError("plugin has no usable config_schema")
    if not isinstance(config, dict):
        raise ValidationError("config must be an object")

    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        raise ValidationError("config_schema.properties must be an object")

    for required_key in schema.get("required") or []:
        if required_key not in config:
            raise ValidationError(f"config is missing required key '{required_key}'")

    if schema.get("additionalProperties", True) is False:
        unknown = sorted(set(config) - set(properties))
        if unknown:
            raise ValidationError(f"config has unknown key(s): {', '.join(unknown)}")

    for config_key, value in config.items():
        property_schema = properties.get(config_key)
        if not isinstance(property_schema, dict):
            continue
        expected_type = property_schema.get("type")
        check = _TYPE_CHECKS.get(expected_type)
        if check is not None and not check(value):
            raise ValidationError(
                f"config.{config_key} must be of type {expected_type}"
            )
        enum = property_schema.get("enum")
        if enum is not None and value not in enum:
            raise ValidationError(
                f"config.{config_key} must be one of {enum}"
            )
