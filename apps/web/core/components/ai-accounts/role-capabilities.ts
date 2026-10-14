/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type {
  TServicePrincipalRoleCap,
  TServiceScopeAction,
  TServiceScopeInput,
  TServiceScopeResourceType,
} from "@plane/types";

/**
 * Role integers, matching ``plane.app.permissions.ROLE`` and
 * ``plane.core.authz.actions``.
 */
export const ROLE_GUEST = 5;
export const ROLE_MEMBER = 15;
export const ROLE_ADMIN = 20;

/**
 * Client mirror of ``apps/api/plane/core/authz/actions.py::ACTION_REQUIRED_ROLE``
 * — the single point that decides the minimum role a service principal needs
 * for a (action, resource_type) pair. Entries are listed in the same order as
 * the Python dict so the two can be diffed side by side.
 *
 * The capability table (role-capability-table.tsx) renders straight off this
 * map and the role → scope derivation below reads from it, so both surfaces
 * show exactly what the engine enforces. When an entry is added or re-floored
 * on the API side, mirror it here in the same change.
 *
 * A missing key means the pair is unregistered: ``authorize()`` default-denies
 * it (``actions.required_role`` returns ``None``).
 */
export const ACTION_REQUIRED_ROLE: Record<string, number> = {
  // Workspace-level resources (read-only in practice — see
  // WORKSPACE_LEVEL_RESOURCES below; writes are rejected at the engine
  // boundary, so the write floors here are carried for symmetry only).
  "read:project": ROLE_GUEST,
  "list:project": ROLE_GUEST,
  "create:project": ROLE_ADMIN,
  "update:project": ROLE_ADMIN,
  "delete:project": ROLE_ADMIN,
  "read:member": ROLE_GUEST,
  "list:member": ROLE_GUEST,
  "create:member": ROLE_ADMIN,
  "update:member": ROLE_ADMIN,
  "delete:member": ROLE_ADMIN,
  "read:user": ROLE_GUEST,
  "list:user": ROLE_GUEST,
  "read:invite": ROLE_MEMBER,
  "list:invite": ROLE_MEMBER,
  "create:invite": ROLE_ADMIN,
  "update:invite": ROLE_ADMIN,
  "delete:invite": ROLE_ADMIN,
  // Project-scoped resources.
  "read:estimate": ROLE_GUEST,
  "list:estimate": ROLE_GUEST,
  "create:estimate": ROLE_ADMIN,
  "update:estimate": ROLE_ADMIN,
  "delete:estimate": ROLE_ADMIN,
  "read:label": ROLE_GUEST,
  "list:label": ROLE_GUEST,
  "create:label": ROLE_MEMBER,
  "update:label": ROLE_MEMBER,
  "delete:label": ROLE_ADMIN,
  "read:asset": ROLE_GUEST,
  "list:asset": ROLE_GUEST,
  "create:asset": ROLE_MEMBER,
  "update:asset": ROLE_MEMBER,
  "delete:asset": ROLE_ADMIN,
  "read:cycle": ROLE_GUEST,
  "list:cycle": ROLE_GUEST,
  "create:cycle": ROLE_MEMBER,
  "update:cycle": ROLE_MEMBER,
  "delete:cycle": ROLE_ADMIN,
  "read:module": ROLE_GUEST,
  "list:module": ROLE_GUEST,
  "create:module": ROLE_MEMBER,
  "update:module": ROLE_MEMBER,
  "delete:module": ROLE_ADMIN,
  "read:sticky": ROLE_GUEST,
  "list:sticky": ROLE_GUEST,
  "create:sticky": ROLE_MEMBER,
  "update:sticky": ROLE_MEMBER,
  "delete:sticky": ROLE_MEMBER,
  "read:intake": ROLE_MEMBER,
  "list:intake": ROLE_MEMBER,
  "create:intake": ROLE_MEMBER,
  "update:intake": ROLE_MEMBER,
  "delete:intake": ROLE_ADMIN,
  "read:work_item": ROLE_GUEST,
  "list:work_item": ROLE_GUEST,
  "create:work_item": ROLE_MEMBER,
  "update:work_item": ROLE_MEMBER,
  "delete:work_item": ROLE_MEMBER,
  "read:comment": ROLE_GUEST,
  "list:comment": ROLE_GUEST,
  "create:comment": ROLE_MEMBER,
  "update:comment": ROLE_MEMBER,
  "delete:comment": ROLE_MEMBER,
  "read:state": ROLE_GUEST,
  "list:state": ROLE_GUEST,
  "create:state": ROLE_ADMIN,
  "update:state": ROLE_MEMBER,
  "delete:state": ROLE_ADMIN,
  "read:page": ROLE_GUEST,
  "list:page": ROLE_GUEST,
  "create:page": ROLE_MEMBER,
  "update:page": ROLE_MEMBER,
  "delete:page": ROLE_MEMBER,
};

