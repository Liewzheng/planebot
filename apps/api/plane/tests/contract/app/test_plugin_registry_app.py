# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: plugin registry management surface and lifecycle.

Covers §4–§6, §7.3 and §10 of the plugin platform design (page 31a4dae6):

* install / enable / disable / uninstall / import / manifest export,
* every management refusal (package kind, over-scoped manifest, contract
  mismatch, missing uninstall confirmation/reason, illegal transitions,
  a contribution without a ``runtime``),
* exactly one audit row per state transition, in order,
* workspace isolation and the admin-only / no-service-principal boundary.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import APIToken, User, Workspace, WorkspaceMember
from plane.plugins.constants import (
    AuditAction,
    ErrorCode,
    PluginKind,
    PluginState,
    DELETED_STATE,
)
from plane.plugins.contract import KERNEL_CONTRACT_HASH, hash_manifest
from plane.plugins.models import Plugin, PluginAuditEvent, PluginConfig
from plane.service_principals.constants import PrincipalType
from plane.service_principals.models import ServicePrincipal

PLUGIN_KEY = "pdf-templates"


def plugins_url(slug):
    return f"/api/workspaces/{slug}/plugins/"


def plugin_url(slug, key, suffix=""):
    return f"/api/workspaces/{slug}/plugins/{key}/{suffix}"


def build_manifest(**overrides):
    """A manifest that satisfies every v0 rule (design §6)."""
    manifest = {
        "manifest_version": "0.1",
        "key": PLUGIN_KEY,
        "name": "PDF 模板导出",
        "description": "test plugin",
        "version": "1.0.0",
        "kind": PluginKind.BUILTIN,
        "kernel_contract": ">=0.1 <0.2",
        "publisher": {"name": "pbot", "key_id": "pbot-incident-2026"},
        "scopes": ["projects.test_cases:read", "workspaces.files:write"],
        "contributions": {
            "settings_pages": [
                {
                    "id": "pdf-templates.settings",
                    "runtime": "server",
                    "title_key": "plugin.pdf_templates.settings_title",
                    "permission": "workspace_admin",
                    "route": "/settings/plugins/pdf-templates",
                }
            ],
            "export_renderers": [
                {
                    "id": "pdf-templates.renderer",
                    "runtime": "server",
                    "input": "test_run_export@1",
                    "output": "application/pdf",
                    "options_schema": {"template_id": "string?"},
                }
            ],
        },
        "config_schema": {
            "type": "object",
            "properties": {
                "template_id": {"type": "string"},
                "orientation": {"type": "string", "enum": ["portrait", "landscape"]},
            },
            "required": ["template_id"],
            "additionalProperties": False,
        },
        "limits": {"storage_mb": 50, "render_timeout_seconds": 30},
    }
    manifest.update(overrides)
    return manifest


def install(client, slug, **overrides):
    return client.post(
        plugins_url(slug), {"manifest": build_manifest(**overrides)}, format="json"
    )


def audit_rows(workspace, key=PLUGIN_KEY):
    return list(
        PluginAuditEvent.objects.filter(workspace_id=workspace.id, plugin_key=key)
    )


def audit_transitions(workspace, key=PLUGIN_KEY):
    return [(row.action, row.from_state, row.to_state) for row in audit_rows(workspace, key)]


@pytest.fixture
def member_user(db, workspace):
    """A workspace MEMBER (role=15) — not allowed to write."""
    user = User.objects.create(email="member-plugins@plane.so", username="plugin-member")
    user.set_password("password")
    user.save()
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=15, is_active=True)
    return user


@pytest.fixture
def sp_client(db, workspace, create_user):
    """An API client authenticated with a service-principal token."""
    sp = ServicePrincipal.objects.create(workspace=workspace, owner=create_user, name="plugin-bot")
    token = APIToken.objects.create(
        user=create_user,
        label="svc:plugin-bot",
        user_type=1,
        is_service=True,
        principal_type=PrincipalType.SERVICE,
        service_principal=sp,
        workspace=workspace,
        token="plane_svc_plugin_test",
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=token.token)
    return client


