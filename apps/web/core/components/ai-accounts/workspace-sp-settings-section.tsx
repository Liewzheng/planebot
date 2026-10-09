/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import useSWR from "swr";
import { observer } from "mobx-react";
// plane imports
import { Switch } from "@makeplane/propel/components/switch";
import { useTranslation } from "@plane/i18n";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { TWorkspaceSPSettings } from "@plane/types";
// hooks
import { useUserPermissions } from "@/hooks/store/user";
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
// services
import { servicePrincipalService } from "@/services/ai-account.service";

type Props = {
  workspaceSlug: string;
};

const SP_SETTINGS_SW_KEY = (slug: string) => `WORKSPACE_SP_SETTINGS_${slug}`;

const isHttpNotFound = (err: unknown): boolean => {
  const status = (err as { status_code?: number })?.status_code;
  // The backend may not be wired yet; treat 404 as "no settings row
  // yet" (the model defaults sp_assignable to false).
  return status === 404 || status === 405;
};

export const WorkspaceSPSettingsSection = observer(function WorkspaceSPSettingsSection(props: Props) {
  const { workspaceSlug } = props;
  // hooks
  const { t } = useTranslation();
  const { allowPermissions } = useUserPermissions();
  const isAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);
  // SWR backed by the future /api/workspaces/<slug>/sp-settings/ endpoint.
  // 404 today means "no settings row yet" — the model defaults
  // sp_assignable to false so the UI lands on the same default.
  const { data, error, mutate } = useSWR<TWorkspaceSPSettings>(
    isAdmin ? SP_SETTINGS_SW_KEY(workspaceSlug) : null,
    () => servicePrincipalService.fetchSPSettings(workspaceSlug)
  );
  const backendWired = !error || !isHttpNotFound(error);
  const currentValue = data?.sp_assignable ?? false;

  // Optimistic toggle with rollback: flipping sends the new value into
  // SWR's cache, then patches the backend; a failure rolls back.
  const handleToggle = async (nextValue: boolean) => {
    const previous = currentValue;
    try {
      mutate({ workspace: workspaceSlug, sp_assignable: nextValue }, false);
      await servicePrincipalService.updateSPSettings(workspaceSlug, { sp_assignable: nextValue });
      mutate({ workspace: workspaceSlug, sp_assignable: nextValue });
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("common.toast.success"),
        message: t("workspace_settings.settings.service_principals.assignable.toast_updated"),
      });
    } catch (err) {
      mutate({ workspace: workspaceSlug, sp_assignable: previous }, false);
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("common.toast.error"),
        message: t("workspace_settings.settings.service_principals.assignable.toast_update_failed"),
      });
    }
  };

  // Hide the control from non-admins (matches other workspace-settings
  // sections).
  if (!isAdmin) return null;

  return (
    <div className="mt-10 flex items-start justify-between gap-4 rounded-md border border-subtle bg-layer-2 p-4">
      <div className="space-y-1">
        <h4 className="text-body-sm-medium text-secondary">
          {t("workspace_settings.settings.service_principals.assignable.label")}
        </h4>
        <p className="text-11 text-tertiary">
          {t("workspace_settings.settings.service_principals.assignable.description")}
        </p>
        {!backendWired && (
          <p className="text-11 text-placeholder">
            {t("workspace_settings.settings.service_principals.assignable.not_persisted")}
          </p>
        )}
      </div>
      <Switch
        size="sm"
        checked={currentValue}
        onCheckedChange={(next: boolean) => {
          void handleToggle(next);
        }}
        aria-label={t("workspace_settings.settings.service_principals.assignable.label")}
      />
    </div>
  );
});