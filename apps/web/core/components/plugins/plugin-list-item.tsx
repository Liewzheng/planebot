/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { mutate } from "swr";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Badge } from "@plane/propel/badge";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { renderFormattedDate } from "@plane/utils";
// hooks
import { useMember } from "@/hooks/store/use-member";
// services
import { pluginService } from "@/services/plugins";
import type { TPlugin } from "@/services/plugins";
// local imports
import { PLUGINS_LIST, PLUGIN_AUDIT, PLUGIN_AUDIT_LIST } from "./constants";
import { PluginKindBadge, PluginStateBadge } from "./plugin-badges";
import { PluginAuditPanel } from "./plugin-audit-panel";
import { PluginManifestModal } from "./plugin-manifest-modal";
import { UninstallPluginModal } from "./uninstall-plugin-modal";
import { exportPluginManifest, getMemberDisplayName, getPluginErrorSentence } from "./helper";

type Props = {
  plugin: TPlugin;
  workspaceSlug: string;
};

/**
 * One installed plugin. Every action is a labelled button (no icon-only
 * controls) because the enable/disable pair is the page's primary verb, and the
 * trail is fetched only when a reader asks for it.
 */
export function PluginListItem({ plugin, workspaceSlug }: Props) {
  // states
  const [isToggling, setIsToggling] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [isAuditVisible, setIsAuditVisible] = useState(false);
  const [isManifestOpen, setIsManifestOpen] = useState(false);
  const [isUninstallOpen, setIsUninstallOpen] = useState(false);
  // hooks
  const { t } = useTranslation();
  const { getWorkspaceMemberDetails } = useMember().workspace;
  // derived values
  const isEnabled = plugin.state === "enabled";
  const installedByName = plugin.installed_by
    ? getMemberDisplayName(getWorkspaceMemberDetails(plugin.installed_by)?.member)
    : null;

  const handleToggle = async () => {
    if (isToggling) return;
    setIsToggling(true);
    try {
      if (isEnabled) await pluginService.disablePlugin(workspaceSlug, plugin.key);
      else await pluginService.enablePlugin(workspaceSlug, plugin.key);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(
          isEnabled
            ? "workspace_settings.settings.plugins.toasts.disabled.title"
            : "workspace_settings.settings.plugins.toasts.enabled.title"
        ),
        message: t(
          isEnabled
            ? "workspace_settings.settings.plugins.toasts.disabled.message"
            : "workspace_settings.settings.plugins.toasts.enabled.message"
        ),
      });
      void mutate(PLUGINS_LIST(workspaceSlug));
      void mutate(PLUGIN_AUDIT_LIST(workspaceSlug));
      void mutate(PLUGIN_AUDIT(workspaceSlug, plugin.key));
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(
          isEnabled
            ? "workspace_settings.settings.plugins.toasts.not_disabled.title"
            : "workspace_settings.settings.plugins.toasts.not_enabled.title"
        ),
        message:
          getPluginErrorSentence(error) ??
          t(
            isEnabled
              ? "workspace_settings.settings.plugins.toasts.not_disabled.message"
              : "workspace_settings.settings.plugins.toasts.not_enabled.message"
          ),
      });
    } finally {
      setIsToggling(false);
    }
  };

  const handleExport = async () => {
    if (isExporting) return;
    setIsExporting(true);
    try {
      await exportPluginManifest(workspaceSlug, plugin);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("workspace_settings.settings.plugins.toasts.manifest_exported.title"),
        message: t("workspace_settings.settings.plugins.toasts.manifest_exported.message", {
          filename: `${plugin.key}-${plugin.version}.manifest.json`,
        }),
      });
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("workspace_settings.settings.plugins.toasts.manifest_export_failed.title"),
        message:
          getPluginErrorSentence(error) ??
          t("workspace_settings.settings.plugins.toasts.manifest_export_failed.message"),
      });
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <>
      <PluginManifestModal
        plugin={plugin}
        isOpen={isManifestOpen}
        onClose={() => setIsManifestOpen(false)}
        workspaceSlug={workspaceSlug}
      />
      <UninstallPluginModal
        plugin={plugin}
        isOpen={isUninstallOpen}
        onClose={() => setIsUninstallOpen(false)}
        workspaceSlug={workspaceSlug}
      />

      <div className="flex flex-col gap-3 rounded-lg border border-subtle bg-layer-2 px-4 py-3">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h5 className="truncate text-body-sm-medium">{plugin.name}</h5>
              <PluginKindBadge kind={plugin.kind} />
              <PluginStateBadge state={plugin.state} />
              {plugin.contract_drifted && (
                <Badge variant="danger" size="base">
                  {t("workspace_settings.settings.plugins.list.contract_drifted_badge")}
                </Badge>
              )}
            </div>
            {plugin.description && <p className="truncate text-11 text-placeholder">{plugin.description}</p>}
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-11 text-tertiary">
              <span className="font-mono">{plugin.key}</span>
              <span>v{plugin.version}</span>
              <span>
                {installedByName
                  ? t("workspace_settings.settings.plugins.list.installed_by", { name: installedByName })
                  : t("workspace_settings.settings.plugins.list.installed_by_unknown")}
              </span>
              <span>
                {t("workspace_settings.settings.plugins.list.installed_at", {
                  date: renderFormattedDate(plugin.installed_at, "MMM dd, yyyy HH:mm"),
                })}
              </span>
            </div>
          </div>

          <div className="flex flex-shrink-0 flex-wrap items-center justify-end gap-2">
            <Button variant="secondary" size="sm" onClick={() => void handleToggle()} loading={isToggling}>
              {isEnabled
                ? t("workspace_settings.settings.plugins.list.disable")
                : t("workspace_settings.settings.plugins.list.enable")}
            </Button>
            <Button variant="secondary" size="sm" onClick={() => setIsManifestOpen(true)}>
              {t("workspace_settings.settings.plugins.list.view_manifest")}
            </Button>
            <Button variant="secondary" size="sm" onClick={() => void handleExport()} loading={isExporting}>
              {t("workspace_settings.settings.plugins.list.export_manifest")}
            </Button>
            <Button variant="error-outline" size="sm" onClick={() => setIsUninstallOpen(true)}>
              {t("workspace_settings.settings.plugins.list.uninstall")}
            </Button>
          </div>
        </div>

        {plugin.state === "disabled" && (
          <p className="rounded-md bg-layer-3 px-3 py-2 text-11 text-secondary">
            {t("workspace_settings.settings.plugins.list.disabled_note")}
          </p>
        )}

        {plugin.contract_drifted && (
          <p className="rounded-md bg-danger-subtle px-3 py-2 text-11 text-danger-primary">
            {t("workspace_settings.settings.plugins.list.contract_drifted")}
          </p>
        )}

        <div className="flex items-center border-t border-subtle pt-3">
          <Button variant="ghost" size="sm" onClick={() => setIsAuditVisible((visible) => !visible)}>
            {isAuditVisible
              ? t("workspace_settings.settings.plugins.list.hide_audit")
              : t("workspace_settings.settings.plugins.list.show_audit")}
          </Button>
        </div>

        {isAuditVisible && <PluginAuditPanel pluginKey={plugin.key} workspaceSlug={workspaceSlug} />}
      </div>
    </>
  );
}
