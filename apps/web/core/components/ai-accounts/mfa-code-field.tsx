/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import { Field } from "@makeplane/propel/components/field";
import { Input, InputGroup } from "@makeplane/propel/components/input";
import { useTranslation } from "@plane/i18n";

type Props = {
  value: string;
  onChange: (value: string) => void;
  error?: string;
};

/**
 * 6-digit TOTP input for step-up verification on sensitive operations.
 * Only rendered when the acting user has 2FA enabled.
 */
export function MfaCodeField(props: Props) {
  const { value, onChange, error } = props;
  // hooks
  const { t } = useTranslation();

  return (
    <div className="space-y-1">
      <h4 className="text-13">{t("auth.mfa.settings.setup.verify_label")}</h4>
      <Field name="totp_code" invalid={Boolean(error)}>
        <InputGroup size="2xl">
          <Input
            size="2xl"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={t("auth.mfa.settings.setup.verify_placeholder")}
            autoComplete="one-time-code"
            inputMode="numeric"
          />
        </InputGroup>
      </Field>
      {error && <span className="text-11 text-danger-primary">{error}</span>}
    </div>
  );
}
