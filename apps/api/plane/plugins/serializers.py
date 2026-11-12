# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Serializers for the plugin registry management endpoints."""

from rest_framework import serializers

from .models import Plugin, PluginAuditEvent


class PluginSerializer(serializers.ModelSerializer):
    #: Derived from the stored contract hash vs the current kernel contract —
    #: P1's whole "detect a contract change" surface.
    contract_drifted = serializers.BooleanField(read_only=True)

    class Meta:
        model = Plugin
        fields = [
            "id",
            "key",
            "name",
            "description",
            "version",
            "kind",
            "state",
            "manifest_hash",
            "contract_hash",
            "contract_drifted",
            "installed_by",
            "installed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class PluginInstallSerializer(serializers.Serializer):
    manifest = serializers.JSONField(required=True)


class PluginUninstallSerializer(serializers.Serializer):
    # Both checks produce registry error codes rather than DRF field errors,
    # so they are validated in the service, not here.
    confirm = serializers.CharField(required=False, allow_blank=True, default="")
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class PluginAuditEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = PluginAuditEvent
        fields = [
            "id",
            "workspace_id",
            "plugin_key",
            "plugin_version",
            "action",
            "from_state",
            "to_state",
            "actor_id",
            "actor_kind",
            "at",
            "manifest_hash",
            "contract_hash",
            "reason",
            "request_id",
        ]
        read_only_fields = fields
