/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { ReactNode } from "react";
import { useTranslation } from "@plane/i18n";
import { cn } from "@plane/utils";

type Props = {
  label: string;
  htmlFor?: string;
  /** The server said this field is empty or wrong — the sentence is shown. */
  errors?: string[];
  /** The field is one of the mandatory items of the case header. */
  required?: boolean;
  /** The hint under the control — shown when the field is still empty. */
  hint?: string;
  children: ReactNode;
  className?: string;
};

/**
 * One field of the case header: the label, the control and the reasons the
 * server refused the record. The reasons are the Chinese sentences of the
 * error envelope — the front end never invents its own wording for them.
 */
export function TestCaseFormField(props: Props) {
  const { label, htmlFor, errors, required, hint, children, className } = props;
  const { t } = useTranslation();

  return (
    <div className={cn("flex min-w-0 flex-col gap-1.5", className)}>
      <div className="flex items-center gap-2">
        <label htmlFor={htmlFor} className="text-11 font-medium text-secondary">
          {label}
        </label>
        {required && (
          <span className="rounded-sm bg-layer-3 px-1 py-0.5 text-10 text-tertiary">
            {t("project_test_case.editor.required")}
          </span>
        )}
      </div>

      {children}

      {hint && <p className="text-11 text-tertiary">{hint}</p>}

      {errors && errors.length > 0 && (
        <ul className="flex flex-col gap-0.5">
          {errors.map((error) => (
            <li key={error} className="text-11 text-danger-primary">
              {error}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
