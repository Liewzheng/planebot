/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useRef, useState } from "react";
import { mutate } from "swr";
// plane imports
import { useTranslation } from "@plane/i18n";
import type { TServicePrincipal } from "@plane/types";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { EModalPosition, EModalWidth, ModalCore } from "@plane/ui";
// hooks
import { useUser } from "@/hooks/store/user";
// services
import { servicePrincipalService } from "@/services/ai-account.service";
// local imports
import { ServicePrincipalForm, type TServicePrincipalFormValues } from "./service-principal-form";
import { SERVICE_PRINCIPALS_LIST, getMfaStepUpError } from "./constants";
import { GeneratedTokenDetails } from "./generated-token-details";

type TCreatedServicePrincipal = TServicePrincipal & { token: string };

type Props = {
  isOpen: boolean;
  onClose: () => void;
  workspaceSlug: string;
};

export function CreateServicePrincipalModal(props: Props) {
  const { isOpen, onClose, workspaceSlug } = props;
  // states
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [createdPrincipal, setCreatedPrincipal] = useState<TCreatedServicePrincipal | null>(null);
  const [totpCode, setTotpCode] = useState("");
  const [totpError, setTotpError] = useState<string | undefined>(undefined);
  // hooks
  const { t } = useTranslation();
  const { data: currentUser } = useUser();
  // Step-up verification applies only to users who opted into 2FA
  const isMFAEnabled = currentUser?.is_mfa_enabled ?? false;
  // refs
  // Bumped on close so a create response that lands after the modal was
  // closed cannot resurrect the stale token screen on the next open
  const requestGenerationRef = useRef(0);
  const resetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Reopening before the delayed reset fires must run the reset immediately
  // instead of just cancelling the timer — otherwise the stale token screen
  // or a stuck submitting state would survive into the reopened modal
  useEffect(() => {
    if (isOpen && resetTimerRef.current) {
      clearTimeout(resetTimerRef.current);
      resetTimerRef.current = null;
      setIsSubmitting(false);
      setCreatedPrincipal(null);
      setTotpCode("");
      setTotpError(undefined);
    }
  }, [isOpen]);

  useEffect(
    () => () => {
      if (resetTimerRef.current) clearTimeout(resetTimerRef.current);
    },
    []
  );

  const handleClose = () => {
    onClose();
    requestGenerationRef.current += 1;
    resetTimerRef.current = setTimeout(() => {
      setIsSubmitting(false);
      setCreatedPrincipal(null);
      setTotpCode("");
      setTotpError(undefined);
      resetTimerRef.current = null;
    }, 350);
  };

  const handleCreate = async (data: TServicePrincipalFormValues) => {
    if (isMFAEnabled && !totpCode.trim()) {
      setTotpError(t("workspace_settings.settings.service_principals.step_up.code_required"));
      return;
    }
    const generation = ++requestGenerationRef.current;
    setIsSubmitting(true);
    setTotpError(undefined);
    try {
      const res = await servicePrincipalService.createServicePrincipal(workspaceSlug, {
        name: data.name,
        description: data.description,
        ...(isMFAEnabled && { totp_code: totpCode.trim() }),
      });
      // The principal is created either way, but only show the token screen
      // when the modal is still on this same request
      if (generation === requestGenerationRef.current) {
        setCreatedPrincipal(res);
      }
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("workspace_settings.settings.service_principals.toasts.created.title"),
        message: t("workspace_settings.settings.service_principals.toasts.created.message"),
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
          title: t("workspace_settings.settings.service_principals.toasts.not_created.title"),
          message:
            (err as { message?: string })?.message ??
            t("workspace_settings.settings.service_principals.toasts.not_created.message"),
        });
      }
    } finally {
      // A stale request must not unlock the submitting state of a newer one
      if (generation === requestGenerationRef.current) {
        setIsSubmitting(false);
      }
    }
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={() => {}} position={EModalPosition.TOP} width={EModalWidth.XXL}>
      {createdPrincipal ? (
        <GeneratedTokenDetails principal={createdPrincipal} handleClose={handleClose} />
      ) : (
        <ServicePrincipalForm
          defaultValues={{ name: "", description: "" }}
          handleClose={handleClose}
          isSubmitting={isSubmitting}
          loadingLabel={t("workspace_settings.settings.service_principals.modal.creating")}
          submitLabel={t("workspace_settings.settings.service_principals.modal.create")}
          title={t("workspace_settings.settings.service_principals.modal.create_title")}
          onSubmit={handleCreate}
          totp={isMFAEnabled ? { value: totpCode, onChange: setTotpCode, error: totpError } : undefined}
        />
      )}
    </ModalCore>
  );
}
