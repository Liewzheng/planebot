/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// Usage:
//   tsx packages/i18n/scripts/sync-check.ts          # Report only
//   tsx packages/i18n/scripts/sync-check.ts --ci     # Exit 1 if issues found

import fs from "node:fs";
import path from "node:path";
import type { LocaleData } from "./lib/locale-io.js";
import { LOCALES_DIR, listLocales, loadLocale } from "./lib/locale-io.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Format a number with commas (e.g. 7712 -> "7,712"). */
function fmt(n: number): string {
  return n.toLocaleString("en-US");
}

// ---------------------------------------------------------------------------
// Checks
// ---------------------------------------------------------------------------

interface CollisionEntry {
  key: string;
  files: string[];
}

/** Cross-namespace collision check: same flattened key in multiple namespace files. */
function findCollisions(localeData: LocaleData): CollisionEntry[] {
  const keyToFiles = new Map<string, string[]>();
  for (const ns of localeData.namespaces) {
    for (const key of ns.keys) {
      const existing = keyToFiles.get(key);
      if (existing) {
        existing.push(`${ns.name}.json`);
      } else {
        keyToFiles.set(key, [`${ns.name}.json`]);
      }
    }
  }

  const collisions: CollisionEntry[] = [];
  for (const [key, files] of keyToFiles) {
    if (files.length > 1) {
      collisions.push({ key, files });
    }
  }
  return collisions.toSorted((a, b) => a.key.localeCompare(b.key));
}

interface PathConflict {
  leaf: string;
  branch: string;
}

/** Path conflict check: a key is both a leaf AND a prefix of another key. */
function findPathConflicts(localeData: LocaleData): PathConflict[] {
  const allKeysArray = [...localeData.allKeys].toSorted();
  const conflicts: PathConflict[] = [];

  // Build a set of all prefixes used in the keys
  const prefixes = new Set<string>();
  for (const key of allKeysArray) {
    const parts = key.split(".");
    for (let i = 1; i < parts.length; i++) {
      prefixes.add(parts.slice(0, i).join("."));
    }
  }

  // A conflict exists when a leaf key is also a prefix
  for (const key of allKeysArray) {
    if (prefixes.has(key)) {
      // Find one example of a key that extends this prefix
      const extending = allKeysArray.find((k) => k.startsWith(key + "."));
      if (extending) {
        conflicts.push({ leaf: key, branch: extending });
      }
    }
  }

  return conflicts;
}

interface LocaleComparison {
  locale: string;
  totalKeys: number;
  missingKeys: string[];
  staleKeys: string[];
  coverage: number; // 0-100
}

function compareToEnglish(enKeys: Set<string>, other: LocaleData): LocaleComparison {
  const missingKeys: string[] = [];
  const staleKeys: string[] = [];

  for (const key of enKeys) {
    if (!other.allKeys.has(key)) {
      missingKeys.push(key);
    }
  }

  for (const key of other.allKeys) {
    if (!enKeys.has(key)) {
      staleKeys.push(key);
    }
  }

  const coverage = enKeys.size > 0 ? ((enKeys.size - missingKeys.length) / enKeys.size) * 100 : 100;

  return {
    locale: other.locale,
    totalKeys: other.allKeys.size,
    missingKeys: missingKeys.toSorted(),
    staleKeys: staleKeys.toSorted(),
    coverage,
  };
}

// ---------------------------------------------------------------------------
// Guards
// ---------------------------------------------------------------------------

/**
 * Confirmation token the leave-project modal compares the typed value against.
 * The same literal is shown by confirm_placeholder, injected into
 * confirm_instruction and named by toast_confirm_mismatch, so all four places
 * have to stay byte-identical.
 * Source: apps/web/core/components/project/leave-project-modal.tsx:66
 */
const LEAVE_PROJECT_CONFIRM_TOKEN = "Leave Project";

/** Modal that owns the token, relative to the repository root. */
const LEAVE_PROJECT_MODAL_PATH = "apps/web/core/components/project/leave-project-modal.tsx";

/**
 * Values where a hyphen in front of an ICU placeholder belongs to the word
 * itself, so the dash has to stay attached. The exemption only holds while the
 * value matches exactly — changing it forces a conscious decision.
 */
const DASH_PLACEHOLDER_EXEMPTIONS: ReadonlyArray<{
  locale: string;
  namespace: string;
  key: string;
  value: string;
}> = [
  // German compounds the loanword with a hyphen ("Backlog-Arbeit").
  { locale: "de", namespace: "workspace", key: "workspace_analytics.backlog_work_items", value: "Backlog-{entity}" },
];

