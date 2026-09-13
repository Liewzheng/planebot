/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { TAIScopeAction, TAIScopeResourceType } from "@plane/types";

export const AI_ACCOUNTS_LIST = (workspaceSlug: string) => `AI_ACCOUNTS_LIST_${workspaceSlug}`;

export const AI_ACCOUNT_SCOPES = (workspaceSlug: string, accountId: string) =>
  `AI_ACCOUNT_SCOPES_${workspaceSlug}_${accountId}`;

export const AI_SCOPE_RESOURCE_TYPES: TAIScopeResourceType[] = [
  "all",
  "project",
  "member",
  "user",
  "asset",
  "estimate",
  "cycle",
  "module",
  "sticky",
  "label",
  "intake",
  "work_item",
  "comment",
  "state",
  "page",
  "invite",
];

export const AI_SCOPE_ACTIONS: TAIScopeAction[] = ["all", "read", "create", "update", "delete"];

// Backend step-up error codes (plane.authentication AUTHENTICATION_ERROR_CODES)
export const MFA_ERROR_CODE_REQUIRED = 5200;
export const MFA_ERROR_CODE_INVALID = 5205;

/** Map a step-up failure to an inline message; returns undefined for non-MFA errors. */
export const getMfaStepUpError = (err: unknown, t: (key: string) => string): string | undefined => {
  const code = (err as { error_code?: number })?.error_code;
  if (code === MFA_ERROR_CODE_REQUIRED) return t("workspace_settings.settings.ai_accounts.step_up.code_required");
  if (code === MFA_ERROR_CODE_INVALID) return t("workspace_settings.settings.ai_accounts.step_up.code_invalid");
  return undefined;
};
