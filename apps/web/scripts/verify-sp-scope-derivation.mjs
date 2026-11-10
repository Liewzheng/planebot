/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// Usage:
//   node apps/web/scripts/verify-sp-scope-derivation.mjs
//   pnpm --filter web verify:sp-scopes
//
// Executable proof for M83: with `test_case` / `test_run` mirrored into
// `role-capabilities.ts`, picking a role in the service-principal access modal
// must *derive* scope rows for those resources — not merely render two extra
// rows in the read-only capability table.
//
// It imports the real modules the UI uses, so a regression in the production
// derivation fails here:
//   - `roleCapabilityCells` / `expandRoleToCellScopes` / `buildScopeRowsForRole`
//     from role-capabilities.ts (the access modal's grid and "expand to cells"),
//   - `buildProjectScopeRows` from project-access-payloads.ts (the project
//     settings switch, which is what PUTs the scope set).
//
// Node runs the `.ts` sources directly (type stripping); the tiny resolve hook
// below only exists because app source imports siblings without a `.ts`
// extension, which Node's ESM resolver does not guess.

import fs from "node:fs";
import { registerHooks } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const AI_ACCOUNTS = path.resolve(HERE, "../core/components/ai-accounts");

registerHooks({
  resolve(specifier, context, nextResolve) {
    try {
      return nextResolve(specifier, context);
    } catch (error) {
      if (specifier.startsWith(".") && context.parentURL) {
        const candidate = new URL(`${specifier}.ts`, context.parentURL);
        if (fs.existsSync(fileURLToPath(candidate))) return { url: candidate.href, shortCircuit: true };
      }
      throw error;
    }
  },
});

const {
  ACTION_REQUIRED_ROLE,
  PROJECT_SCOPED_RESOURCES,
  ROLE_ADMIN,
  ROLE_GUEST,
  ROLE_MEMBER,
  buildScopeRowsForRole,
  expandRoleToCellScopes,
  requiredRole,
  roleCapabilityCells,
  roleCanPerform,
} = await import(path.join(AI_ACCOUNTS, "role-capabilities.ts"));
const { buildProjectScopeRows } = await import(path.join(AI_ACCOUNTS, "project-access-payloads.ts"));

const PROJECT = "project-under-test";
const TEST_RESOURCES = ["test_case", "test_run"];

const failures = [];
const check = (label, actual, expected) => {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a === e) {
    console.log(`  ✓ ${label}`);
  } else {
    failures.push(`${label}: expected ${e}, got ${a}`);
    console.log(`  ✗ ${label}: expected ${e}, got ${a}`);
  }
};

const cellsFor = (roleCap) =>
  roleCapabilityCells(roleCap)
    .filter((cell) => TEST_RESOURCES.includes(cell.resource))
    .map((cell) => `${cell.resource}:${cell.action}`)
    .toSorted();

const scopeKey = (row) => `${row.project ?? "workspace"} / ${row.resource_type} / ${row.action}`;

console.log("\n== 1. the matrix registers both resources (mirror of actions.py) ==");
for (const resource of TEST_RESOURCES) {
  check(`${resource} read -> GUEST`, requiredRole("read", resource), ROLE_GUEST);
  check(
    `${resource} list -> GUEST (read row satisfies list, engine._scope_action_set)`,
    requiredRole("list", resource),
    ROLE_GUEST
  );
  check(`${resource} create -> MEMBER`, requiredRole("create", resource), ROLE_MEMBER);
  check(`${resource} update -> MEMBER`, requiredRole("update", resource), ROLE_MEMBER);
  check(`${resource} delete -> ADMIN`, requiredRole("delete", resource), ROLE_ADMIN);
}
check(
  "both resources are in PROJECT_SCOPED_RESOURCES",
  PROJECT_SCOPED_RESOURCES.filter((r) => TEST_RESOURCES.includes(r)),
  TEST_RESOURCES
);
check(
  "the two resources add 10 mirror entries",
  Object.keys(ACTION_REQUIRED_ROLE).filter((key) => TEST_RESOURCES.some((r) => key.endsWith(`:${r}`))).length,
  10
);

