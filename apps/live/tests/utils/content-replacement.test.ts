/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, expect, it, vi, afterEach } from "vitest";
// local imports
import {
  CONTENT_REPLACEMENT_TTL_MS,
  CONTENT_SHRINK_TOLERANCE,
  clearContentReplacement,
  contentTextLength,
  contentReplacementCount,
  getContentReplacementFloor,
  looksLikeDuplicatedGrowth,
  recordContentReplacement,
  recordDocumentContent,
  rememberedDocumentCount,
} from "@/utils/content-replacement";

const DOC = "a-page-id";

afterEach(() => {
  clearContentReplacement(DOC);
  vi.useRealTimers();
});

describe("contentTextLength", () => {
  it("counts visible text only", () => {
    expect(contentTextLength('<p class="editor-paragraph-block">\n  hello\n</p>')).toBe(5);
    expect(contentTextLength("<p>hello</p>")).toBe(5);
  });

  it("is zero for empty content", () => {
    expect(contentTextLength("")).toBe(0);
  });
});

describe("content replacement guard", () => {
  it("arms a floor scaled by the tolerance", () => {
    recordContentReplacement(DOC, 1000);

    expect(getContentReplacementFloor(DOC)).toBe(Math.floor(1000 * CONTENT_SHRINK_TOLERANCE));
  });

  it("returns null when nothing was recorded", () => {
    expect(getContentReplacementFloor("never-written")).toBeNull();
  });

  it("expires, so a later edit is not blocked forever", () => {
    vi.useFakeTimers();
    recordContentReplacement(DOC, 1000);
    vi.advanceTimersByTime(CONTENT_REPLACEMENT_TTL_MS + 1);

    expect(getContentReplacementFloor(DOC)).toBeNull();
  });

  it("sweeps expired entries when arming the next one, so the map cannot grow", () => {
    vi.useFakeTimers();
    for (let i = 0; i < 5; i++) recordContentReplacement(`page-${i}`, 1000);
    vi.advanceTimersByTime(CONTENT_REPLACEMENT_TTL_MS + 1);
    recordContentReplacement(DOC, 1000);

    expect(contentReplacementCount()).toBe(1);
  });

  it("drops the guard once the replacement reached the document", () => {
    recordContentReplacement(DOC, 1000);
    clearContentReplacement(DOC);

    expect(getContentReplacementFloor(DOC)).toBeNull();
  });

  it("catches the reported incident shape", () => {
    // the CLI wrote 6867 characters of text; the stale client's store kept 3918
    recordContentReplacement(DOC, 6867);

    const floor = getContentReplacementFloor(DOC)!;
    expect(contentTextLength("<p>" + "x".repeat(3918) + "</p>")).toBeLessThan(floor);
    expect(contentTextLength("<p>" + "x".repeat(6867) + "</p>")).toBeGreaterThanOrEqual(floor);
  });
});


describe("duplicated growth guard", () => {
  // a body of long, distinct paragraphs: healthy content has unique lines
  const body = (marker: string, seed: string, count = 12) =>
    Array.from(
      { length: count },
      (_, i) => `<p>${marker}-${i}-${seed.repeat(Math.ceil(40 / seed.length))}</p>`
    ).join("");

  it("flags a body that holds the previous content twice", () => {
    const previous = body("第一段", "alpha");
    recordDocumentContent(DOC, previous);

    // a stale client merge keeps the union: the same body appended again
    const verdict = looksLikeDuplicatedGrowth(DOC, previous + previous);

    expect(verdict.duplicated).toBe(true);
    expect(verdict.uniqueRatio).toBeLessThanOrEqual(0.85);
    expect(verdict.ratio).toBeGreaterThanOrEqual(1.3);
  });

  it("keeps a genuine longer edit", () => {
    recordDocumentContent(DOC, body("第一段", "alpha"));

    const edited = body("改写后的第一段", "gamma") + body("新增段落", "delta");
    const verdict = looksLikeDuplicatedGrowth(DOC, edited);

    expect(verdict.duplicated).toBe(false);
    expect(verdict.uniqueRatio).toBeGreaterThan(0.9);
  });

  it("does not compare documents it never saw", () => {
    expect(looksLikeDuplicatedGrowth("unknown-doc", body("未知", "omega")).duplicated).toBe(false);
  });

  it("ignores bodies too short to compare", () => {
    recordDocumentContent(DOC, "<p>短</p>");
    expect(looksLikeDuplicatedGrowth(DOC, "<p>短</p><p>短</p>").duplicated).toBe(false);
  });

  it("sweeps expired entries", () => {
    vi.useFakeTimers();
    recordDocumentContent("stale-doc", body("陈旧", "sigma"));
    vi.advanceTimersByTime(24 * 60 * 60 * 1000 + 1);
    recordDocumentContent(DOC, body("新", "tau"));

    expect(rememberedDocumentCount()).toBe(1);
  });
});
