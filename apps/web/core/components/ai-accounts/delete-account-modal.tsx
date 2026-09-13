/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { mutate } from "swr";
// plane imports
import { useTranslation } from "@plane/i18n";
import type { TAIAccount } from "@plane/types";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
// ui
import { AlertModalCore } from "@plane/ui";
// hooks
import { useUser } from "@/hooks/store/user";
// services
import { aiAccountService } from "@/services/ai-account.service";
// local imports
import { AI_ACCOUNTS_LIST, getMfaStepUpError } from "./constants";
import { MfaCodeField } from "./mfa-code-field";

type Props = {
  account: TAIAccount;
  isOpen: boolean;
  onClose: () => void;
  workspaceSlug: string;
};

export function DeleteAIAccountModal(props: Props) {
  const { account, isOpen, onClose, workspaceSlug } = props;
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
      setTotpError(t("workspace_settings.settings.ai_accounts.step_up.code_required"));
      return;
    }
    setIsDeleting(true);
    setTotpError(undefined);
    try {
      await aiAccountService.deleteAIAccount(workspaceSlug, account.id, isMFAEnabled ? totpCode.trim() : undefined);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("workspace_settings.settings.ai_accounts.delete.success.title"),
        message: t("workspace_settings.settings.ai_accounts.delete.success.message"),
      });
      mutate<TAIAccount[]>(AI_ACCOUNTS_LIST(workspaceSlug));
      handleClose();
    } catch (err) {
      const mfaError = getMfaStepUpError(err, t);
      if (mfaError) {
        // Step-up failure stays inline next to the code input
        setTotpError(mfaError);
      } else {
        setToast({
          type: TOAST_TYPE.ERROR,
          title: t("workspace_settings.settings.ai_accounts.delete.error.title"),
          message:
            (err as { message?: string })?.message ?? t("workspace_settings.settings.ai_accounts.delete.error.message"),
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
      title={t("workspace_settings.settings.ai_accounts.delete.title")}
      content={
        <div className="space-y-3">
          <p>{t("workspace_settings.settings.ai_accounts.delete.description")}</p>
          {isMFAEnabled && (
            <>
              <p className="text-11 text-tertiary">{t("workspace_settings.settings.ai_accounts.step_up.hint")}</p>
              <MfaCodeField value={totpCode} onChange={setTotpCode} error={totpError} />
            </>
          )}
        </div>
      }
    />
  );
}
