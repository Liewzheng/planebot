/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// Usage:
//   node apps/web/scripts/check-sp-caps.mjs
//   pnpm --filter web check:sp-caps
//
// Guard for the service-principal capability mirror. The API decides every
// service-principal authorization from `apps/api/plane/core/authz/actions.py::
// ACTION_REQUIRED_ROLE`; the web client carries a hand-kept copy of that map in
// `apps/web/core/components/ai-accounts/role-capabilities.ts`. The role ->
// scope derivation and the read-only capability table both read the client
// copy, so a key added on one side and forgotten on the other silently changes
// what a grant derives (M83: `test_case` / `test_run` were registered on the
// API and missing here, so picking a role wrote zero scopes for them and the
// v1 endpoints answered 403).
//
// The check compares both directions — a key present in only one map, and a
// key present in both with a different role floor, are both failures. Exit
// code 1 with a readable diff; exit code 2 when the sources cannot be parsed
// at all (a regex that stops matching must fail loudly, not pass vacuously).

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const BACKEND_ACTIONS = path.join(REPO_ROOT, "apps/api/plane/core/authz/actions.py");
const BACKEND_RESOURCES = path.join(REPO_ROOT, "apps/api/plane/core/authz/resources.py");
const FRONTEND_CAPS = path.join(REPO_ROOT, "apps/web/core/components/ai-accounts/role-capabilities.ts");
const FRONTEND_PICKER = path.join(REPO_ROOT, "apps/web/core/components/ai-accounts/constants.ts");

/** Role names as written in both sources, mapped to the role integer. */
const ROLE_VALUES = { GUEST: 5, MEMBER: 15, ADMIN: 20 };

const read = (file) => {
  if (!fs.existsSync(file)) {
    console.error(`FATAL: ${path.relative(REPO_ROOT, file)} not found`);
    process.exit(2);
  }
  return fs.readFileSync(file, "utf8");
};

/**
 * Lines strictly between the (dedented) line opening a block and the next
 * (dedented) line closing it. Line-anchored on purpose: both sources put the
 * closing bracket at column 0, while their comments and docstrings contain
 * stray braces (`app/permissions/{workspace,project}.py`) that break a naive
 * brace counter.
 */
function blockBody(source, startPattern, endPattern) {
  const lines = source.split("\n");
  const start = lines.findIndex((line) => startPattern.test(line));
  if (start === -1) return null;
  const end = lines.findIndex((line, index) => index > start && endPattern.test(line));
  if (end === -1) return null;
  return lines.slice(start + 1, end).join("\n");
}

// ---------------------------------------------------------------------------
// The matrix: (action, resource) -> role floor
// ---------------------------------------------------------------------------

const backendBody = blockBody(read(BACKEND_ACTIONS), /^ACTION_REQUIRED_ROLE\s*=\s*\{$/, /^\}$/);
const frontendBody = blockBody(read(FRONTEND_CAPS), /^export const ACTION_REQUIRED_ROLE\b.*\{$/, /^\};$/);
if (!backendBody || !frontendBody) {
  console.error("FATAL: could not locate one of the ACTION_REQUIRED_ROLE dicts");
  process.exit(2);
}

/** Backend entries look like `(Action.READ, "test_case"): ROLE_GUEST,`. */
const backendEntries = new Map();
for (const match of backendBody.matchAll(/\(\s*Action\.([A-Z]+)\s*,\s*"([a-z_]+)"\s*\)\s*:\s*ROLE_([A-Z]+)/g)) {
  const [, action, resource, role] = match;
  backendEntries.set(`${action.toLowerCase()}:${resource}`, ROLE_VALUES[role]);
}

/** Frontend entries look like `"read:test_case": ROLE_GUEST,`. */
const frontendEntries = new Map();
for (const match of frontendBody.matchAll(/"([a-z]+):([a-z_]+)"\s*:\s*ROLE_([A-Z]+)/g)) {
  const [, action, resource, role] = match;
  frontendEntries.set(`${action}:${resource}`, ROLE_VALUES[role]);
}

