/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

export type TServiceScopeResourceType =
  | "all"
  | "project"
  | "member"
  | "user"
  | "asset"
  | "estimate"
  | "cycle"
  | "module"
  | "sticky"
  | "label"
  | "intake"
  | "work_item"
  | "comment"
  | "state"
  | "page"
  | "invite";

export type TServiceScopeAction = "all" | "read" | "create" | "update" | "delete";

export type TServiceScope = {
  id: string;
  project: string | null;
  resource_type: TServiceScopeResourceType;
  action: TServiceScopeAction;
};

export type TServiceScopeInput = {
  project: string | null;
  resource_type: TServiceScopeResourceType;
  action: TServiceScopeAction;
};

export type TServicePrincipalRoleCap = 20 | 15 | 5;

export type TProjectGrant = {
  id: string;
  project: string;
  role_cap: TServicePrincipalRoleCap;
  is_active: boolean;
};

export type TProjectGrantInput = {
  project: string;
  role_cap: TServicePrincipalRoleCap;
  is_active: boolean;
};

export type TServicePrincipal = {
  id: string;
  name: string;
  description: string;
  avatar: string;
  is_active: boolean;
  workspace: string;
  owner: string;
  scopes: TServiceScope[];
  grants: TProjectGrant[];
  token_last_used: string | null;
  created_at: string;
  updated_at: string;
};

export type TServicePrincipalCreatePayload = {
  name: string;
  description?: string;
  avatar?: string;
  totp_code?: string;
};

export type TServicePrincipalUpdatePayload = {
  name?: string;
  description?: string;
  avatar?: string;
  is_active?: boolean;
};

export type TWorkspaceSPSettings = {
  workspace: string;
  sp_assignable: boolean;
};
