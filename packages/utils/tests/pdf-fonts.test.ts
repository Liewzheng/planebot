/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, it, expect } from "vitest";
import { PDF_FALLBACK_FONT_FAMILIES, detectPdfFontLocale, resolvePdfFontFamilies } from "../src/pdf-fonts";

describe("detectPdfFontLocale", () => {
  it("detects Japanese from kana", () => {
    expect(detectPdfFontLocale("日本語のテスト")).toBe("ja");
    expect(detectPdfFontLocale("カタカナ")).toBe("ja");
  });

  it("detects Korean from Hangul", () => {
    expect(detectPdfFontLocale("안녕하세요")).toBe("ko");
  });

  it("detects Traditional Chinese from orthography markers", () => {
    expect(detectPdfFontLocale("繁體中文與軟體資訊")).toBe("zh-Hant");
  });

  it("detects Simplified Chinese", () => {
    expect(detectPdfFontLocale("简体中文与软件信息")).toBe("zh-Hans");
  });

  it("prefers kana over Hangul when both appear", () => {
    // 日本語 is kanji-only, so the string needs real kana to be marked Japanese
    expect(detectPdfFontLocale("日本語のテスト 안녕")).toBe("ja");
  });

  it("falls back to Simplified Chinese for latin-only text", () => {
    expect(detectPdfFontLocale("Hello World")).toBe("zh-Hans");
    expect(detectPdfFontLocale("")).toBe("zh-Hans");
  });
});

describe("resolvePdfFontFamilies", () => {
  it("puts the document's own locale first and keeps every subset as fallback", () => {
    const families = resolvePdfFontFamilies("日本語のテスト");

    expect(families[0]).toBe("Noto Sans JP");
    expect(families).toHaveLength(PDF_FALLBACK_FONT_FAMILIES.length);
    expect(new Set(families)).toEqual(new Set(PDF_FALLBACK_FONT_FAMILIES));
  });

  it("keeps Traditional Chinese first for a Traditional document", () => {
    expect(resolvePdfFontFamilies("繁體中文與軟體資訊")[0]).toBe("Noto Sans TC");
  });

  it("keeps Korean first for a Korean document", () => {
    expect(resolvePdfFontFamilies("안녕하세요")[0]).toBe("Noto Sans KR");
  });

  it("leaves the default order untouched for Simplified Chinese", () => {
    expect(resolvePdfFontFamilies("简体中文")).toEqual(PDF_FALLBACK_FONT_FAMILIES);
  });
});