console.log("\n== 2. role -> capability cells ==");
check("GUEST(5) on test_case/test_run", cellsFor(ROLE_GUEST), ["test_case:read", "test_run:read"]);
check("MEMBER(15) on test_case/test_run", cellsFor(ROLE_MEMBER), [
  "test_case:create",
  "test_case:read",
  "test_case:update",
  "test_run:create",
  "test_run:read",
  "test_run:update",
]);
check("ADMIN(20) on test_case/test_run", cellsFor(ROLE_ADMIN), [
  "test_case:create",
  "test_case:delete",
  "test_case:read",
  "test_case:update",
  "test_run:create",
  "test_run:delete",
  "test_run:read",
  "test_run:update",
]);
check("MEMBER may not delete a test_case", roleCanPerform(ROLE_MEMBER, "delete", "test_case"), false);
check("ADMIN may delete a test_case", roleCanPerform(ROLE_ADMIN, "delete", "test_case"), true);

console.log("\n== 3. expandRoleToCellScopes writes the per-cell rows ==");
const memberRows = expandRoleToCellScopes(ROLE_MEMBER, PROJECT).filter((row) =>
  TEST_RESOURCES.includes(row.resource_type)
);
check("MEMBER(15) per-cell rows for the two resources", memberRows.map(scopeKey).toSorted(), [
  `${PROJECT} / test_case / create`,
  `${PROJECT} / test_case / read`,
  `${PROJECT} / test_case / update`,
  `${PROJECT} / test_run / create`,
  `${PROJECT} / test_run / read`,
  `${PROJECT} / test_run / update`,
]);
const adminRows = expandRoleToCellScopes(ROLE_ADMIN, PROJECT).filter((row) =>
  TEST_RESOURCES.includes(row.resource_type)
);
check("ADMIN(20) per-cell rows for the two resources (the 8 rows)", adminRows.map(scopeKey).toSorted(), [
  `${PROJECT} / test_case / create`,
  `${PROJECT} / test_case / delete`,
  `${PROJECT} / test_case / read`,
  `${PROJECT} / test_case / update`,
  `${PROJECT} / test_run / create`,
  `${PROJECT} / test_run / delete`,
  `${PROJECT} / test_run / read`,
  `${PROJECT} / test_run / update`,
]);
check(
  "a GUEST grant derives read-only cells (nothing over-grants)",
  expandRoleToCellScopes(ROLE_GUEST, PROJECT)
    .filter((row) => TEST_RESOURCES.includes(row.resource_type))
    .map((row) => `${row.resource_type}:${row.action}`)
    .toSorted(),
  ["test_case:read", "test_run:read"]
);

console.log("\n== 4. the payload builders the UI actually calls ==");
check("buildScopeRowsForRole(MEMBER) is the wildcard row", buildScopeRowsForRole(ROLE_MEMBER, PROJECT).map(scopeKey), [
  `${PROJECT} / all / all`,
]);
const carried = [
  { project: "other-project", resource_type: "work_item", action: "read" },
  { project: null, resource_type: "project", action: "read" },
];
const projectScopeRows = buildProjectScopeRows(
  [...carried, { project: PROJECT, resource_type: "work_item", action: "read" }],
  PROJECT,
  ROLE_MEMBER
);
check(
  "buildProjectScopeRows(MEMBER) replaces this project's rows and keeps the rest",
  projectScopeRows.map(scopeKey).toSorted(),
  ["other-project / work_item / read", "workspace / project / read", `${PROJECT} / all / all`].toSorted()
);
check("an unknown role cap derives no rows at all (fail-closed)", buildScopeRowsForRole(0, PROJECT), []);

console.log("\n== 5. what the operator sees after picking a role ==");
for (const [label, roleCap] of [
  ["GUEST (5)", ROLE_GUEST],
  ["MEMBER (15)", ROLE_MEMBER],
  ["ADMIN (20)", ROLE_ADMIN],
]) {
  console.log(`  ${label}: ${cellsFor(roleCap).join(", ")}`);
}
console.log(`  list requests are satisfied by the read row (engine.py::_scope_action_set), so no list row is derived`);

if (failures.length > 0) {
  console.error(`\nFAIL: ${failures.length} derivation assertion(s) failed`);
  process.exit(1);
}
console.log("\n✓ picking a role derives the test_case / test_run scopes");