interface GuardViolation {
  locale: string;
  key: string;
  message: string;
}

/** Collect every string leaf of a namespace together with its dotted key. */
function collectStringLeaves(data: Record<string, unknown>, prefix = ""): Array<{ key: string; value: string }> {
  const leaves: Array<{ key: string; value: string }> = [];
  for (const [k, v] of Object.entries(data)) {
    const full = prefix ? `${prefix}.${k}` : k;
    if (typeof v === "string") {
      leaves.push({ key: full, value: v });
    } else if (v !== null && typeof v === "object" && !Array.isArray(v)) {
      leaves.push(...collectStringLeaves(v as Record<string, unknown>, full));
    }
  }
  return leaves;
}

/** Read a dotted path out of a namespace object. */
function readPath(data: Record<string, unknown>, dottedPath: string): unknown {
  return dottedPath
    .split(".")
    .reduce<unknown>(
      (acc, part) => (acc !== null && typeof acc === "object" ? (acc as Record<string, unknown>)[part] : undefined),
      data
    );
}

/**
 * Guard 1: a hyphen glued to an ICU placeholder ("the project -{name}") leaves a
 * dangling dash in the rendered sentence.
 */
function findDashPlaceholderViolations(localeData: LocaleData): GuardViolation[] {
  const violations: GuardViolation[] = [];
  for (const ns of localeData.namespaces) {
    for (const { key, value } of collectStringLeaves(ns.data)) {
      if (!value.includes("-{")) continue;
      const exempt = DASH_PLACEHOLDER_EXEMPTIONS.some(
        (e) => e.locale === localeData.locale && e.namespace === ns.name && e.key === key && e.value === value
      );
      if (exempt) continue;
      violations.push({
        locale: localeData.locale,
        key: `${ns.name}.json:${key}`,
        message: `hyphen is glued to the ICU placeholder: ${JSON.stringify(value)}`,
      });
    }
  }
  return violations;
}

/** Extract the single-quoted literals a message asks the user to type. */
function quotedLiterals(value: string): string[] {
  return [...value.matchAll(/'([^']+)'/g)].map((match) => match[1]);
}

/**
 * Guard 2: the confirmation token has to be identical in the placeholder, in the
 * instruction, in the mismatch toast and in the modal's own comparison.
 */
function findConfirmTokenViolations(localeData: LocaleData): GuardViolation[] {
  const violations: GuardViolation[] = [];
  const projectNs = localeData.namespaces.find((ns) => ns.name === "project");
  if (!projectNs) return violations;

  const placeholder = readPath(projectNs.data, "project.modals.leave_project.confirm_placeholder");
  const instruction = readPath(projectNs.data, "project.modals.leave_project.confirm_instruction");
  const toast = readPath(projectNs.data, "project.modals.leave_project.toast_confirm_mismatch");

  if (typeof placeholder !== "string") {
    violations.push({
      locale: localeData.locale,
      key: "project.json:project.modals.leave_project.confirm_placeholder",
      message: "missing or not a string",
    });
  } else {
    const literals = quotedLiterals(placeholder);
    if (literals.length === 0) {
      violations.push({
        locale: localeData.locale,
        key: "project.json:project.modals.leave_project.confirm_placeholder",
        message: `does not name the confirmation token ${JSON.stringify(LEAVE_PROJECT_CONFIRM_TOKEN)}: ${JSON.stringify(placeholder)}`,
      });
    } else if (!literals.includes(LEAVE_PROJECT_CONFIRM_TOKEN)) {
      violations.push({
        locale: localeData.locale,
        key: "project.json:project.modals.leave_project.confirm_placeholder",
        message: `asks for ${literals.map((l) => JSON.stringify(l)).join(", ")} instead of ${JSON.stringify(LEAVE_PROJECT_CONFIRM_TOKEN)}`,
      });
    }
  }

  if (typeof instruction !== "string") {
    violations.push({
      locale: localeData.locale,
      key: "project.json:project.modals.leave_project.confirm_instruction",
      message: "missing or not a string",
    });
  } else {
    if (!instruction.includes("{value}")) {
      violations.push({
        locale: localeData.locale,
        key: "project.json:project.modals.leave_project.confirm_instruction",
        message: `does not interpolate the confirmation token {value}: ${JSON.stringify(instruction)}`,
      });
    }
    for (const literal of quotedLiterals(instruction)) {
      if (literal !== LEAVE_PROJECT_CONFIRM_TOKEN) {
        violations.push({
          locale: localeData.locale,
          key: "project.json:project.modals.leave_project.confirm_instruction",
          message: `names ${JSON.stringify(literal)} instead of ${JSON.stringify(LEAVE_PROJECT_CONFIRM_TOKEN)}`,
        });
      }
    }
  }

  if (typeof toast !== "string") {
    violations.push({
      locale: localeData.locale,
      key: "project.json:project.modals.leave_project.toast_confirm_mismatch",
      message: "missing or not a string",
    });
  } else if (!quotedLiterals(toast).includes(LEAVE_PROJECT_CONFIRM_TOKEN)) {
    violations.push({
      locale: localeData.locale,
      key: "project.json:project.modals.leave_project.toast_confirm_mismatch",
      message: `does not name the confirmation token ${JSON.stringify(LEAVE_PROJECT_CONFIRM_TOKEN)}: ${JSON.stringify(toast)}`,
    });
  }

  return violations;
}

