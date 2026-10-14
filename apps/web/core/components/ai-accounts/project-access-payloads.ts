/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type {
  TProjectGrant,
  TProjectGrantInput,
  TServicePrincipalRoleCap,
  TServiceScope,
  TServiceScopeInput,
} from "@plane/types";
// local imports
import { buildScopeRowsForRole } from "./role-capabilities";

/**
 * Role cap a first-time grant gets. Member (15) — never Admin, so flipping the
 * switch on cannot silently elevate a principal.
 */
export const DEFAULT_PROJECT_ROLE_CAP: TServicePrincipalRoleCap = 15;

type TGrantRow = Pick<TProjectGrant, "project" | "role_cap" | "is_active">;
type TScopeRow = Pick<TServiceScope, "project" | "resource_type" | "action">;

const toGrantInput = ({ project, role_cap, is_active }: TGrantRow): TProjectGrantInput => ({
  project,
  role_cap,
  is_active,
});

const toScopeInput = ({ project, resource_type, action }: TScopeRow): TServiceScopeInput => ({
  project,
  resource_type,
  action,
});

/**
 * The full grant set to PUT after the project-access switch flips.
 *
 * `PUT .../grants/` replaces the whole set, so every other project's row is
 * carried over unchanged. Turning the switch ON keeps the row's existing role
 * cap when one is already there — re-enabling a grant the inner "active" switch
 * had disabled must not silently reset an Admin cap to Member — and falls back
 * to Member for a first-time grant (or one after a revoke, which drops the row
 * and with it the cap). Turning it OFF drops the row: the switch's checked
 * state *is* "this project has a grant", so removing the row is what keeps the
 * rendered state in step with the engine — a row left behind with
 * `is_active=false` would render "Granted" while the engine denies.
 */
export const buildProjectGrantRows = (
  current: TGrantRow[],
  projectId: string,
  nextIsGranted: boolean
): TProjectGrantInput[] => {
  const preserved = current.filter((grant) => grant.project !== projectId).map(toGrantInput);
  if (!nextIsGranted) return preserved;
  const existing = current.find((grant) => grant.project === projectId);
  return [
    ...preserved,
    { project: projectId, role_cap: existing?.role_cap ?? DEFAULT_PROJECT_ROLE_CAP, is_active: true },
  ];
};

/**
 * The full scope set to PUT when the switch turns ON: this project's rows are
 * rebuilt from the role cap through the same role -> scope mapping the access
 * modal uses (`buildScopeRowsForRole`), and every other row — other projects
 * and the workspace-wide rows (`project === null`) — is carried over untouched.
 *
 * Only called for a grant, never for a revoke. On a revoke the scope rows are
 * deliberately left in place: the engine checks the scope before the grant, so
 * clearing them would flip the deny from `DENY_GRANT_MISS` (accurate — the
 * project has no active grant) to `DENY_SCOPE_MISS`, which tells a workspace
 * admin to go add scopes when the grant is what is missing.
 */
export const buildProjectScopeRows = (
  current: TScopeRow[],
  projectId: string,
  roleCap: TServicePrincipalRoleCap
): TServiceScopeInput[] => [
  ...current.filter((scope) => scope.project !== projectId).map(toScopeInput),
  ...buildScopeRowsForRole(roleCap, projectId),
];
