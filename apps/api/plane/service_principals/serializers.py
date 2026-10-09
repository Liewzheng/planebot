# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Serializers for the SP management endpoints.

The token field is intentionally read-only and is only populated on the
create / rotate responses — listing and detail never expose it.
"""

from rest_framework import serializers

from plane.db.models import APIToken
from plane.service_principals.models import (
    ProjectGrant,
    ServicePrincipal,
    ServiceScope,
    WorkspaceSPSettings,
)

from .constants import ACTION_CHOICES, RESOURCE_CHOICES


class ServiceScopeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceScope
        fields = ["id", "project", "resource_type", "action"]
        read_only_fields = ["id"]


class ProjectGrantSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectGrant
        fields = ["id", "project", "role_cap", "is_active"]
        read_only_fields = ["id"]


class ServicePrincipalSerializer(serializers.ModelSerializer):
    scopes = ServiceScopeSerializer(many=True, read_only=True)
    grants = ProjectGrantSerializer(many=True, read_only=True)
    token_last_used = serializers.SerializerMethodField()

    class Meta:
        model = ServicePrincipal
        fields = [
            "id",
            "name",
            "description",
            "avatar",
            "is_active",
            "workspace",
            "owner",
            "scopes",
            "grants",
            "token_last_used",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "workspace",
            "owner",
            "scopes",
            "grants",
            "created_at",
            "updated_at",
        ]

    def get_token_last_used(self, obj):
        token = (
            APIToken.objects.filter(
                service_principal_id=obj.id,
                is_active=True,
                last_used__isnull=False,
            )
            .order_by("-last_used")
            .values_list("last_used", flat=True)
            .first()
        )
        return token


class ServicePrincipalCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    avatar = serializers.CharField(required=False, allow_blank=True, default="", max_length=800)


class ServicePrincipalUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    avatar = serializers.CharField(required=False, allow_blank=True, max_length=800)
    is_active = serializers.BooleanField(required=False)


class ServiceScopeInputSerializer(serializers.Serializer):
    project = serializers.UUIDField(required=False, allow_null=True, default=None)
    resource_type = serializers.ChoiceField(choices=[c[0] for c in RESOURCE_CHOICES])
    action = serializers.ChoiceField(choices=[c[0] for c in ACTION_CHOICES])


class ProjectGrantInputSerializer(serializers.Serializer):
    project = serializers.UUIDField(required=True)
    role_cap = serializers.ChoiceField(choices=[20, 15, 5], default=15)
    is_active = serializers.BooleanField(default=True)


class WorkspaceSPSettingsSerializer(serializers.ModelSerializer):
    """Read-only view of a workspace's SP settings.

    Accepts either a saved WorkspaceSPSettings row or None — when the row
    doesn't exist yet the read endpoint returns the model defaults without
    persisting a row. The frontend (M11) treats the absence of a row the same
    way, so this lazy default keeps GET/PATCH round-trips symmetric.
    """

    class Meta:
        model = WorkspaceSPSettings
        fields = ["sp_assignable", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]

    def to_representation(self, instance):
        if instance is None:
            return {"sp_assignable": False}
        return super().to_representation(instance)


class WorkspaceSPSettingsInputSerializer(serializers.Serializer):
    sp_assignable = serializers.BooleanField(required=True)