/**
 * Mirror of ``apps/api/plane/core/authz/resources.py::WORKSPACE_LEVEL_RESOURCES``.
 * These resource types exist only at the workspace level: ``authorize()`` needs
 * a workspace-wide scope row for them and rejects every write outright
 * (``ALLOWED_WORKSPACE_LEVEL_ACTIONS`` is ``{"read"}``). Their matrix write
 * floors are therefore unreachable and the capability table renders them as
 * blocked instead of quoting a role.
 */
export const WORKSPACE_LEVEL_RESOURCES: TServiceScopeResourceType[] = ["project", "member", "user", "invite"];

/**
 * The project-scoped counterparts. ``label`` and ``estimate`` are listed here
 * (their v1 surfaces live under ``…/projects/<project_id>/…``), even though a
 * write with no resolved project is refused as a workspace-level write.
 */
export const PROJECT_SCOPED_RESOURCES: TServiceScopeResourceType[] = [
  "work_item",
  "comment",
  "page",
  "cycle",
  "module",
  "state",
  "asset",
  "sticky",
  "intake",
  "label",
  "estimate",
];

/**
 * Actions the capability table and the scope derivation cover. ``all`` is a
 * scope-row wildcard (not a capability) and ``list`` is satisfied by a ``read``
 * scope row (``engine._scope_action_set``), which is why neither is presented
 * as a per-cell column — the same reason the scope picker
 * (SERVICE_PRINCIPAL_SCOPE_ACTIONS) offers no ``list``.
 */
export const CAPABILITY_ACTIONS: TServiceScopeAction[] = ["read", "create", "update", "delete"];

/** Minimum role for a pair, or ``null`` when the pair is unregistered. */
export const requiredRole = (action: TServiceScopeAction, resource: TServiceScopeResourceType): number | null =>
  ACTION_REQUIRED_ROLE[`${action}:${resource}`] ?? null;

export const isWorkspaceLevelResource = (resource: TServiceScopeResourceType): boolean =>
  WORKSPACE_LEVEL_RESOURCES.includes(resource);

/** True when ``roleCap`` is allowed to perform the action on the resource. */
export const roleCanPerform = (
  roleCap: TServicePrincipalRoleCap,
  action: TServiceScopeAction,
  resource: TServiceScopeResourceType
): boolean => {
  const required = requiredRole(action, resource);
  return required !== null && roleCap >= required;
};

/**
 * The (resource, action) cells a role can perform, derived from the matrix.
 * Workspace-level rows are filtered out: their writes are blocked by the engine
 * regardless of the role, and their reads need a workspace-wide scope row that
 * is not tied to any project.
 */
export const roleCapabilityCells = (
  roleCap: TServicePrincipalRoleCap
): { resource: TServiceScopeResourceType; action: TServiceScopeAction }[] =>
  PROJECT_SCOPED_RESOURCES.flatMap((resource) =>
    CAPABILITY_ACTIONS.filter((action) => roleCanPerform(roleCap, action, resource)).map((action) => ({
      resource,
      action,
    }))
  );

/**
 * Scope rows to write when a project is assigned ``roleCap``.
 *
 * The decided model for this surface: one ``<project> / all / all`` row plus the
 * project's grant role cap equals "do everything that role can do in that
 * project". The row is deliberately not expanded per cell — the engine narrows
 * every cell with ``grant.role_cap`` (``ACTION_REQUIRED_ROLE`` is compared with
 * ``<=``), so the effective set is exactly :func:`roleCapabilityCells`, and the
 * per-project difference stays in the grant where it belongs. Use
 * :func:`expandRoleToCellScopes` when an operator wants the explicit per-cell
 * form instead.
 */
export const buildScopeRowsForRole = (roleCap: TServicePrincipalRoleCap, projectId: string): TServiceScopeInput[] => {
  if (roleCapabilityCells(roleCap).length === 0) return [];
  return [{ project: projectId, resource_type: "all", action: "all" }];
};

/** The explicit per-cell form of :func:`buildScopeRowsForRole`. */
export const expandRoleToCellScopes = (roleCap: TServicePrincipalRoleCap, projectId: string): TServiceScopeInput[] =>
  roleCapabilityCells(roleCap).map(({ resource, action }) => ({
    project: projectId,
    resource_type: resource,
    action,
  }));

/**
 * True for cells whose outcome also depends on resource ownership — the M40
 * rule: a sub-admin role cap may only ``update`` / ``delete`` resources the
 * principal itself created, and unknown ownership is denied. Cells that already
 * require ADMIN are unaffected (an admin acts on anything).
 */
export const isOwnershipGated = (action: TServiceScopeAction, resource: TServiceScopeResourceType): boolean => {
  if (action !== "update" && action !== "delete") return false;
  if (isWorkspaceLevelResource(resource)) return false;
  const required = requiredRole(action, resource);
  return required !== null && required < ROLE_ADMIN;
};
