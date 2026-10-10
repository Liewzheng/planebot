/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";

/**
 * Static reachability test for the PLANE-76 guards.
 *
 * `apps/live/src/utils/content-replacement.ts` and
 * `apps/live/src/utils/document-balloon.ts` were shipped in m16 but never
 * imported by the live server; tree-shaking dropped them from the bundle and
 * only `document-duplication.ts` was live. This test reads the source files
 * that are supposed to wire the guards in and fails if any import or call
 * disappears — a regression in the wiring is caught at PR time, before the
 * image is built.
 */
describe("PLANE-76 guards are reachable from the live store and invalidate paths", () => {
  const srcDir = path.resolve(__dirname, "../../src");
  const databasePath = path.join(srcDir, "extensions/database.ts");
  const invalidatePath = path.join(srcDir, "controllers/invalidate-document.controller.ts");

  it("extensions/database.ts imports and uses the duplicated-growth, balloon-block, and replacement-floor guards", () => {
    const src = fs.readFileSync(databasePath, "utf-8");

    // Every guard module must be imported.
    expect(src).toMatch(/from\s+["']@\/utils\/content-replacement["']/);
    expect(src).toMatch(/from\s+["']@\/utils\/document-balloon["']/);

    // Every guard function used in the store path must appear at least
    // twice (once in the import, once on a call site) — a bare import with
    // no call site means tree-shaking can still drop it.
    const refs = (name: string): number => (src.match(new RegExp(`\\b${name}\\b`, "g")) ?? []).length;
    expect(refs("looksLikeDuplicatedGrowth")).toBeGreaterThanOrEqual(2);
    expect(refs("duplicatedBlockLength")).toBeGreaterThanOrEqual(2);
    expect(refs("getContentReplacementFloor")).toBeGreaterThanOrEqual(2);
    expect(refs("clearContentReplacement")).toBeGreaterThanOrEqual(2);
    expect(refs("recordDocumentContent")).toBeGreaterThanOrEqual(2);

    // The store path must short-circuit each guard with a force-close so the
    // refused store is not silently persisted.
    expect(src).toMatch(/DOCUMENT_STORE_GUARD: refusing to persist a duplicated document/);
    expect(src).toMatch(/DOCUMENT_STORE_GUARD: refusing to persist a content-replacement revert/);
    expect(src).toMatch(/DOCUMENT_STORE_GUARD: refusing to persist a duplicated-growth document/);
    expect(src).toMatch(/DOCUMENT_STORE_GUARD: refusing to persist a document with a repeated body block/);
  });

  it("controllers/invalidate-document.controller.ts arms the replacement floor when contentTextLength is provided", () => {
    const src = fs.readFileSync(invalidatePath, "utf-8");

    expect(src).toMatch(/from\s+["']@\/utils\/content-replacement["']/);
    const refs = (name: string): number => (src.match(new RegExp(`\\b${name}\\b`, "g")) ?? []).length;
    expect(refs("recordContentReplacement")).toBeGreaterThanOrEqual(2);

    // The schema must accept the optional contentTextLength that the API
    // already sends.
    expect(src).toMatch(/contentTextLength/);
    expect(src).toMatch(/z\.number\(\)\.nonnegative\(\)/);
  });

  it("keeps the existing DOCUMENT_STORE_GUARD log line so the duplication-force-close contract is intact", () => {
    // This is the line the operator grep looks for in the production logs;
    // removing it would silently disable the only previously-wired guard.
    const src = fs.readFileSync(databasePath, "utf-8");
    expect(src).toContain('"DOCUMENT_STORE_GUARD: refusing to persist a duplicated document"');
  });
});
