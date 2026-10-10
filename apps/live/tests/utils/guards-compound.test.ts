/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { afterEach, describe, expect, it } from "vitest";
// local imports — the guards that the store path now wires in
import {
  clearContentReplacement,
  contentTextLength,
  getContentReplacementFloor,
  looksLikeDuplicatedGrowth,
  recordContentReplacement,
  recordDocumentContent,
} from "@/utils/content-replacement";
import { duplicatedBlockLength } from "@/utils/document-balloon";
import { detectDocumentDuplication } from "@/utils/document-duplication";

/**
 * Behavioural test for the PLANE-76 guard chain.
 *
 * The store path in `extensions/database.ts` runs each guard in turn and
 * refuses the write as soon as one fires. This test exercises the same
 * guards in isolation, on the same inputs the store path would feed them,
 * so a regression that breaks the chain (wrong tolerance, wrong ratio,
 * wrong threshold) is caught here. Combined with `guards-reachability.test.ts`
 * (which proves the wiring is in place) and the per-file suites in
 * `content-replacement.test.ts` / `document-balloon.test.ts`, the full
 * "defence works and is wired" contract is covered.
 */
const DOC = "compound-guard-test";
const OTHER_DOC = "another-doc";

afterEach(() => {
  clearContentReplacement(DOC);
  clearContentReplacement(OTHER_DOC);
});

const paragraph = (text: string) => `<p>${text}</p>`;

const longBody = (start = 0): string =>
  Array.from(
    { length: 130 },
    (_, index) => `第${start + index}段内容，记录一次实测的数据与结论，详尽描述这一段的内容。`
  )
    .map(paragraph)
    .join("");

describe("store-path guard chain (compound)", () => {
  it("detectDocumentDuplication fires on a body whose long lines are mostly repeated", () => {
    // 80 unique lines per copy → 160 lines when concatenated, 80 unique → 0.5 ratio, below 0.6.
    const baseline = longBody();
    const duplicated = baseline + baseline;

    const verdict = detectDocumentDuplication(duplicated);
    expect(verdict.duplicated).toBe(true);
    expect(verdict.uniqueLines / verdict.totalLines).toBeLessThan(0.6);
  });

  it("duplicatedBlockLength fires on a body that holds a 600-char block twice", () => {
    const body = longBody();

    expect(duplicatedBlockLength(body)).toBe(0);
    expect(duplicatedBlockLength(body + body)).toBeGreaterThanOrEqual(600);
  });

  it("looksLikeDuplicatedGrowth fires when a stale-client merge grows the body by >=1.3x with low uniqueness", () => {
    recordDocumentContent(DOC, longBody());

    const merged = longBody() + longBody();
    const verdict = looksLikeDuplicatedGrowth(DOC, merged);

    expect(verdict.duplicated).toBe(true);
    expect(verdict.ratio).toBeGreaterThanOrEqual(1.3);
    expect(verdict.uniqueRatio).toBeLessThanOrEqual(0.85);
  });

  it("looksLikeDuplicatedGrowth does not fire on a clean longer edit", () => {
    recordDocumentContent(DOC, longBody());

    const edited = longBody(500);
    const verdict = looksLikeDuplicatedGrowth(DOC, edited);

    expect(verdict.duplicated).toBe(false);
    expect(verdict.ratio).toBeLessThan(1.3);
  });

  it("getContentReplacementFloor refuses a store that drops below the armed floor", () => {
    recordContentReplacement(DOC, 6867);

    const floor = getContentReplacementFloor(DOC);
    expect(floor).not.toBeNull();

    // The reported incident kept 3918 characters of the 6867-character
    // external write — below the 0.7 tolerance.
    const shrunk = contentTextLength(paragraph("x".repeat(3918)));
    expect(shrunk).toBeLessThan(floor!);

    // The accepted store path must clear the floor so subsequent edits
    // are not blocked — mirrors the database.ts store path.
    clearContentReplacement(DOC);
    expect(getContentReplacementFloor(DOC)).toBeNull();
  });

  it("guards do not bleed across documents", () => {
    recordContentReplacement(DOC, 6867);
    recordDocumentContent(DOC, longBody());

    // A second document must not see the floor or the recorded content.
    expect(getContentReplacementFloor(OTHER_DOC)).toBeNull();
    expect(looksLikeDuplicatedGrowth(OTHER_DOC, longBody()).duplicated).toBe(false);
  });
});
