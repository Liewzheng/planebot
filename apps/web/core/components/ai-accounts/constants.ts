/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { TServicePrincipalRoleCap, TServiceScopeAction, TServiceScopeResourceType } from "@plane/types";

export const SERVICE_PRINCIPALS_LIST = (workspaceSlug: string) => `SERVICE_PRINCIPALS_LIST_${workspaceSlug}`;

export const SERVICE_PRINCIPAL_SCOPES = (workspaceSlug: string, principalId: string) =>
  `SERVICE_PRINCIPAL_SCOPES_${workspaceSlug}_${principalId}`;

export const SERVICE_PRINCIPAL_GRANTS = (workspaceSlug: string, principalId: string) =>
  `SERVICE_PRINCIPAL_GRANTS_${workspaceSlug}_${principalId}`;

export const SERVICE_PRINCIPAL_SCOPE_RESOURCE_TYPES: TServiceScopeResourceType[] = [
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

export const SERVICE_PRINCIPAL_SCOPE_ACTIONS: TServiceScopeAction[] = ["all", "read", "create", "update", "delete"];

export const SERVICE_PRINCIPAL_GRANT_ROLE_CAPS: TServicePrincipalRoleCap[] = [20, 15, 5];

// Backend step-up error codes (plane.authentication AUTHENTICATION_ERROR_CODES)
export const MFA_ERROR_CODE_REQUIRED = 5200;
export const MFA_ERROR_CODE_INVALID = 5205;

/** Map a step-up failure to an inline message; returns undefined for non-MFA errors. */
export const getMfaStepUpError = (err: unknown, t: (key: string) => string): string | undefined => {
  const code = (err as { error_code?: number })?.error_code;
  if (code === MFA_ERROR_CODE_REQUIRED)
    return t("workspace_settings.settings.service_principals.step_up.code_required");
  if (code === MFA_ERROR_CODE_INVALID) return t("workspace_settings.settings.service_principals.step_up.code_invalid");
  return undefined;
};
