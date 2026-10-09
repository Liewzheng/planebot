/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { TIssueActivity } from "@plane/types";

type TTranslationFunction = (key: string, params?: Record<string, unknown>) => string;

export const getRelationActivityContent = (
  activity: TIssueActivity | undefined,
  t: TTranslationFunction
): string | undefined => {
  if (!activity) return;

  switch (activity.field) {
    case "blocking":
      return activity.old_value === ""
        ? t("issue_activity.relation_blocking_set")
        : t("issue_activity.relation_blocking_removed");
    case "blocked_by":
      return activity.old_value === ""
        ? t("issue_activity.relation_blocked_by_set")
        : t("issue_activity.relation_blocked_by_removed");
    case "duplicate":
      return activity.old_value === ""
        ? t("issue_activity.relation_duplicate_set")
        : t("issue_activity.relation_duplicate_removed");
    case "relates_to":
      return activity.old_value === ""
        ? t("issue_activity.relation_relates_to_set")
        : t("issue_activity.relation_relates_to_removed");
  }

  return;
};
