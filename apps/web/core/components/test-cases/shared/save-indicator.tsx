/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useTranslation } from "@plane/i18n";
import { CheckDoneOutline, InfoOutline, WarningTriangleOutline } from "@makeplane/propel/icons";
import { cn } from "@plane/utils";

/**
 * The state of the automatic save. The mission asks for three states at the
 * top of the editor — *saving*, *saved* and *conflict* — and the two extra
 * ones the server can cause: a refused write (the reasons are shown next to
 * the fields) and the idle state (nothing to save).
 */
export type TTestCaseSaveState = "idle" | "saving" | "saved" | "conflict" | "error";

type Props = {
  state: TTestCaseSaveState;
  className?: string;
};

export function TestCaseSaveIndicator(props: Props) {
  const { state, className } = props;
  const { t } = useTranslation();

  const content = {
    idle: { icon: CheckDoneOutline, text: t("project_test_case.save.idle"), tone: "text-tertiary" },
    saving: { icon: InfoOutline, text: t("project_test_case.save.saving"), tone: "text-tertiary" },
    saved: { icon: CheckDoneOutline, text: t("project_test_case.save.saved"), tone: "text-success-primary" },
    conflict: {
      icon: WarningTriangleOutline,
      text: t("project_test_case.save.conflict"),
      tone: "text-warning-primary",
    },
    error: { icon: WarningTriangleOutline, text: t("project_test_case.save.error"), tone: "text-danger-primary" },
  }[state];

  const Icon = content.icon;

  return (
    <div
      className={cn("flex items-center gap-1.5 text-11 font-medium", content.tone, className)}
      role="status"
      aria-live="polite"
    >
      <Icon className="size-3.5 shrink-0" />
      <span className="truncate">{content.text}</span>
    </div>
  );
}