@pytest.fixture
def installed(session_client, workspace):
    response = install(session_client, workspace.slug)
    assert response.status_code == status.HTTP_201_CREATED, response.data
    return Plugin.objects.get(workspace=workspace, key=PLUGIN_KEY)


@pytest.mark.contract
@pytest.mark.django_db
class TestPluginInstall:
    def test_install_lands_in_installed_state(self, session_client, workspace, create_user):
        response = install(session_client, workspace.slug)
        assert response.status_code == status.HTTP_201_CREATED, response.data

        plugin = Plugin.objects.get(workspace=workspace, key=PLUGIN_KEY)
        assert plugin.state == PluginState.INSTALLED
        assert plugin.kind == PluginKind.BUILTIN
        assert plugin.version == "1.0.0"
        assert plugin.name == "PDF 模板导出"
        assert plugin.installed_by_id == create_user.id
        assert plugin.installed_at is not None
        assert plugin.deleted_at is None
        # The kernel writes the hashes; the manifest is stored read-only.
        assert plugin.contract_hash == KERNEL_CONTRACT_HASH
        assert plugin.manifest_hash == hash_manifest(build_manifest())
        assert plugin.manifest["key"] == PLUGIN_KEY
        # The declared contribution runtime is stored, not executed (§7.3).
        assert plugin.manifest["contributions"]["settings_pages"][0]["runtime"] == "server"

    def test_install_writes_exactly_one_audit_row(self, session_client, workspace, create_user):
        install(session_client, workspace.slug)
        rows = audit_rows(workspace)
        assert len(rows) == 1
        row = rows[0]
        assert row.action == AuditAction.INSTALL
        assert row.from_state == ""
        assert row.to_state == PluginState.INSTALLED
        assert row.actor_kind == "human"
        assert row.actor_id == create_user.id
        assert row.plugin_version == "1.0.0"
        assert row.manifest_hash == hash_manifest(build_manifest())
        assert row.contract_hash == KERNEL_CONTRACT_HASH

    def test_installed_plugin_is_listed(self, session_client, workspace, installed):
        response = session_client.get(plugins_url(workspace.slug))
        assert response.status_code == status.HTTP_200_OK
        body = response.data
        assert len(body) == 1
        assert body[0]["key"] == PLUGIN_KEY
        assert body[0]["state"] == PluginState.INSTALLED
        assert body[0]["contract_drifted"] is False

    def test_install_twice_is_refused(self, session_client, workspace, installed):
        response = install(session_client, workspace.slug)
        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data["code"] == ErrorCode.ALREADY_INSTALLED
        assert len(audit_rows(workspace)) == 1

    def test_package_kind_is_refused(self, session_client, workspace):
        response = install(session_client, workspace.slug, kind=PluginKind.PACKAGE)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == ErrorCode.RUNTIME_UNAVAILABLE
        assert Plugin.all_objects.filter(workspace=workspace, key=PLUGIN_KEY).exists() is False
        assert audit_rows(workspace) == []

    def test_manifest_outside_workspace_grant_is_refused(self, session_client, workspace):
        response = install(session_client, workspace.slug, scopes=["projects.documents:write"])
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == ErrorCode.SCOPE_NOT_GRANTED
        assert Plugin.all_objects.filter(workspace=workspace, key=PLUGIN_KEY).exists() is False

    def test_contract_mismatch_is_refused(self, session_client, workspace):
        response = install(session_client, workspace.slug, kernel_contract="<0.1")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == ErrorCode.CONTRACT_MISMATCH
        assert audit_rows(workspace) == []

    def test_invalid_manifest_is_refused(self, session_client, workspace):
        # Key grammar from design §6: [a-z0-9-], 3–32 chars.
        response = install(session_client, workspace.slug, key="PDF Templates")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == ErrorCode.MANIFEST_INVALID

    def test_contribution_without_runtime_is_refused(self, session_client, workspace):
        contributions = build_manifest()["contributions"]
        del contributions["settings_pages"][0]["runtime"]
        response = install(session_client, workspace.slug, contributions=contributions)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == ErrorCode.MANIFEST_INVALID
        assert audit_rows(workspace) == []

    def test_contribution_with_unknown_runtime_is_refused(self, session_client, workspace):
        contributions = build_manifest()["contributions"]
        contributions["export_renderers"][0]["runtime"] = "browser"
        response = install(session_client, workspace.slug, contributions=contributions)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == ErrorCode.MANIFEST_INVALID

    def test_one_plugin_may_declare_both_runtimes(self, session_client, workspace):
        """A plugin may carry client AND server contributions (§7.3) — they
        share one key, one manifest and one audit trail."""
        contributions = {
            "settings_pages": [
                {"id": "mermaid.settings", "runtime": "client", "slot": "settings.project.panel", "title_key": "x"}
            ],
            "export_renderers": [
                {"id": "mermaid.renderer", "runtime": "server", "input": "page_content@1", "output": "image/svg+xml"}
            ],
        }
        response = install(
            session_client, workspace.slug, key="mermaid-pro", name="Mermaid",
            scopes=["pages.content:read"], contributions=contributions,
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        plugin = Plugin.objects.get(workspace=workspace, key="mermaid-pro")
        runtimes = {
            item["runtime"]
            for entries in plugin.manifest["contributions"].values()
            for item in entries
        }
        assert runtimes == {"client", "server"}

    def test_member_cannot_install(self, api_client, workspace, member_user):
        api_client.force_authenticate(user=member_user)
        response = install(api_client, workspace.slug)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert Plugin.all_objects.filter(workspace=workspace, key=PLUGIN_KEY).exists() is False


@pytest.mark.contract
@pytest.mark.django_db
class TestPluginLifecycle:
    def test_enable_moves_to_enabled(self, session_client, workspace, installed):
        response = session_client.post(plugin_url(workspace.slug, PLUGIN_KEY, "enable/"))
        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["state"] == PluginState.ENABLED

        plugin = Plugin.objects.get(workspace=workspace, key=PLUGIN_KEY)
        assert plugin.state == PluginState.ENABLED
        assert audit_transitions(workspace)[-1] == (
            AuditAction.ENABLE,
            PluginState.INSTALLED,
            PluginState.ENABLED,
        )

    def test_enable_twice_is_refused(self, session_client, workspace, installed):
        session_client.post(plugin_url(workspace.slug, PLUGIN_KEY, "enable/"))
        response = session_client.post(plugin_url(workspace.slug, PLUGIN_KEY, "enable/"))
        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data["code"] == ErrorCode.INVALID_STATE
        assert [r.action for r in audit_rows(workspace)] == [AuditAction.INSTALL, AuditAction.ENABLE]

    def test_disable_keeps_data_intact(self, session_client, workspace, installed):
        PluginConfig.objects.create(
            workspace=workspace, plugin_key=PLUGIN_KEY, config={"template_id": "a4"}
        )
        session_client.post(plugin_url(workspace.slug, PLUGIN_KEY, "enable/"))
        response = session_client.post(plugin_url(workspace.slug, PLUGIN_KEY, "disable/"))
        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["state"] == PluginState.DISABLED

        plugin = Plugin.objects.get(workspace=workspace, key=PLUGIN_KEY)
        assert plugin.state == PluginState.DISABLED
        # Disabling unmounts contributions, it does not drop data (design §5).
        assert plugin.manifest == build_manifest()
        assert PluginConfig.objects.get(workspace=workspace, plugin_key=PLUGIN_KEY).config == {
            "template_id": "a4"
        }
        assert audit_transitions(workspace)[-1] == (
            AuditAction.DISABLE,
            PluginState.ENABLED,
            PluginState.DISABLED,
        )

    def test_disable_requires_enabled_state(self, session_client, workspace, installed):
        response = session_client.post(plugin_url(workspace.slug, PLUGIN_KEY, "disable/"))
        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data["code"] == ErrorCode.INVALID_STATE
        assert [r.action for r in audit_rows(workspace)] == [AuditAction.INSTALL]

    def test_one_audit_row_per_transition_in_order(self, session_client, workspace, installed):
        session_client.post(plugin_url(workspace.slug, PLUGIN_KEY, "enable/"))
        session_client.post(plugin_url(workspace.slug, PLUGIN_KEY, "disable/"))
        response = session_client.post(
            plugin_url(workspace.slug, PLUGIN_KEY, "uninstall/"),
            {"confirm": PLUGIN_KEY, "reason": "evaluated, using a fork"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data

        assert audit_transitions(workspace) == [
            (AuditAction.INSTALL, "", PluginState.INSTALLED),
            (AuditAction.ENABLE, PluginState.INSTALLED, PluginState.ENABLED),
            (AuditAction.DISABLE, PluginState.ENABLED, PluginState.DISABLED),
            (AuditAction.UNINSTALL, PluginState.DISABLED, DELETED_STATE),
        ]
        # Monotonic ids keep the history sortable (design §4.2).
        ids = [row.id for row in audit_rows(workspace)]
        assert ids == sorted(ids)


@pytest.mark.contract
@pytest.mark.django_db
class TestPluginUninstall:
    def test_uninstall_without_confirmation_is_refused(self, session_client, workspace, installed):
        response = session_client.post(
            plugin_url(workspace.slug, PLUGIN_KEY, "uninstall/"),
            {"confirm": "not-the-key", "reason": "because"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == ErrorCode.CONFIRMATION_REQUIRED
        assert Plugin.objects.filter(pk=installed.pk).exists()

    def test_uninstall_without_reason_is_refused(self, session_client, workspace, installed):
        response = session_client.post(
            plugin_url(workspace.slug, PLUGIN_KEY, "uninstall/"),
            {"confirm": PLUGIN_KEY, "reason": "   "},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == ErrorCode.REASON_REQUIRED
        assert Plugin.objects.filter(pk=installed.pk).exists()

    def test_uninstall_soft_deletes_and_keeps_key_reserved(self, session_client, workspace, installed):
        response = session_client.post(
            plugin_url(workspace.slug, PLUGIN_KEY, "uninstall/"),
            {"confirm": PLUGIN_KEY, "reason": "switching implementation"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data

        plugin = Plugin.all_objects.get(pk=installed.pk)
        assert plugin.deleted_at is not None
        assert plugin.state == PluginState.INSTALLED
        # Gone from the live listing.
        assert session_client.get(plugins_url(workspace.slug)).data == []
        # The key stays reserved for the retention period (design §6/§10).
        response = install(session_client, workspace.slug)
        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data["code"] == ErrorCode.KEY_TAKEN
        # The audit row carries the human reason.
        final = audit_rows(workspace)[-1]
        assert final.action == AuditAction.UNINSTALL
        assert final.to_state == DELETED_STATE
        assert final.reason == "switching implementation"

    def test_uninstall_from_enabled_disables_first(
        self, session_client, workspace, installed
    ):
        session_client.post(plugin_url(workspace.slug, PLUGIN_KEY, "enable/"))
        response = session_client.post(
            plugin_url(workspace.slug, PLUGIN_KEY, "uninstall/"),
            {"confirm": PLUGIN_KEY, "reason": "retired"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        # Unmount contributions first (one disable transition), then the
        # delete transition — one audit row each.
        assert audit_transitions(workspace)[-2:] == [
            (AuditAction.DISABLE, PluginState.ENABLED, PluginState.DISABLED),
            (AuditAction.UNINSTALL, PluginState.DISABLED, DELETED_STATE),
        ]

    def test_uninstall_unknown_key_is_not_found(self, session_client, workspace):
        response = session_client.post(
            plugin_url(workspace.slug, "mermaid", "uninstall/"),
            {"confirm": "mermaid", "reason": "x"},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data["code"] == ErrorCode.NOT_FOUND


@pytest.mark.contract
@pytest.mark.django_db
class TestPluginImportAndExport:
    def test_import_registers_manifest_only(self, session_client, workspace):
        response = session_client.post(
            f"{plugins_url(workspace.slug)}import/",
            {"manifest": build_manifest(key="mermaid-pro", name="Advanced Mermaid")},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        plugin = Plugin.objects.get(workspace=workspace, key="mermaid-pro")
        assert plugin.state == PluginState.INSTALLED
        row = audit_rows(workspace, key="mermaid-pro")[0]
        assert row.action == AuditAction.IMPORT
        assert row.to_state == PluginState.INSTALLED

    def test_import_refuses_code(self, session_client, workspace):
        manifest = build_manifest(key="mermaid-pro")
        manifest["code"] = "print('not accepted')"
        response = session_client.post(
            f"{plugins_url(workspace.slug)}import/", {"manifest": manifest}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == ErrorCode.MANIFEST_INVALID
        assert not Plugin.all_objects.filter(workspace=workspace, key="mermaid-pro").exists()

    def test_import_refuses_package(self, session_client, workspace):
        response = session_client.post(
            f"{plugins_url(workspace.slug)}import/",
            {"manifest": build_manifest(key="remote-plug", kind=PluginKind.PACKAGE)},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == ErrorCode.RUNTIME_UNAVAILABLE

    def test_manifest_export_returns_stored_manifest(
        self, session_client, workspace, installed
    ):
        response = session_client.get(plugin_url(workspace.slug, PLUGIN_KEY, "manifest/"))
        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["manifest"] == build_manifest()
        assert response.data["manifest_hash"] == hash_manifest(build_manifest())
        assert response.data["contract_hash"] == KERNEL_CONTRACT_HASH
        assert response.data["kernel_contract_hash"] == KERNEL_CONTRACT_HASH
        assert response.data["contract_drifted"] is False

    def test_manifest_export_unknown_key_is_not_found(self, session_client, workspace):
        response = session_client.get(plugin_url(workspace.slug, "nope", "manifest/"))
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data["code"] == ErrorCode.NOT_FOUND


@pytest.mark.contract
@pytest.mark.django_db
class TestPluginAuditListing:
    def test_audit_listing_is_ordered_and_filterable(self, session_client, workspace, installed):
        session_client.post(plugin_url(workspace.slug, PLUGIN_KEY, "enable/"))

        response = session_client.get(f"{plugins_url(workspace.slug)}audit/")
        assert response.status_code == status.HTTP_200_OK
        assert [row["action"] for row in response.data] == [
            AuditAction.INSTALL,
            AuditAction.ENABLE,
        ]
        assert [row["id"] for row in response.data] == sorted(
            row["id"] for row in response.data
        )

        response = session_client.get(f"{plugins_url(workspace.slug)}audit/?key={PLUGIN_KEY}")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        response = session_client.get(f"{plugins_url(workspace.slug)}audit/?key=absent")
        assert response.data == []

    def test_audit_listing_is_read_only(self, session_client, workspace, installed):
        url = f"{plugins_url(workspace.slug)}audit/"
        assert session_client.put(url, {}, format="json").status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        assert session_client.delete(url).status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_audit_listing_requires_admin(self, api_client, workspace, installed, member_user):
        api_client.force_authenticate(user=member_user)
        assert api_client.get(f"{plugins_url(workspace.slug)}audit/").status_code == (
            status.HTTP_403_FORBIDDEN
        )


@pytest.mark.contract
@pytest.mark.django_db
class TestPluginWorkspaceIsolation:
    def test_other_workspace_does_not_list_the_plugin(
        self, session_client, workspace, installed, create_user
    ):
        other = Workspace.objects.create(name="Other", owner=create_user, slug="other-plugins")
        WorkspaceMember.objects.create(workspace=other, member=create_user, role=20, is_active=True)

        response = session_client.get(plugins_url(other.slug))
        assert response.status_code == status.HTTP_200_OK
        assert response.data == []

        response = session_client.get(plugin_url(other.slug, PLUGIN_KEY, "manifest/"))
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data["code"] == ErrorCode.NOT_FOUND

        response = session_client.post(plugin_url(other.slug, PLUGIN_KEY, "enable/"))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_same_key_can_be_installed_in_another_workspace(
        self, session_client, workspace, installed, create_user
    ):
        other = Workspace.objects.create(name="Other", owner=create_user, slug="other-plugins")
        WorkspaceMember.objects.create(workspace=other, member=create_user, role=20, is_active=True)
        response = install(session_client, other.slug)
        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert Plugin.objects.filter(workspace=other, key=PLUGIN_KEY).count() == 1
        assert PluginAuditEvent.objects.filter(workspace_id=other.id).count() == 1


@pytest.mark.contract
@pytest.mark.django_db
class TestPluginServicePrincipalBoundary:
    def test_service_principal_write_is_refused(self, sp_client, workspace):
        response = install(sp_client, workspace.slug)
        assert response.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_401_UNAUTHORIZED,
        )
        assert not Plugin.all_objects.filter(workspace=workspace).exists()
        assert audit_rows(workspace, key=PLUGIN_KEY) == []

    def test_service_principal_lifecycle_write_is_refused(
        self, sp_client, session_client, workspace, installed
    ):
        response = sp_client.post(plugin_url(workspace.slug, PLUGIN_KEY, "uninstall/"))
        assert response.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_401_UNAUTHORIZED,
        )
        assert Plugin.all_objects.get(pk=installed.pk).deleted_at is None


@pytest.mark.contract
@pytest.mark.django_db
class TestPluginContractDrift:
    def test_drift_is_detectable_and_enable_refuses(
        self, session_client, workspace, installed
    ):
        # Simulate a kernel contract bump: the stored hash no longer matches
        # the current one. P1 only detects and records (design §5, §14 P1).
        Plugin.all_objects.filter(pk=installed.pk).update(contract_hash="0" * 64)

        response = session_client.get(plugins_url(workspace.slug))
        assert response.data[0]["contract_drifted"] is True

        response = session_client.post(plugin_url(workspace.slug, PLUGIN_KEY, "enable/"))
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == ErrorCode.CONTRACT_MISMATCH
        assert Plugin.objects.get(pk=installed.pk).state == PluginState.INSTALLED

    def test_kernel_contract_hash_is_derived_from_the_contract(self):
        from plane.plugins.contract import KERNEL_CONTRACT_DESCRIPTOR

        assert KERNEL_CONTRACT_HASH == hash_manifest(KERNEL_CONTRACT_DESCRIPTOR)
        assert len(KERNEL_CONTRACT_HASH) == 64


@pytest.mark.contract
@pytest.mark.django_db
class TestPluginConfigModel:
    def test_config_is_validated_against_manifest_schema(self, workspace, installed):
        PluginConfig.objects.create(
            workspace=workspace, plugin_key=PLUGIN_KEY, config={"template_id": "a4"}
        )
        with pytest.raises(ValidationError):
            PluginConfig.objects.create(
                workspace=workspace, plugin_key=PLUGIN_KEY, config={"template_id": 7}
            )
        with pytest.raises(ValidationError):
            PluginConfig.objects.create(
                workspace=workspace, plugin_key=PLUGIN_KEY, config={"unknown": True}
            )
        with pytest.raises(ValidationError):
            # "required": ["template_id"]
            PluginConfig.objects.create(
                workspace=workspace, plugin_key=PLUGIN_KEY, config={}
            )
