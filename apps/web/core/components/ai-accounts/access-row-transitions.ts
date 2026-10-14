/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { TServicePrincipalRoleCap } from "@plane/types";
// local imports
import { createGrantRow, createScopeRow } from "./constants";
import type { TGrantRow, TScopeRow } from "./constants";
import { buildProjectScopeRows } from "./project-access-payloads";
import { buildScopeRowsForRole } from "./role-capabilities";

/**
 * The editable pair the access modal works on. Both PUTs replace their whole
 * set, so the rows here *are* the request bodies (`save` strips the `key`).
 */
export type TAccessRows = {
  grantRows: TGrantRow[];
  scopeRows: TScopeRow[];
};

/**
 * Role picked for one project in the grid.
 *
 * Picking a role rewrites that project's grant and rebuilds its scope rows from
 * the same role, so the two halves the engine needs cannot drift apart. The
 * rebuild collapses the project's rows into the single `<project> / all / all`
 * row `buildScopeRowsForRole` returns — per-cell rows the operator hand-tuned in
 * the advanced editor are replaced, and the grant's role cap is what narrows the
 * row to the role's real capabilities.
 *
 * "No access" removes the grant row **only** and keeps the project's scope rows.
 * The engine evaluates the scope step (`engine.py:249-272`) before the grant
 * step (`277-295`), so clearing the scopes too would flip the deny from
 * `DENY_GRANT_MISS` to `DENY_SCOPE_MISS` — pointing a workspace admin at the
 * scopes when the removed grant is the actual cause. The project-settings switch
 * follows the same rule.
 */
export const applyRoleToProject = (
  { grantRows, scopeRows }: TAccessRows,
  projectId: string,
  role: TServicePrincipalRoleCap | null
): TAccessRows => {
  const index = grantRows.findIndex((row) => row.project === projectId);
  const nextGrantRows =
    role === null
      ? grantRows.filter((row) => row.project !== projectId)
      : index === -1
        ? [...grantRows, createGrantRow({ project: projectId, role_cap: role, is_active: true })]
        : grantRows.map((row, rowIndex) => (rowIndex === index ? { ...row, role_cap: role, is_active: true } : row));
  // A revoke touches the grant half only — see the docstring.
  if (role === null) return { grantRows: nextGrantRows, scopeRows };
  return {
    grantRows: nextGrantRows,
    scopeRows: [
      ...scopeRows.filter((row) => row.project !== projectId),
      ...buildScopeRowsForRole(role, projectId).map(createScopeRow),
    ],
  };
};

/**
 * Bulk form of :func:`applyRoleToProject` for the "apply to all projects"
 * control. Only the projects handed in are touched; grants for archived or
 * otherwise unlisted projects are carried over, and workspace-wide scope rows
 * (`project === null`) are left alone — this control only speaks about the
 * projects the grid lists.
 */
export const applyRoleToProjects = (
  { grantRows, scopeRows }: TAccessRows,
  projectIds: string[],
  role: TServicePrincipalRoleCap | null
): TAccessRows => {
  const projectIdSet = new Set(projectIds);
  const untouchedGrants = grantRows.filter((row) => !projectIdSet.has(row.project));
  const nextGrantRows =
    role === null
      ? untouchedGrants
      : [
          ...untouchedGrants,
          ...projectIds.map((projectId) => createGrantRow({ project: projectId, role_cap: role, is_active: true })),
        ];
  if (role === null) return { grantRows: nextGrantRows, scopeRows };
  const untouchedScopes = scopeRows.filter((row) => row.project === null || !projectIdSet.has(row.project));
  return {
    grantRows: nextGrantRows,
    scopeRows: [
      ...untouchedScopes,
      ...projectIds.flatMap((projectId) => buildScopeRowsForRole(role, projectId).map(createScopeRow)),
    ],
  };
};

/**
 * Heal projects whose active grant has no scope row covering it — the
 * half-filled pair the grant-only switch produced before M43. Each project gets
 * the rows the project-settings switch writes on a fresh grant
 * (`buildProjectScopeRows`, which wraps the same `buildScopeRowsForRole`
 * mapping), so the repair cannot invent a second role -> scope mapping.
 *
 * Projects without a grant row are skipped: there is no role cap to derive rows
 * from, and a scope row without a grant would still deny (`DENY_GRANT_MISS`).
 */
export const fillScopesForProjects = ({ grantRows, scopeRows }: TAccessRows, projectIds: string[]): TAccessRows => {
  const roleCapByProject = new Map<string, TServicePrincipalRoleCap>();
  grantRows.forEach((row) => {
    if (row.project && !roleCapByProject.has(row.project)) roleCapByProject.set(row.project, row.role_cap);
  });
  const nextScopeRows = projectIds.reduce<TScopeRow[]>((rows, projectId) => {
    const roleCap = roleCapByProject.get(projectId);
    if (roleCap === undefined) return rows;
    return buildProjectScopeRows(rows, projectId, roleCap).map(createScopeRow);
  }, scopeRows);
  return { grantRows, scopeRows: nextScopeRows };
};
