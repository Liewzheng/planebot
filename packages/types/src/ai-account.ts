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

// ---------------------------------------------------------------------------
// M10 — unified principal-dispatch payload (see
// `apps/api/plane/app/views/principal/base.py`).
//
// The app API exposes one endpoint that hands the web client the
// single-source-of-truth view of who is in the workspace and what
// actions the calling principal can take.  The types here are the
// wire shape; the web store (apps/web/core/store/principal) and the
// live server (apps/live/src/lib/auth.ts) consume them.
// ---------------------------------------------------------------------------

/** Workspace role constant (mirrors `ROLE` in
 *  `plane.app.permissions`).  20 = ADMIN, 15 = MEMBER, 5 = GUEST. */
export type TWorkspaceRoleCode = 20 | 15 | 5;

/** The five actions reported by the dispatch endpoint, resource-type
 *  agnostic.  ``STANDARD_ACTIONS`` in `plane.core.authz.permissions`
 *  is the server-side source of truth; the union must match. */
export type TStandardDispatchAction = "read" | "list" | "create" | "update" | "delete";

/** Resource types registered in the dispatch endpoint's
 *  ``_DISPATCH_RESOURCE_TYPES`` tuple.  Mirrors
 *  ``plane.app.views.principal.base._DISPATCH_RESOURCE_TYPES`` —
 *  keep these in sync when one side adds a new type. */
export type TDispatchResourceType =
  | "project"
  | "work_item"
  | "cycle"
  | "module"
  | "page"
  | "state"
  | "label"
  | "estimate"
  | "intake"
  | "comment"
  | "asset"
  | "sticky"
  | "member"
  | "user";

/** The ``principal`` block at the top of the dispatch payload. */
export type TPrincipalKind = "user" | "service" | "anonymous";

export interface TPrincipalSummary {
  kind: TPrincipalKind;
  id: string;
  /** ``null`` for service principals — the engine resolves the
   *  owner's role on every authorize() call rather than caching it
   *  here. */
  workspace_role: TWorkspaceRoleCode | null;
}

/** One row in the dispatch's ``members`` block.  Already filtered
 *  server-side: bots are excluded, inactive rows are dropped, and
 *  the workspace's ``sp_assignable`` toggle decides whether
 *  service rows appear.  Email is withheld for guest viewers. */
export interface TDispatchMemberRow {
  kind: "user" | "service";
  id: string;
  display_name: string;
  email: string | null;
  avatar: string;
  /** ``null`` for service rows (SPs have no WorkspaceMember role). */
  role: TWorkspaceRoleCode | null;
  is_active: boolean;
}

/** Per-(resource_type, action) decision.  ``effective_role`` is
 *  the role the engine resolved for the calling principal — the
 *  number the front end can compare against any UI thresholds. */
export interface TDispatchActionPermission {
  allowed: boolean;
  effective_role: TWorkspaceRoleCode | null;
  /** Mirrors the ``reason`` field on
   *  ``plane.core.authz.permissions.ActionPermission``. */
  reason: string;
}

export type TDispatchPermissionsMatrix = Record<
  TDispatchResourceType,
  Record<TStandardDispatchAction, TDispatchActionPermission>
>;

/** The full payload returned by
 *  ``GET /api/workspaces/<slug>/principal/permissions/``. */
export interface TPrincipalDispatchPayload {
  principal: TPrincipalSummary;
  members: TDispatchMemberRow[];
  /** Per-workspace switch.  When false, SPs are invisible on every
   *  picker / filter / mention / count surface. */
  sp_assignable: boolean;
  permissions: TDispatchPermissionsMatrix;
}
