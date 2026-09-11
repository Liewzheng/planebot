/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
// icons
import { CopyOutline, DownloadOutline } from "@makeplane/propel/icons";
// plane imports
import { Input, InputGroup } from "@makeplane/propel/components/input";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { TOTPSetupResponse } from "@plane/types";
// components
import { ProfileSettingsHeading } from "@/components/settings/profile/heading";
// hooks
import { useUser } from "@/hooks/store/user";
// services
import { AuthService } from "@/services/auth.service";
import { UserService } from "@/services/user.service";

const authService = new AuthService();
const userService = new UserService();

type TConfirmAction = "disable" | "regenerate";

export const TwoFactorAuthSettings = observer(function TwoFactorAuthSettings() {
  // store
  const { data: currentUser, fetchCurrentUser } = useUser();
  // plane hooks
  const { t } = useTranslation();
  // states
  const [setupData, setSetupData] = useState<TOTPSetupResponse | undefined>(undefined);
  const [enableCode, setEnableCode] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | undefined>(undefined);
  const [confirmAction, setConfirmAction] = useState<TConfirmAction | undefined>(undefined);
  const [actionPassword, setActionPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const isMFAEnabled = currentUser?.is_mfa_enabled ?? false;

  const getCSRFToken = async () => {
    const csrfToken = await authService.requestCSRFToken().then((data) => data?.csrf_token);
    if (!csrfToken) throw new Error("csrf token not found");
    return csrfToken;
  };

  const handleError = (error: unknown, fallbackKey: string) => {
    const err = error as { error?: string; error_message?: string };
    setToast({
      type: TOAST_TYPE.ERROR,
      title: t("auth.mfa.settings.toasts.error.title"),
      message: err?.error ?? err?.error_message ?? t(fallbackKey),
    });
  };

  const handleSetup = async () => {
    try {
      const csrfToken = await getCSRFToken();
      const data = await userService.mfaSetup(csrfToken);
      setSetupData(data);
      setEnableCode("");
    } catch (error: unknown) {
      handleError(error, "auth.mfa.settings.toasts.setup_error.message");
    }
  };

  const handleEnable = async () => {
    if (!enableCode.trim()) return;
    setIsSubmitting(true);
    try {
      const csrfToken = await getCSRFToken();
      const data = await userService.mfaEnable(csrfToken, { code: enableCode.trim() });
      setRecoveryCodes(data.recovery_codes);
      setSetupData(undefined);
      setEnableCode("");
      await fetchCurrentUser();
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("auth.mfa.settings.toasts.enabled.title"),
        message: t("auth.mfa.settings.toasts.enabled.message"),
      });
    } catch (error: unknown) {
      handleError(error, "auth.mfa.settings.toasts.enable_error.message");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleConfirmAction = async () => {
    if (!confirmAction || !actionPassword) return;
    setIsSubmitting(true);
    try {
      const csrfToken = await getCSRFToken();
      if (confirmAction === "disable") {
        await userService.mfaDisable(csrfToken, { password: actionPassword });
        setRecoveryCodes(undefined);
        await fetchCurrentUser();
        setToast({
          type: TOAST_TYPE.SUCCESS,
          title: t("auth.mfa.settings.toasts.disabled.title"),
          message: t("auth.mfa.settings.toasts.disabled.message"),
        });
      } else {
        const data = await userService.mfaRegenerateRecoveryCodes(csrfToken, { password: actionPassword });
        setRecoveryCodes(data.recovery_codes);
        setToast({
          type: TOAST_TYPE.SUCCESS,
          title: t("auth.mfa.settings.toasts.codes_regenerated.title"),
          message: t("auth.mfa.settings.toasts.codes_regenerated.message"),
        });
      }
      setConfirmAction(undefined);
      setActionPassword("");
    } catch (error: unknown) {
      handleError(error, "auth.mfa.settings.toasts.error.message");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleCopyCodes = async () => {
    if (!recoveryCodes) return;
    try {
      await navigator.clipboard.writeText(recoveryCodes.join("\n"));
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("auth.mfa.settings.recovery_codes.copied.title"),
        message: t("auth.mfa.settings.recovery_codes.copied.message"),
      });
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("auth.mfa.settings.toasts.error.title"),
        message: t("auth.mfa.settings.recovery_codes.copy_failed"),
      });
    }
  };

  const handleDownloadCodes = () => {
    if (!recoveryCodes) return;
    const blob = new Blob([recoveryCodes.join("\n")], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "plane-recovery-codes.txt";
    anchor.click();
    URL.revokeObjectURL(url);
  };

  // Step 3: show the freshly generated recovery codes (only time they are visible)
  if (recoveryCodes) {
    return (
      <div className="flex flex-col gap-y-6">
        <ProfileSettingsHeading
          title={t("auth.mfa.settings.recovery_codes.title")}
          description={t("auth.mfa.settings.recovery_codes.description")}
        />
        <div className="grid max-w-md grid-cols-2 gap-2">
          {recoveryCodes.map((code) => (
            <code key={code} className="font-mono rounded-md border border-subtle bg-surface-2 px-2 py-1 text-13">
              {code}
            </code>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="lg" onClick={handleCopyCodes}>
            <CopyOutline className="size-4" />
            {t("auth.mfa.settings.recovery_codes.copy")}
          </Button>
          <Button variant="secondary" size="lg" onClick={handleDownloadCodes}>
            <DownloadOutline className="size-4" />
            {t("auth.mfa.settings.recovery_codes.download")}
          </Button>
          <Button
            variant="primary"
            size="lg"
            onClick={() => {
              setRecoveryCodes(undefined);
              setConfirmAction(undefined);
            }}
          >
            {t("auth.mfa.settings.recovery_codes.done")}
          </Button>
        </div>
      </div>
    );
  }

  // Step 2: confirm the authenticator code to finish the setup
  if (setupData) {
    return (
      <div className="flex flex-col gap-y-6">
        <ProfileSettingsHeading
          title={t("auth.mfa.settings.setup.title")}
          description={t("auth.mfa.settings.setup.description")}
        />
        <div className="flex max-w-md flex-col gap-y-2">
          <p className="text-13 text-tertiary">{t("auth.mfa.settings.setup.manual_entry")}</p>
          <div className="flex items-center justify-between gap-2 rounded-md border border-subtle bg-surface-2 px-3 py-2">
            <code className="font-mono text-13 break-all">{setupData.secret}</code>
            <Button variant="secondary" size="sm" onClick={() => navigator.clipboard.writeText(setupData.secret)}>
              <CopyOutline className="size-4" />
              {t("auth.mfa.settings.setup.copy_secret")}
            </Button>
          </div>
          <p className="text-13 text-tertiary">{t("auth.mfa.settings.setup.uri_hint")}</p>
          <code className="font-mono rounded-md border border-subtle bg-surface-2 px-3 py-2 text-11 break-all">
            {setupData.otpauth_uri}
          </code>
        </div>
        <div className="flex max-w-md flex-col gap-y-2">
          <h4 className="text-13">{t("auth.mfa.settings.setup.verify_label")}</h4>
          <InputGroup size="2xl">
            <Input
              size="2xl"
              value={enableCode}
              onChange={(e) => setEnableCode(e.target.value)}
              placeholder={t("auth.mfa.settings.setup.verify_placeholder")}
              autoComplete="one-time-code"
            />
          </InputGroup>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="primary"
            size="lg"
            loading={isSubmitting}
            disabled={!enableCode.trim()}
            onClick={handleEnable}
          >
            {t("auth.mfa.settings.setup.verify_submit")}
          </Button>
          <Button variant="secondary" size="lg" onClick={() => setSetupData(undefined)}>
            {t("cancel")}
          </Button>
        </div>
      </div>
    );
  }

  // Step 1: overview + password-confirmed actions
  return (
    <div className="flex flex-col gap-y-6">
      <ProfileSettingsHeading
        title={t("auth.mfa.settings.title")}
        description={
          isMFAEnabled ? t("auth.mfa.settings.description_enabled") : t("auth.mfa.settings.description_disabled")
        }
      />
      {confirmAction && (
        <div className="flex max-w-md flex-col gap-y-2">
          <h4 className="text-13">{t("auth.mfa.settings.password_label")}</h4>
          <InputGroup size="2xl">
            <Input
              size="2xl"
              type="password"
              value={actionPassword}
              onChange={(e) => setActionPassword(e.target.value)}
              placeholder={t("auth.mfa.settings.password_placeholder")}
              autoComplete="current-password"
              autoFocus
            />
          </InputGroup>
          <div className="flex items-center gap-2 pt-2">
            <Button
              variant={confirmAction === "disable" ? "error-outline" : "primary"}
              size="lg"
              loading={isSubmitting}
              disabled={!actionPassword}
              onClick={handleConfirmAction}
            >
              {confirmAction === "disable"
                ? t("auth.mfa.settings.confirm_disable")
                : t("auth.mfa.settings.confirm_regenerate")}
            </Button>
            <Button
              variant="secondary"
              size="lg"
              onClick={() => {
                setConfirmAction(undefined);
                setActionPassword("");
              }}
            >
              {t("cancel")}
            </Button>
          </div>
        </div>
      )}
      {!confirmAction && (
        <div className="flex items-center gap-2">
          {!isMFAEnabled && (
            <Button variant="primary" size="lg" onClick={handleSetup}>
              {t("auth.mfa.settings.enable")}
            </Button>
          )}
          {isMFAEnabled && (
            <>
              <Button variant="secondary" size="lg" onClick={() => setConfirmAction("regenerate")}>
                {t("auth.mfa.settings.regenerate_codes")}
              </Button>
              <Button variant="error-outline" size="lg" onClick={() => setConfirmAction("disable")}>
                {t("auth.mfa.settings.disable")}
              </Button>
            </>
          )}
        </div>
      )}
    </div>
  );
});
