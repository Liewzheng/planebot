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
 *
 * The runtime counterpart (`tests/extensions/database.test.ts`) drives the
 * real `storeDocument` with a mocked page service so it catches regression
 * modes that this static check would miss (gating a guard behind a constant
 * condition, moving it into a dead branch). The static check below adds
 * belt-and-suspenders by parsing the `storeDocument` body to make sure each
 * guard call sits inside it, and asserting the no-force-close contract for
 * the two false-positive-prone guards.
 */

const SRC_DIR = path.resolve(__dirname, "../../src");
const DATABASE_PATH = path.join(SRC_DIR, "extensions/database.ts");
const INVALIDATE_PATH = path.join(SRC_DIR, "controllers/invalidate-document.controller.ts");

function readFile(p: string): string {
  return fs.readFileSync(p, "utf-8");
}

function occurrences(src: string, name: string): number {
  return (src.match(new RegExp(`\\b${name}\\b`, "g")) ?? []).length;
}

// Extract the `storeDocument` function body. It is declared with
// `export const storeDocument = async (...) => { ... }` and ends at the
// matching closing brace before `export class Database`. We slice from
// the declaration of `storeDocument` to the next `export` (the Database
// class) — every guard call we care about sits between those markers.
function extractStoreDocumentBody(src: string): string {
  const start = src.indexOf("export const storeDocument");
  const end = src.indexOf("\nexport class Database", start);
  return start === -1 || end === -1 ? "" : src.slice(start, end);
}

describe("PLANE-76 guards are reachable from the live store and invalidate paths", () => {
  it("extensions/database.ts imports and uses the duplicated-growth, balloon-block, and replacement-floor guards", () => {
    const src = readFile(DATABASE_PATH);

    // Every guard module must be imported.
    expect(src).toMatch(/from\s+["']@\/utils\/content-replacement["']/);
    expect(src).toMatch(/from\s+["']@\/utils\/document-balloon["']/);

    // Every guard function used in the store path must appear at least
    // twice (once in the import, once on a call site) — a bare import with
    // no call site means tree-shaking can still drop it.
    expect(occurrences(src, "looksLikeDuplicatedGrowth")).toBeGreaterThanOrEqual(2);
    expect(occurrences(src, "duplicatedBlockLength")).toBeGreaterThanOrEqual(2);
    expect(occurrences(src, "getContentReplacementFloor")).toBeGreaterThanOrEqual(2);
    expect(occurrences(src, "clearContentReplacement")).toBeGreaterThanOrEqual(2);
    expect(occurrences(src, "recordDocumentContent")).toBeGreaterThanOrEqual(2);

    // The store path must log each refusal so the operator can grep for it.
    expect(src).toMatch(/DOCUMENT_STORE_GUARD: refusing to persist a duplicated document/);
    expect(src).toMatch(/DOCUMENT_STORE_GUARD: refusing to persist a content-replacement revert/);
    expect(src).toMatch(/DOCUMENT_STORE_GUARD: refusing to persist a duplicated-growth document/);
    expect(src).toMatch(/DOCUMENT_STORE_GUARD: refusing to persist a document with a repeated body block/);
  });

  it("each guard call site sits inside the storeDocument function body", () => {
    // The runtime test in tests/extensions/database.test.ts drives the
    // real storeDocument. This static check adds belt-and-suspenders by
    // asserting that each guard's call site is inside storeDocument —
    // not inside a sibling helper that the store path never invokes.
    const src = readFile(DATABASE_PATH);
    const body = extractStoreDocumentBody(src);
    expect(body).not.toBe("");

    expect(body).toMatch(/\blooksLikeDuplicatedGrowth\s*\(/);
    expect(body).toMatch(/\bduplicatedBlockLength\s*\(/);
    expect(body).toMatch(/\bgetContentReplacementFloor\s*\(/);
    expect(body).toMatch(/\bclearContentReplacement\s*\(/);
    expect(body).toMatch(/\brecordDocumentContent\s*\(/);
  });

  it("the false-positive-prone growth and block guards do not force-close clients", () => {
    // Per the M22 round-1 review: `looksLikeDuplicatedGrowth` (24-h
    // memory) and `duplicatedBlockLength` (rolling-hash ≥600 chars) can
    // fire on legitimately repetitive content. On refusal they must drop
    // the store WITHOUT unloading the in-memory document so the author's
    // edit survives. `detectDocumentDuplication` and the time-boxed
    // `getContentReplacementFloor` keep their force-close contract.
    const src = readFile(DATABASE_PATH);
    const body = extractStoreDocumentBody(src);

    const slices: Array<{ name: string; re: RegExp }> = [
      { name: "looksLikeDuplicatedGrowth", re: /looksLikeDuplicatedGrowth\s*\([^)]*\)/ },
      { name: "duplicatedBlockLength", re: /duplicatedBlockLength\s*\([^)]*\)/ },
    ];
    for (const { name, re } of slices) {
      const idx = body.search(re);
      expect(idx, `${name} guard must be present in storeDocument`).toBeGreaterThanOrEqual(0);
      // Look at the next ~600 chars after the guard call site — enough
      // to capture the `if (…duplicated) { logger.warn(...); return; }`
      // block without crossing into the next guard.
      const window = new Set(body.slice(idx, idx + 600));
      expect(window.has("forceCloseDocumentAcrossServers"), `${name} guard must not force-close clients`).toBe(false);
      expect(window.has("return"), `${name} guard must short-circuit the store`).toBe(true);
    }
  });

  it("controllers/invalidate-document.controller.ts arms the replacement floor when contentTextLength is provided", () => {
    const src = readFile(INVALIDATE_PATH);

    expect(src).toMatch(/from\s+["']@\/utils\/content-replacement["']/);
    expect(occurrences(src, "recordContentReplacement")).toBeGreaterThanOrEqual(2);

    // The schema must accept the optional contentTextLength that the API
    // already sends.
    expect(src).toMatch(/contentTextLength/);
    expect(src).toMatch(/z\.number\(\)\.nonnegative\(\)/);
  });

  it("keeps the existing DOCUMENT_STORE_GUARD log line so the duplication-force-close contract is intact", () => {
    // This is the line the operator grep looks for in the production logs;
    // removing it would silently disable the only previously-wired guard.
    const src = readFile(DATABASE_PATH);
    expect(src).toContain('"DOCUMENT_STORE_GUARD: refusing to persist a duplicated document"');
  });
});
