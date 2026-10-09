/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, expect, it } from "vitest";
// helpers
import { documentStamp, shouldResetLocalDocument } from "@/helpers/document-stamp";

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

  it("resets when the served revision is not known yet", () => {
    // the page details may still be loading: do not merge an unverifiable copy
    expect(shouldResetLocalDocument(documentStamp("<p>正文</p>") ?? null, undefined)).toBe(true);
    expect(shouldResetLocalDocument(null, undefined)).toBe(true);
  });
});
