/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo, useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
// plane imports
import { WarningTriangleOutline } from "@makeplane/propel/icons";
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { EmptyStateCompact } from "@plane/propel/empty-state";
import { Spinner } from "@plane/propel/spinners";
// components
import { NotAuthorizedView } from "@/components/auth-screens/not-authorized-view";
import { PageHead } from "@/components/core/page-title";
import { AddPluginModal, PluginRemovedSection, PluginsList } from "@/components/plugins";
import { PLUGINS_LIST } from "@/components/plugins/constants";
import { SettingsContentWrapper } from "@/components/settings/content-wrapper";
import { SettingsHeading } from "@/components/settings/heading";
// hooks
import { useWorkspace } from "@/hooks/store/use-workspace";
import { useUserPermissions } from "@/hooks/store/user";
// services
import { pluginService } from "@/services/plugins";
// local imports
import type { Route } from "./+types/page";
import { PluginsWorkspaceSettingsHeader } from "./header";

/**
 * Workspace plugin management (design §7/§14 P1).
 *
 * The page is admin-only, matching the server: writes and the audit listing are
 * `ROLE.ADMIN`, so a member would only ever reach a 403. The list itself is the
 * registry's live view; soft-deleted plugins are gone from it, which is why the
 * audit trail below is a separate, always-visible record rather than a detail
 * attached to a row that no longer exists.
 */
function WorkspacePluginsSettingsPage({ params }: Route.ComponentProps) {
  // router
  const { workspaceSlug } = params;
  // states
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { workspaceUserInfo, allowPermissions } = useUserPermissions();
  const { currentWorkspace } = useWorkspace();
  // derived values
  const canManagePlugins = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);
  const pageTitle = currentWorkspace?.name
    ? `${currentWorkspace.name} - ${t("workspace_settings.settings.plugins.title")}`
    : undefined;

  const {
    data: plugins,
    isLoading,
    error,
    mutate,
  } = useSWR(canManagePlugins ? PLUGINS_LIST(workspaceSlug) : null, () =>
    pluginService.fetchPluginsList(workspaceSlug)
  );

  const activeKeys = useMemo(() => (plugins ?? []).map((plugin) => plugin.key), [plugins]);

  if (workspaceUserInfo && !canManagePlugins) {
    return <NotAuthorizedView section="settings" className="h-auto" />;
  }

  return (
    <SettingsContentWrapper header={<PluginsWorkspaceSettingsHeader />}>
      <PageHead title={pageTitle} />
      <div className="w-full">
        <AddPluginModal
          isOpen={isAddModalOpen}
          onClose={() => setIsAddModalOpen(false)}
          workspaceSlug={workspaceSlug}
        />
        <SettingsHeading
          title={t("workspace_settings.settings.plugins.title")}
          description={t("workspace_settings.settings.plugins.description")}
          control={
            <Button variant="primary" size="lg" onClick={() => setIsAddModalOpen(true)}>
              {t("workspace_settings.settings.plugins.install_plugin")}
            </Button>
          }
        />

        {error ? (
          <div className="flex h-full w-full flex-col items-center justify-center gap-3 py-20 text-center">
            <WarningTriangleOutline className="size-8 text-tertiary" />
            <p className="text-14 text-secondary">
              {t("workspace_settings.settings.plugins.toasts.load_failed.message")}
            </p>
            <Button variant="secondary" size="sm" onClick={() => mutate()}>
              {t("common.retry")}
            </Button>
          </div>
        ) : isLoading || !plugins ? (
          <div className="flex items-center justify-center py-20">
            <Spinner width="24px" height="24px" />
          </div>
        ) : plugins.length > 0 ? (
          <div className="mt-4">
            <PluginsList plugins={plugins} workspaceSlug={workspaceSlug} />
          </div>
        ) : (
          <div className="flex h-full w-full flex-col">
            <div className="flex h-full w-full items-center justify-center">
              <EmptyStateCompact
                assetKey="settings"
                title={t("settings_empty_state.plugins.title")}
                description={t("settings_empty_state.plugins.description")}
                actions={[
                  {
                    label: t("settings_empty_state.plugins.cta_primary"),
                    onClick: () => {
                      setIsAddModalOpen(true);
                    },
                  },
                ]}
                align="start"
                rootClassName="py-20"
              />
            </div>
          </div>
        )}

        <div className="mt-6">
          <PluginRemovedSection workspaceSlug={workspaceSlug} activeKeys={activeKeys} />
        </div>
      </div>
    </SettingsContentWrapper>
  );
}

export default observer(WorkspacePluginsSettingsPage);
