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
import { AlertModalCore, EModalPosition, EModalWidth, ModalCore } from "@plane/ui";
// hooks
import { useUser } from "@/hooks/store/user";
// services
import { servicePrincipalService } from "@/services/ai-account.service";
// local imports
import { SERVICE_PRINCIPALS_LIST, getMfaStepUpError } from "./constants";
import { GeneratedTokenDetails } from "./generated-token-details";
import { MfaCodeField } from "./mfa-code-field";

type TRotatedServicePrincipal = TServicePrincipal & { token: string };

type Props = {
  principal: TServicePrincipal;
  isOpen: boolean;
  onClose: () => void;
  workspaceSlug: string;
};

export function RotateServicePrincipalTokenModal(props: Props) {
  const { principal, isOpen, onClose, workspaceSlug } = props;
  // states
  const [isRotating, setIsRotating] = useState(false);
  const [rotatedPrincipal, setRotatedPrincipal] = useState<TRotatedServicePrincipal | null>(null);
  const [totpCode, setTotpCode] = useState("");
  const [totpError, setTotpError] = useState<string | undefined>(undefined);
  // hooks
  const { t } = useTranslation();
  const { data: currentUser } = useUser();
  // Step-up verification applies only to users who opted into 2FA
  const isMFAEnabled = currentUser?.is_mfa_enabled ?? false;

  const handleClose = () => {
    onClose();
    setTimeout(() => {
      setIsRotating(false);
      setRotatedPrincipal(null);
      setTotpCode("");
      setTotpError(undefined);
    }, 350);
  };

  const handleRotate = async () => {
    if (isMFAEnabled && !totpCode.trim()) {
      setTotpError(t("workspace_settings.settings.service_principals.step_up.code_required"));
      return;
    }
    setIsRotating(true);
    setTotpError(undefined);
    try {
      const res = await servicePrincipalService.rotateServicePrincipalToken(
        workspaceSlug,
        principal.id,
        isMFAEnabled ? totpCode.trim() : undefined
      );
      setRotatedPrincipal(res);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("workspace_settings.settings.service_principals.rotate.success.title"),
        message: t("workspace_settings.settings.service_principals.rotate.success.message"),
      });
      mutate<TServicePrincipal[]>(SERVICE_PRINCIPALS_LIST(workspaceSlug));
    } catch (err) {
      const mfaError = getMfaStepUpError(err, t);
      if (mfaError) {
        // Step-up failure stays inline next to the code input
        setTotpError(mfaError);
      } else {
        setToast({
          type: TOAST_TYPE.ERROR,
          title: t("workspace_settings.settings.service_principals.rotate.error.title"),
          message:
            (err as { message?: string })?.message ??
            t("workspace_settings.settings.service_principals.rotate.error.message"),
        });
      }
      setIsRotating(false);
    }
  };

  // After a successful rotation the new token is shown exactly once
  if (rotatedPrincipal) {
    return (
      <ModalCore isOpen={isOpen} handleClose={() => {}} position={EModalPosition.TOP} width={EModalWidth.XXL}>
        <GeneratedTokenDetails
          principal={rotatedPrincipal}
          handleClose={handleClose}
          title={t("workspace_settings.settings.service_principals.token.rotated_title")}
        />
      </ModalCore>
    );
  }

  return (
    <AlertModalCore
      handleClose={handleClose}
      handleSubmit={handleRotate}
      isSubmitting={isRotating}
      isOpen={isOpen}
      primaryButtonText={{
        loading: t("workspace_settings.settings.service_principals.rotate.rotating"),
        default: t("workspace_settings.settings.service_principals.rotate.confirm"),
      }}
      title={t("workspace_settings.settings.service_principals.rotate.title")}
      content={
        <div className="space-y-3">
          <p>{t("workspace_settings.settings.service_principals.rotate.description")}</p>
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