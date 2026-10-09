/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { mutate } from "swr";
// plane imports
import { Avatar } from "@makeplane/propel/components/avatar";
import { useTranslation } from "@plane/i18n";
import { EditIcon, TrashIcon } from "@plane/propel/icons";
import { Button } from "@plane/propel/button";
import { Switch } from "@makeplane/propel/components/switch";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { TServicePrincipal } from "@plane/types";
import { getFileURL, renderFormattedDate, calculateTimeAgo } from "@plane/utils";
// hooks
import { servicePrincipalService } from "@/services/ai-account.service";
// local imports
import { SERVICE_PRINCIPALS_LIST } from "./constants";
import { DeleteServicePrincipalModal } from "./delete-service-principal-modal";
import { EditServicePrincipalModal } from "./edit-service-principal-modal";
import { RotateServicePrincipalTokenModal } from "./rotate-token-modal";
import { ServicePrincipalGrantsModal } from "./grants-modal";
import { ServicePrincipalScopesModal } from "./scopes-modal";

type Props = {
  principal: TServicePrincipal;
  workspaceSlug: string;
};

export function ServicePrincipalsListItem(props: Props) {
  const { principal, workspaceSlug } = props;
  // states
  const [showEditModal, setShowEditModal] = useState(false);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [showScopesModal, setShowScopesModal] = useState(false);
  const [showGrantsModal, setShowGrantsModal] = useState(false);
  const [showRotateTokenModal, setShowRotateTokenModal] = useState(false);
  const [isToggling, setIsToggling] = useState(false);
  // hooks
  const { t, currentLocale } = useTranslation();

  const handleToggle = async () => {
    if (isToggling) return;
    setIsToggling(true);
    try {
      await servicePrincipalService.updateServicePrincipal(workspaceSlug, principal.id, {
        is_active: !principal.is_active,
      });
      mutate<TServicePrincipal[]>(SERVICE_PRINCIPALS_LIST(workspaceSlug));
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("workspace_settings.settings.service_principals.toasts.not_updated.title"),
        message: t("workspace_settings.settings.service_principals.toasts.not_updated.message"),
      });
    } finally {
      setIsToggling(false);
    }
  };

  return (
    <>
      <DeleteServicePrincipalModal
        principal={principal}
        isOpen={showDeleteModal}
        onClose={() => setShowDeleteModal(false)}
        workspaceSlug={workspaceSlug}
      />
      <EditServicePrincipalModal
        principal={principal}
        isOpen={showEditModal}
        onClose={() => setShowEditModal(false)}
        workspaceSlug={workspaceSlug}
      />
      <ServicePrincipalScopesModal
        principal={principal}
        isOpen={showScopesModal}
        onClose={() => setShowScopesModal(false)}
        workspaceSlug={workspaceSlug}
      />
      <ServicePrincipalGrantsModal
        principal={principal}
        isOpen={showGrantsModal}
        onClose={() => setShowGrantsModal(false)}
        workspaceSlug={workspaceSlug}
      />
      <RotateServicePrincipalTokenModal
        principal={principal}
        isOpen={showRotateTokenModal}
        onClose={() => setShowRotateTokenModal(false)}
        workspaceSlug={workspaceSlug}
      />
      <div className="flex items-center justify-between gap-4 rounded-lg border border-subtle bg-layer-2 px-4 py-3">
        <div className="flex min-w-0 items-center gap-3">
          <Avatar
            src={getFileURL(principal.avatar)}
            alt={principal.name}
            fallback={principal.name.charAt(0)}
            size="md"
            tooltip
          />
          <div className="min-w-0">
            <h5 className="truncate text-body-sm-medium">{principal.name}</h5>
            {principal.description && <p className="truncate text-11 text-placeholder">{principal.description}</p>}
            <p className="text-11 text-placeholder">
              {t("workspace_settings.settings.service_principals.list.created_on")}{" "}
              {renderFormattedDate(principal.created_at)}
            </p>
            <p className="text-11 text-placeholder">
              {principal.token_last_used
                ? t("token_last_used", { time: calculateTimeAgo(principal.token_last_used, currentLocale) })
                : t("token_never_used")}
            </p>
          </div>
        </div>
        <div className="flex flex-shrink-0 items-center gap-3">
          <Button variant="secondary" size="sm" onClick={() => setShowScopesModal(true)}>
            {t("workspace_settings.settings.service_principals.list.manage_scopes")}
          </Button>
          <Button variant="secondary" size="sm" onClick={() => setShowGrantsModal(true)}>
            {t("workspace_settings.settings.service_principals.list.manage_grants")}
          </Button>
          <Button variant="secondary" size="sm" onClick={() => setShowEditModal(true)}>
            <EditIcon className="size-3" />
            {t("workspace_settings.settings.service_principals.list.edit")}
          </Button>
          {principal.is_active && (
            <Button variant="secondary" size="sm" onClick={() => setShowRotateTokenModal(true)}>
              {t("workspace_settings.settings.service_principals.list.rotate_token")}
            </Button>
          )}
          <Button variant="error-outline" size="sm" onClick={() => setShowDeleteModal(true)}>
            <TrashIcon className="size-3" />
            {t("workspace_settings.settings.service_principals.list.delete")}
          </Button>
          <Switch
            size="sm"
            checked={principal.is_active}
            onCheckedChange={() => {
              void handleToggle();
            }}
            aria-label={principal.name}
          />
        </div>
      </div>
    </>
  );
}
