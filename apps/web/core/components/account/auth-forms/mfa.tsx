/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useRef, useState } from "react";
import { observer } from "mobx-react";
import { useSearchParams } from "next/navigation";
// plane imports
import { API_BASE_URL } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Input, InputGroup } from "@makeplane/propel/components/input";
import { Button } from "@plane/propel/button";
import { Spinner } from "@plane/ui";
// helpers
import { EAuthenticationErrorCodes } from "@/helpers/authentication.helper";
// services
import { AuthService } from "@/services/auth.service";
// local components
import { AuthBanner } from "./auth-banner";
import { FormContainer } from "./common/container";
import { AuthFormHeader } from "./common/header";

// Error codes that keep the user on the MFA step (vs MFA_SESSION_EXPIRED,
// which redirects back to sign-in)
const MFA_STEP_ERROR_MESSAGES: Record<string, string> = {
  [EAuthenticationErrorCodes.MFA_CODE_REQUIRED]: "auth.mfa.errors.code_required",
  [EAuthenticationErrorCodes.MFA_INVALID_CODE]: "auth.mfa.errors.invalid_code",
  [EAuthenticationErrorCodes.MFA_RECOVERY_CODE_REQUIRED]: "auth.mfa.errors.recovery_code_required",
  [EAuthenticationErrorCodes.MFA_INVALID_RECOVERY_CODE]: "auth.mfa.errors.invalid_recovery_code",
};

const authService = new AuthService();

export const MFAForm = observer(function MFAForm() {
  // search params
  const searchParams = useSearchParams();
  // plane hooks
  const { t } = useTranslation();
  // refs
  const formRef = useRef<HTMLFormElement>(null);
  // states
  const [csrfPromise, setCsrfPromise] = useState<Promise<{ csrf_token: string }> | undefined>(undefined);
  const [code, setCode] = useState("");
  const [isRecoveryMode, setIsRecoveryMode] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [bannerMessage, setBannerMessage] = useState<string | undefined>(undefined);

  useEffect(() => {
    if (csrfPromise === undefined) {
      const promise = authService.requestCSRFToken();
      setCsrfPromise(promise);
    }
  }, [csrfPromise]);

  // Surface the error_code the auth backend redirected back with
  useEffect(() => {
    const error_code = searchParams.get("error_code");
    if (error_code) {
      const messageKey = MFA_STEP_ERROR_MESSAGES[error_code];
      setBannerMessage(messageKey ? t(messageKey) : t("auth.mfa.errors.invalid_code"));
    }
  }, [searchParams, t]);

  const handleCSRFToken = async () => {
    if (!formRef || !formRef.current) return;
    const token = await csrfPromise;
    if (!token?.csrf_token) return;
    const csrfElement = formRef.current.querySelector("input[name=csrfmiddlewaretoken]");
    csrfElement?.setAttribute("value", token?.csrf_token);
  };

  const isButtonDisabled = isSubmitting || code.trim().length === 0;

  return (
    <FormContainer>
      <AuthFormHeader
        title={t("auth.mfa.step.title")}
        description={isRecoveryMode ? t("auth.mfa.step.description_recovery") : t("auth.mfa.step.description")}
      />
      {bannerMessage && <AuthBanner message={bannerMessage} handleBannerData={() => setBannerMessage(undefined)} />}
      <form
        ref={formRef}
        className="space-y-4"
        method="POST"
        action={`${API_BASE_URL}/auth/mfa/${isRecoveryMode ? "recovery" : "totp"}/`}
        onSubmit={async (event) => {
          event.preventDefault(); // Prevent form from submitting by default
          await handleCSRFToken();
          setIsSubmitting(true);
          if (formRef.current) formRef.current.submit(); // Manually submit the form
        }}
      >
        <input type="hidden" name="csrfmiddlewaretoken" />
        <div className="space-y-1">
          <label htmlFor="code" className="text-13 font-medium text-tertiary">
            {isRecoveryMode ? t("auth.mfa.step.recovery_code.label") : t("auth.mfa.step.code.label")}
          </label>
          <InputGroup size="2xl">
            <Input
              size="2xl"
              id="code"
              name="code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder={
                isRecoveryMode ? t("auth.mfa.step.recovery_code.placeholder") : t("auth.mfa.step.code.placeholder")
              }
              autoComplete="one-time-code"
              autoFocus
            />
          </InputGroup>
        </div>
        <div className="space-y-2.5">
          <Button type="submit" variant="primary" className="w-full" size="xl" disabled={isButtonDisabled}>
            {isSubmitting ? <Spinner height="20px" width="20px" /> : t("auth.mfa.step.submit")}
          </Button>
          <Button
            type="button"
            variant="secondary"
            className="w-full"
            size="xl"
            onClick={() => {
              setIsRecoveryMode((prev) => !prev);
              setCode("");
              setBannerMessage(undefined);
            }}
          >
            {isRecoveryMode ? t("auth.mfa.step.use_authenticator") : t("auth.mfa.step.use_recovery_code")}
          </Button>
        </div>
      </form>
    </FormContainer>
  );
});
