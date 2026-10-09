/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { mutate } from "swr";
// plane imports
import { useTranslation } from "@plane/i18n";
import type { TServicePrincipal } from "@plane/types";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
// ui
import { AlertModalCore } from "@plane/ui";
// hooks
import { useUser } from "@/hooks/store/user";
// services
import { servicePrincipalService } from "@/services/ai-account.service";
// local imports
import { SERVICE_PRINCIPALS_LIST, getMfaStepUpError } from "./constants";
import { MfaCodeField } from "./mfa-code-field";

type Props = {
  principal: TServicePrincipal;
  isOpen: boolean;
  onClose: () => void;
  workspaceSlug: string;
};

export function DeleteServicePrincipalModal(props: Props) {
  const { principal, isOpen, onClose, workspaceSlug } = props;
  // states
  const [isDeleting, setIsDeleting] = useState(false);
  const [totpCode, setTotpCode] = useState("");
  const [totpError, setTotpError] = useState<string | undefined>(undefined);
  // hooks
  const { t } = useTranslation();
  const { data: currentUser } = useUser();
  // Step-up verification applies only to users who opted into 2FA
  const isMFAEnabled = currentUser?.is_mfa_enabled ?? false;

  const handleClose = () => {
    onClose();
    setIsDeleting(false);
    setTotpCode("");
    setTotpError(undefined);
  };

  const handleDeletion = async () => {
    if (isMFAEnabled && !totpCode.trim()) {
      setTotpError(t("workspace_settings.settings.service_principals.step_up.code_required"));
      return;
    }
    setIsDeleting(true);
    setTotpError(undefined);
    try {
      await servicePrincipalService.deleteServicePrincipal(
        workspaceSlug,
        principal.id,
        isMFAEnabled ? totpCode.trim() : undefined
      );
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("workspace_settings.settings.service_principals.delete.success.title"),
        message: t("workspace_settings.settings.service_principals.delete.success.message"),
      });
      mutate<TServicePrincipal[]>(SERVICE_PRINCIPALS_LIST(workspaceSlug));
      handleClose();
    } catch (err) {
      const mfaError = getMfaStepUpError(err, t);
      if (mfaError) {
        // Step-up failure stays inline next to the code input
        setTotpError(mfaError);
      } else {
        setToast({
          type: TOAST_TYPE.ERROR,
          title: t("workspace_settings.settings.service_principals.delete.error.title"),
          message:
            (err as { message?: string })?.message ??
            t("workspace_settings.settings.service_principals.delete.error.message"),
        });
      }
      setIsDeleting(false);
    }
  };

  return (
    <AlertModalCore
      handleClose={handleClose}
      handleSubmit={handleDeletion}
      isSubmitting={isDeleting}
      isOpen={isOpen}
      title={t("workspace_settings.settings.service_principals.delete.title")}
      content={
        <div className="space-y-3">
          <p>{t("workspace_settings.settings.service_principals.delete.description")}</p>
          {isMFAEnabled && (
            <>
              <p className="text-11 text-tertiary">
                {t("workspace_settings.settings.service_principals.step_up.hint")}
              </p>
              <MfaCodeField value={totpCode} onChange={setTotpCode} error={totpError} />
            </>
          )}
        </div>
      }
    />
  );
}