// Self-check: an empty or implausibly small parse means the source shape moved
// and the regexes below stopped seeing entries. Fail instead of reporting
// "in sync" for two empty maps.
const MIN_EXPECTED_ENTRIES = 60;
if (backendEntries.size < MIN_EXPECTED_ENTRIES || frontendEntries.size < MIN_EXPECTED_ENTRIES) {
  console.error(
    `FATAL: parsed only ${backendEntries.size} backend / ${frontendEntries.size} frontend entries ` +
      `(expected at least ${MIN_EXPECTED_ENTRIES}) — the parser no longer matches the source shape`
  );
  process.exit(2);
}

// ---------------------------------------------------------------------------
// The resource vocabulary: what a scope row may name
// ---------------------------------------------------------------------------

const backendResourcesBody = blockBody(read(BACKEND_RESOURCES), /^RESOURCE_CHOICES = \($/, /^\)$/);
if (!backendResourcesBody) {
  console.error("FATAL: could not locate RESOURCE_CHOICES in apps/api/plane/core/authz/resources.py");
  process.exit(2);
}
const backendResources = new Set(
  [...backendResourcesBody.matchAll(/\(\s*ResourceType\.[A-Z_]+\s*,\s*"[^"]*"\s*\)/g)].map((match) =>
    match[0].match(/\(\s*ResourceType\.([A-Z_]+)/)[1].toLowerCase()
  )
);

const pickerBody = blockBody(
  read(FRONTEND_PICKER),
  /^export const SERVICE_PRINCIPAL_SCOPE_RESOURCE_TYPES\b.*\[$/,
  /^\];$/
);
if (!pickerBody) {
  console.error("FATAL: could not locate SERVICE_PRINCIPAL_SCOPE_RESOURCE_TYPES");
  process.exit(2);
}
const frontendResources = new Set([...pickerBody.matchAll(/"([a-z_]+)"/g)].map((match) => match[1]));
if (backendResources.size === 0 || frontendResources.size === 0) {
  console.error("FATAL: parsed an empty resource vocabulary — the parser no longer matches the source shape");
  process.exit(2);
}

// ---------------------------------------------------------------------------
// Report
// ---------------------------------------------------------------------------

const problems = [];
const describe = (entries, key) =>
  `${key} = ROLE_${Object.keys(ROLE_VALUES).find((r) => ROLE_VALUES[r] === entries.get(key))}`;

for (const key of backendEntries.keys()) {
  if (!frontendEntries.has(key)) {
    problems.push(`missing in apps/web role-capabilities.ts: ${describe(backendEntries, key)}`);
  } else if (frontendEntries.get(key) !== backendEntries.get(key)) {
    problems.push(
      `role floor differs for ${key}: backend ${describe(backendEntries, key)}, frontend ${describe(frontendEntries, key)}`
    );
  }
}
for (const key of frontendEntries.keys()) {
  if (!backendEntries.has(key)) {
    problems.push(`missing in apps/api actions.py: ${describe(frontendEntries, key)}`);
  }
}
for (const resource of backendResources) {
  if (!frontendResources.has(resource)) {
    problems.push(`resource "${resource}" is registered on the API but not offered by the web scope picker`);
  }
}
for (const resource of frontendResources) {
  if (!backendResources.has(resource)) {
    problems.push(`resource "${resource}" is offered by the web scope picker but not registered on the API`);
  }
}

console.log(`backend ACTION_REQUIRED_ROLE: ${backendEntries.size} entries / ${backendResources.size} resources`);
console.log(`frontend ACTION_REQUIRED_ROLE: ${frontendEntries.size} entries / ${frontendResources.size} resources`);

if (problems.length > 0) {
  console.error(`\nFAIL: ${problems.length} mismatch(es) between the API matrix and the web mirror\n`);
  for (const problem of problems) console.error(`  ✗ ${problem}`);
  console.error(
    "\nFix: mirror the entry in apps/web/core/components/ai-accounts/role-capabilities.ts " +
      "(ACTION_REQUIRED_ROLE, same order as the Python dict) — see that file's header comment."
  );
  process.exit(1);
}

console.log("✓ the web capability mirror matches apps/api/plane/core/authz/actions.py exactly");
