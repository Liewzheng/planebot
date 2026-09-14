/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Which vendored Noto Sans subset should win for a document's ideographs.
 *
 * The PDF exporters register the subsets under one fallback chain, and react-pdf
 * picks, per code point, the first family that has a glyph. The CJK locales share
 * most of their ideographs, so that first hit also decides which glyph *form* is
 * drawn — the Han unification problem. Ordering the document's own locale first
 * gives Japanese text Japanese forms, Traditional Chinese its own, and so on,
 * while the remaining subsets stay behind as coverage for anything else.
 */

/** Vendored subsets, in the default (Simplified-Chinese-first) order. */
export const PDF_FALLBACK_FONT_FAMILIES: string[] = [
  "Noto Sans SC",
  "Noto Sans TC",
  "Noto Sans JP",
  "Noto Sans KR",
  "Noto Sans Thai",
];

export type TPdfFontLocale = "zh-Hans" | "zh-Hant" | "ja" | "ko";

/** The family that carries each locale's own glyph forms. */
const LOCALE_FONT_FAMILY: Record<TPdfFontLocale, string> = {
  "zh-Hans": "Noto Sans SC",
  "zh-Hant": "Noto Sans TC",
  ja: "Noto Sans JP",
  ko: "Noto Sans KR",
};

// Hangul syllables and jamo; hiragana, katakana and the katakana phonetic
// extensions. Kana and Hangul are unambiguous script markers.
const HANGUL_PATTERN = /[\u1100-\u11ff\u3130-\u318f\ua960-\ua97f\uac00-\ud7a3]/;
const KANA_PATTERN = /[\u3040-\u309f\u30a0-\u30ff\u31f0-\u31ff]/;

/**
 * High-frequency characters that exist in only one of the two Chinese
 * orthographies. Detection compares how many of each appear; the lists do not
 * need to be exhaustive, only frequent enough to outvote each other in real text.
 */
const TRADITIONAL_ONLY_CHARACTERS =
  "們這說對後學國龍灣體數據語導出與為個業務實義會點總東車馬鳥魚長門問題時間開關發現產圖書館網頁應選擇設計檔資訊軟電腦傳輸專團隊員試驗證環邊蓋";
const SIMPLIFIED_ONLY_CHARACTERS =
  "们这说对后学国龙湾体数据语导出与为个业务实义会点总东车马鸟鱼长门问题时间开关发现产图书馆网页应选择设计档资讯软电脑传输专团队员试验证环边盖";

const TRADITIONAL_ONLY = new Set(TRADITIONAL_ONLY_CHARACTERS);
const SIMPLIFIED_ONLY = new Set(SIMPLIFIED_ONLY_CHARACTERS);

/**
 * Guess which locale's glyph forms a document wants.
 *
 * Kana wins over Hangul wins over the Chinese heuristics, since Japanese and
 * Korean text is marked by its own script while the Chinese variants can only be
 * told apart by tallying orthography-specific characters. Latin-only text falls
 * through to Simplified Chinese, which is also the default order.
 */
export const detectPdfFontLocale = (text: string): TPdfFontLocale => {
  if (!text) return "zh-Hans";

  let kana = 0;
  let hangul = 0;
  let traditional = 0;
  let simplified = 0;

  for (const character of text) {
    if (KANA_PATTERN.test(character)) kana += 1;
    else if (HANGUL_PATTERN.test(character)) hangul += 1;

    if (TRADITIONAL_ONLY.has(character)) traditional += 1;
    else if (SIMPLIFIED_ONLY.has(character)) simplified += 1;
  }

  if (kana > 0) return "ja";
  if (hangul > 0) return "ko";
  if (traditional > simplified) return "zh-Hant";
  return "zh-Hans";
};

/**
 * The fallback chain for a document, with its own locale's subset first.
 *
 * `en` is deliberately absent: the latin family is registered separately and
 * sits ahead of this chain in both exporters.
 */
export const resolvePdfFontFamilies = (text: string): string[] => {
  const preferred = LOCALE_FONT_FAMILY[detectPdfFontLocale(text)];
  return [preferred, ...PDF_FALLBACK_FONT_FAMILIES.filter((family) => family !== preferred)];
};
