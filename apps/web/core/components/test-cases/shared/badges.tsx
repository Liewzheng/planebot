/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useTranslation } from "@plane/i18n";
import { Badge } from "@plane/propel/badge";
import type { TBadgeVariant } from "@plane/propel/badge";
import type { TTestRunConclusion } from "@plane/types";

/** The colour of a priority: the higher the priority, the louder the badge. */
const PRIORITY_VARIANTS: Record<string, TBadgeVariant> = {
  P0: "danger",
  P1: "warning",
  P2: "brand",
  P3: "neutral",
};

export function TestCasePriorityBadge(props: { priority: string }) {
  const { priority } = props;
  if (!priority) return null;

  return (
    <Badge variant={PRIORITY_VARIANTS[priority] ?? "neutral"} size="sm">
      {priority}
    </Badge>
  );
}

/** The state of a case: an active one and a voided one. */
export function TestCaseStatusBadge(props: { status: string }) {
  const { status } = props;
  const { t } = useTranslation();

  return (
    <Badge variant={status === "void" ? "neutral" : "success"} size="sm">
      {t(`project_test_case.status.${status}`)}
    </Badge>
  );
}

/**
 * The conclusion of the last execution of a case. The value itself is the
 * one the server wrote (`PASS`, `FAIL`, `N/A`, `MANUAL`); the badge carries
 * the readable word.
 *
 * Three states have to be told apart: the case was never executed (no
 * batch carried it), the batch that carried it has no conclusion yet, and
 * the batch has one.
 */
export function TestCaseResultBadge(props: { conclusion: TTestRunConclusion | string | null | undefined }) {
  const { conclusion } = props;
  const { t } = useTranslation();

  if (conclusion === null || conclusion === undefined)
    return <span className="text-tertiary">{t("project_test_case.last_result.none")}</span>;

  if (conclusion === "") return <span className="text-tertiary">{t("project_test_case.last_result.pending")}</span>;

  const variant: TBadgeVariant =
    conclusion === "PASS"
      ? "success"
      : conclusion === "FAIL"
        ? "danger"
        : conclusion === "MANUAL"
          ? "warning"
          : "neutral";

  return (
    <Badge variant={variant} size="sm">
      {t(`project_test_case.last_result.${conclusion === "N/A" ? "na" : conclusion.toLowerCase()}`)}
    </Badge>
  );
}