/**
 * Cross-check the token constant against the modal source, so the locales and
 * the runtime comparison cannot drift apart silently.
 */
function findModalSourceViolations(): { checked: boolean; violations: GuardViolation[] } {
  const repoRoot = path.resolve(import.meta.dirname, "../../..");
  const modalPath = path.join(repoRoot, LEAVE_PROJECT_MODAL_PATH);
  if (!fs.existsSync(modalPath)) return { checked: false, violations: [] };

  const source = fs.readFileSync(modalPath, "utf8");
  const violations: GuardViolation[] = [];

  const comparisons = [...source.matchAll(/confirmLeave(?:\.trim\(\))?\s*===?\s*"([^"]*)"/g)].map((m) => m[1]);
  if (comparisons.length === 0) {
    violations.push({
      locale: "-",
      key: LEAVE_PROJECT_MODAL_PATH,
      message: "no confirmLeave comparison found — update the guard in packages/i18n/scripts/sync-check.ts",
    });
  } else if (!comparisons.includes(LEAVE_PROJECT_CONFIRM_TOKEN)) {
    violations.push({
      locale: "-",
      key: LEAVE_PROJECT_MODAL_PATH,
      message: `compares against ${comparisons.map((c) => JSON.stringify(c)).join(", ")} instead of ${JSON.stringify(LEAVE_PROJECT_CONFIRM_TOKEN)}`,
    });
  }

  const injected = [...source.matchAll(/\{\s*value:\s*"([^"]*)"\s*\}/g)].map((m) => m[1]);
  if (injected.length === 0) {
    violations.push({
      locale: "-",
      key: LEAVE_PROJECT_MODAL_PATH,
      message: "no value passed to confirm_instruction — update the guard in packages/i18n/scripts/sync-check.ts",
    });
  } else if (!injected.includes(LEAVE_PROJECT_CONFIRM_TOKEN)) {
    violations.push({
      locale: "-",
      key: LEAVE_PROJECT_MODAL_PATH,
      message: `passes ${injected.map((v) => JSON.stringify(v)).join(", ")} to confirm_instruction instead of ${JSON.stringify(LEAVE_PROJECT_CONFIRM_TOKEN)}`,
    });
  }

  return { checked: true, violations };
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

function main() {
  const ciMode = process.argv.includes("--ci");

  // Discover all locale directories
  const localeDirs = listLocales();

  if (!localeDirs.includes("en")) {
    console.error("ERROR: English locale (en) not found in", LOCALES_DIR);
    process.exit(1);
  }

  // Load all locales
  const localeDataMap = new Map<string, LocaleData>();
  for (const locale of localeDirs) {
    localeDataMap.set(locale, loadLocale(locale));
  }

  const enData = localeDataMap.get("en")!;

  // Run checks
  const collisions = findCollisions(enData);
  const pathConflicts = findPathConflicts(enData);

  // Run guards
  const dashViolations: GuardViolation[] = [];
  const tokenViolations: GuardViolation[] = [];
  for (const locale of localeDirs) {
    const localeData = localeDataMap.get(locale)!;
    dashViolations.push(...findDashPlaceholderViolations(localeData));
    tokenViolations.push(...findConfirmTokenViolations(localeData));
  }
  const modalSource = findModalSourceViolations();
  tokenViolations.push(...modalSource.violations);

  const comparisons: LocaleComparison[] = [];
  for (const locale of localeDirs) {
    if (locale === "en") continue;
    comparisons.push(compareToEnglish(enData.allKeys, localeDataMap.get(locale)!));
  }

  // -------------------------------------------------------------------------
  // Print report
  // -------------------------------------------------------------------------

  let hasFailure = false;

  console.log("\n=== Sync Check Results ===\n");
  console.log(`  en:    ${fmt(enData.allKeys.size)} keys (source)\n`);

  for (const comp of comparisons) {
    const status = comp.missingKeys.length === 0 ? "✓" : "✗";
    const missingStr = comp.missingKeys.length > 0 ? ` — ${fmt(comp.missingKeys.length)} missing` : "";
    const staleStr = comp.staleKeys.length > 0 ? `, ${fmt(comp.staleKeys.length)} stale` : "";
    console.log(
      `  ${status} ${comp.locale.padEnd(10)} ${fmt(comp.totalKeys)} keys (${comp.coverage.toFixed(1)}%)${missingStr}${staleStr}`
    );
    if (comp.missingKeys.length > 0) {
      hasFailure = true;
    }
  }

  // Cross-namespace collisions
  if (collisions.length > 0) {
    hasFailure = true;
    console.log("\nCROSS-NAMESPACE COLLISIONS:");
    for (const c of collisions) {
      console.log(`  ✗ "${c.key}" exists in: ${c.files.join(", ")}`);
    }
  }

  // Path conflicts
  if (pathConflicts.length > 0) {
    hasFailure = true;
    console.log("\nPATH CONFLICTS:");
    for (const pc of pathConflicts) {
      console.log(`  ✗ "${pc.leaf}" is a leaf but "${pc.branch}" extends it`);
    }
  }

  // Missing keys detail
  const withMissing = comparisons.filter((c) => c.missingKeys.length > 0);
  if (withMissing.length > 0) {
    console.log("\n--- Missing Keys Detail ---\n");
    for (const comp of withMissing) {
      console.log(`${comp.locale} (${fmt(comp.missingKeys.length)} missing):`);
      const show = comp.missingKeys.slice(0, 20);
      for (const key of show) {
        console.log(`  - ${key}`);
      }
      if (comp.missingKeys.length > 20) {
        console.log(`  ... and ${fmt(comp.missingKeys.length - 20)} more`);
      }
      console.log();
    }
  }

  // Stale keys detail
  const withStale = comparisons.filter((c) => c.staleKeys.length > 0);
  if (withStale.length > 0) {
    console.log("--- Stale Keys Detail ---\n");
    for (const comp of withStale) {
      console.log(`${comp.locale} (${fmt(comp.staleKeys.length)} stale):`);
      const show = comp.staleKeys.slice(0, 20);
      for (const key of show) {
        console.log(`  - ${key}`);
      }
      if (comp.staleKeys.length > 20) {
        console.log(`  ... and ${fmt(comp.staleKeys.length - 20)} more`);
      }
      console.log();
    }
  }

  // Guards
  if (dashViolations.length > 0 || tokenViolations.length > 0) {
    hasFailure = true;
    console.log("\n=== Guard Results ===\n");
    if (dashViolations.length > 0) {
      console.log("HYPHEN GLUED TO AN ICU PLACEHOLDER:");
      for (const v of dashViolations) {
        console.log(`  ✗ ${v.locale} ${v.key} — ${v.message}`);
      }
      console.log();
    }
    if (tokenViolations.length > 0) {
      console.log(`CONFIRMATION TOKEN MISMATCH (expected ${JSON.stringify(LEAVE_PROJECT_CONFIRM_TOKEN)}):`);
      for (const v of tokenViolations) {
        console.log(`  ✗ ${v.locale} ${v.key} — ${v.message}`);
      }
      console.log();
    }
  } else {
    console.log("\n=== Guard Results ===\n");
    console.log(`  ✓ no hyphen glued to an ICU placeholder`);
    console.log(`  ✓ confirmation token ${JSON.stringify(LEAVE_PROJECT_CONFIRM_TOKEN)} is consistent`);
    if (!modalSource.checked) {
      console.log(`  ! ${LEAVE_PROJECT_MODAL_PATH} not found — modal source not cross-checked`);
    }
  }

  // CI exit code
  if (ciMode && hasFailure) {
    console.log("CI mode: exiting with code 1 due to missing keys, collisions, path conflicts, or guard violations.");
    process.exit(1);
  }

  if (!hasFailure) {
    console.log("\nAll locales are in sync with English. No issues found.");
  }
}

try {
  main();
} catch (err) {
  console.error("Sync check failed:", err);
  process.exit(1);
}
