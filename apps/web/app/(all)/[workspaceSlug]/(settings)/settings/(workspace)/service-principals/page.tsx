/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
// plane imports
import { WarningTriangleOutline } from "@makeplane/propel/icons";
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
// components
import { EmptyStateCompact } from "@plane/propel/empty-state";
import { ServicePrincipalsList, CreateServicePrincipalModal } from "@/components/ai-accounts";
import { SERVICE_PRINCIPALS_LIST } from "@/components/ai-accounts/constants";
import { NotAuthorizedView } from "@/components/auth-screens/not-authorized-view";
import { PageHead } from "@/components/core/page-title";
import { SettingsHeading } from "@/components/settings/heading";
import { SettingsContentWrapper } from "@/components/settings/content-wrapper";
import { AIAccountSettingsLoader } from "@/components/ui/loader/settings/ai-account";
import { servicePrincipalService } from "@/services/ai-account.service";
// hooks
import { useWorkspace } from "@/hooks/store/use-workspace";
import { useUserPermissions } from "@/hooks/store/user";
// local imports
import type { Route } from "./+types/page";
import { ServicePrincipalsWorkspaceSettingsHeader } from "./header";

function ServicePrincipalsListPage({ params }: Route.ComponentProps) {
  // states
  const [showCreateModal, setShowCreateModal] = useState(false);
  // router
  const { workspaceSlug } = params;
  // plane hooks
  const { t } = useTranslation();
  // mobx store
  const { workspaceUserInfo, allowPermissions } = useUserPermissions();
  const { currentWorkspace } = useWorkspace();
  // derived values
  const canPerformWorkspaceAdminActions = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);

  const {
    data: principals,
    isLoading,
    error,
    mutate,
  } = useSWR(
    canPerformWorkspaceAdminActions ? SERVICE_PRINCIPALS_LIST(workspaceSlug) : null,
    canPerformWorkspaceAdminActions
      ? () => servicePrincipalService.fetchServicePrincipalsList(workspaceSlug)
      : null
  );

  const pageTitle = currentWorkspace?.name
    ? `${currentWorkspace.name} - ${t("workspace_settings.settings.service_principals.title")}`
    : undefined;

  if (workspaceUserInfo && !canPerformWorkspaceAdminActions) {
    return <NotAuthorizedView section="settings" className="h-auto" />;
  }

  return (
    <SettingsContentWrapper header={<ServicePrincipalsWorkspaceSettingsHeader />}>
      <PageHead title={pageTitle} />
      <div className="w-full">
        <CreateServicePrincipalModal
          isOpen={showCreateModal}
          onClose={() => setShowCreateModal(false)}
          workspaceSlug={workspaceSlug}
        />
        <SettingsHeading
          title={t("workspace_settings.settings.service_principals.title")}
          description={t("workspace_settings.settings.service_principals.description")}
          control={
            <Button variant="primary" size="lg" onClick={() => setShowCreateModal(true)}>
              {t("workspace_settings.settings.service_principals.add_principal")}
            </Button>
          }
        />
        {error ? (
          <div className="flex h-full w-full flex-col items-center justify-center gap-3 py-20 text-center">
            <WarningTriangleOutline className="size-8 text-tertiary" />
            <p className="text-14 text-secondary">{t("something_went_wrong")}</p>
            <Button variant="secondary" size="sm" onClick={() => mutate()}>
              {t("common.retry")}
            </Button>
          </div>
        ) : isLoading || !principals ? (
          <div className="mt-4">
            <AIAccountSettingsLoader />
          </div>
        ) : principals.length > 0 ? (
          <div className="mt-4">
            <ServicePrincipalsList principals={principals} workspaceSlug={workspaceSlug} />
          </div>
        ) : (
          <div className="flex h-full w-full flex-col">
            <div className="flex h-full w-full items-center justify-center">
              <EmptyStateCompact
                assetKey="token"
                title={t("settings_empty_state.service_principals.title")}
                description={t("settings_empty_state.service_principals.description")}
                actions={[
                  {
                    label: t("settings_empty_state.service_principals.cta_primary"),
                    onClick: () => {
                      setShowCreateModal(true);
                    },
                  },
                ]}
                align="start"
                rootClassName="py-20"
              />
            </div>
          </div>
        )}
      </div>
    </SettingsContentWrapper>
  );
}

export default observer(ServicePrincipalsListPage);