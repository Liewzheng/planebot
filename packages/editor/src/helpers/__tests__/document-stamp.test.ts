/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";
// helpers
import {
  documentStamp,
  readDocumentStamp,
  shouldResetLocalDocument,
  shouldWriteStamp,
  writeDocumentStamp,
} from "@/helpers/document-stamp";

describe("documentStamp", () => {
  it("is stable for the same body", () => {
    const body = "<p>正文内容</p>";

    expect(documentStamp(body)).toBe(documentStamp(body));
  });

  it("changes when the body changes", () => {
    expect(documentStamp("<p>第一版</p>")).not.toBe(documentStamp("<p>第二版</p>"));
  });

  it("is undefined without a body", () => {
    expect(documentStamp(undefined)).toBeUndefined();
    expect(documentStamp(null)).toBeUndefined();
  });
});

describe("shouldResetLocalDocument", () => {
  it("keeps the cache when it descends from the served revision", () => {
    const stamp = documentStamp("<p>正文内容</p>");

    expect(shouldResetLocalDocument(stamp!, stamp)).toBe(false);
  });

  it("resets the cache when the served revision moved on", () => {
    const cached = documentStamp("<p>旧版本</p>")!;
    const served = documentStamp("<p>新版本</p>")!;

    expect(shouldResetLocalDocument(cached, served)).toBe(true);
  });

  it("resets a cache that predates the stamp", () => {
    // a cache written before this guard existed cannot be trusted
    expect(shouldResetLocalDocument(null, documentStamp("<p>正文</p>"))).toBe(true);
  });

  it("does not reset when the served revision is not known yet (pages-list mount)", () => {
    // the page details may still be loading: a guessed wipe is what causes
    // the multi-second "正在同步..." on long pages — wait for the served value
    // and re-decide when it arrives.
    expect(shouldResetLocalDocument(documentStamp("<p>正文</p>") ?? null, undefined)).toBe(false);
    expect(shouldResetLocalDocument(null, undefined)).toBe(false);
  });
});

describe("shouldWriteStamp", () => {
  it("writes on a first visit (no stored stamp)", () => {
    expect(shouldWriteStamp(null, documentStamp("<p>正文</p>"))).toBe(true);
  });

  it("writes when the stored stamp already matches (idempotent)", () => {
    const stamp = documentStamp("<p>正文</p>")!;
    expect(shouldWriteStamp(stamp, stamp)).toBe(true);
  });

  it("does not write when the stored stamp differs (preserve the PLANE-76 reset)", () => {
    expect(shouldWriteStamp(documentStamp("<p>旧版本</p>") ?? null, documentStamp("<p>新版本</p>"))).toBe(false);
  });

  it("does not write when the served value is unknown", () => {
    expect(shouldWriteStamp(null, undefined)).toBe(false);
    expect(shouldWriteStamp(documentStamp("<p>正文</p>") ?? null, undefined)).toBe(false);
  });
});

describe("readDocumentStamp / writeDocumentStamp", () => {
  afterEach(() => {
    window.localStorage.clear();
  });

  it("round-trips a stamp for a page", () => {
    const pageId = "11111111-0000-4000-8000-000000000001";
    const stamp = documentStamp("<p>正文内容</p>")!;

    expect(readDocumentStamp(pageId)).toBeNull();
    writeDocumentStamp(pageId, stamp);
    expect(readDocumentStamp(pageId)).toBe(stamp);
  });

  it("keeps pages apart", () => {
    const a = "11111111-0000-4000-8000-000000000002";
    const b = "11111111-0000-4000-8000-000000000003";

    writeDocumentStamp(a, documentStamp("<p>正文 A</p>")!);
    expect(readDocumentStamp(b)).toBeNull();
  });

  it("ignores an undefined stamp on write", () => {
    const pageId = "11111111-0000-4000-8000-000000000004";

    writeDocumentStamp(pageId, undefined);
    expect(readDocumentStamp(pageId)).toBeNull();
  });
});

describe("read-only visit stamps the cache (PLANE-76 regression)", () => {
  // The reported symptom: opening a page without saving left the cache with
  // `stored === null` on every visit, so `shouldResetLocalDocument` forced a
  // full rebuild before binding. The hook now writes the stamp when the
  // session reaches the synced state; these tests pin the round-trip the
  // hook relies on so a regression cannot silently undo it.

  afterEach(() => {
    window.localStorage.clear();
  });

  it("a second open with the same served contentStamp does not request a cache reset", () => {
    const pageId = "11111111-0000-4000-8000-000000000010";
    const body = "<p>一篇长文章 …</p>";
    const stamp = documentStamp(body)!;

    // First open: the page loads, reaches the synced state, and stamps the cache.
    writeDocumentStamp(pageId, stamp);
    expect(shouldResetLocalDocument(readDocumentStamp(pageId), stamp)).toBe(false);
  });

  it("a genuinely changed served contentStamp still requests a cache reset", () => {
    const pageId = "11111111-0000-4000-8000-000000000011";
    const previousBody = "<p>旧版本</p>";
    const nextBody = "<p>新版本</p>";

    // First open: the page loads and stamps the cache with the old body.
    writeDocumentStamp(pageId, documentStamp(previousBody)!);
    // Another writer saves: the served body moves on.
    expect(shouldResetLocalDocument(readDocumentStamp(pageId), documentStamp(nextBody))).toBe(true);
  });

  it("a missing stamp still resets the cache on the first visit", () => {
    // The first visit on a device that has never seen this page has no stamp to
    // prove the cache belongs to the served revision: refuse the cache.
    const stamp = documentStamp("<p>正文</p>")!;
    expect(shouldResetLocalDocument(readDocumentStamp("11111111-0000-4000-8000-000000000012"), stamp)).toBe(true);
  });
});
