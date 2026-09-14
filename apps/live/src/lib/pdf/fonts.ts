/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Font families used by the PDF exporter, and the fallback chains built from
 * them.
 *
 * React-pdf resolves a `fontFamily` array per code point and picks the first
 * family that has a glyph for it, so latin text keeps Inter/Courier while other
 * scripts fall through to a Noto subset. Every family named here is registered
 * in plane-pdf-exporter.tsx, which is also where the files come from.
 */
export const LATIN_FONT_FAMILY = "Inter";
export const MONO_FONT_FAMILY = "Courier";

/** Extra subsets that ship inside the @fontsource/inter package. */
export const LATIN_EXT_FONT_FAMILY = "Inter Latin Ext";
export const VIETNAMESE_FONT_FAMILY = "Inter Vietnamese";

/**
 * Vendored Noto Sans subsets (assets/fonts, SIL OFL 1.1), in fallback order.
 *
 * Noto Sans SC already carries the common Chinese set plus the full kana, so it
 * also serves Japanese kana and the ideographs the locales share; TC and JP add
 * the ideographs only their locale uses, and KR adds Hangul. Order matters for
 * Han unification: the first family holding a code point wins, so an ideograph
 * shared across locales renders with its Simplified form.
 */
export const NOTO_FONT_SUBSETS = [
  { family: "Noto Sans SC", dir: "noto-sans-sc", regular: "NotoSansSC-Regular.ttf", bold: "NotoSansSC-Bold.ttf" },
  { family: "Noto Sans TC", dir: "noto-sans-tc", regular: "NotoSansTC-Regular.ttf", bold: "NotoSansTC-Bold.ttf" },
  { family: "Noto Sans JP", dir: "noto-sans-jp", regular: "NotoSansJP-Regular.ttf", bold: "NotoSansJP-Bold.ttf" },
  { family: "Noto Sans KR", dir: "noto-sans-kr", regular: "NotoSansKR-Regular.ttf", bold: "NotoSansKR-Bold.ttf" },
  {
    family: "Noto Sans Thai",
    dir: "noto-sans-thai",
    regular: "NotoSansThai-Regular.ttf",
    bold: "NotoSansThai-Bold.ttf",
  },
];

export const NOTO_FONT_FAMILIES: string[] = NOTO_FONT_SUBSETS.map((subset) => subset.family);

/** Everything a latin/mono family falls back to. */
const FALLBACK_FONT_FAMILIES: string[] = [LATIN_EXT_FONT_FAMILY, VIETNAMESE_FONT_FAMILY, ...NOTO_FONT_FAMILIES];

export const TEXT_FONT_FAMILIES: string[] = [LATIN_FONT_FAMILY, ...FALLBACK_FONT_FAMILIES];
export const CODE_FONT_FAMILIES: string[] = [MONO_FONT_FAMILY, ...FALLBACK_FONT_FAMILIES];

/**
 * The same chains, but with the subset order a particular document resolved to
 * (see `resolvePdfFontFamilies` in @plane/utils). The latin/mono family and the
 * extra Inter subsets stay ahead; only the Noto order changes.
 */
export const textFontFamiliesFor = (notoFamilies: string[]): string[] => [
  LATIN_FONT_FAMILY,
  LATIN_EXT_FONT_FAMILY,
  VIETNAMESE_FONT_FAMILY,
  ...notoFamilies,
];

export const codeFontFamiliesFor = (notoFamilies: string[]): string[] => [
  MONO_FONT_FAMILY,
  LATIN_EXT_FONT_FAMILY,
  VIETNAMESE_FONT_FAMILY,
  ...notoFamilies,
];
