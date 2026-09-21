/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// assets — vendored Noto Sans subsets (SIL OFL 1.1, license alongside). These are
// fetched by the browser when a PDF is generated, not on page load.
import notoSansJPBold from "@/app/assets/fonts/noto/NotoSansJP-Bold.ttf?url";
import notoSansJPRegular from "@/app/assets/fonts/noto/NotoSansJP-Regular.ttf?url";
import notoSansKRBold from "@/app/assets/fonts/noto/NotoSansKR-Bold.ttf?url";
import notoSansKRRegular from "@/app/assets/fonts/noto/NotoSansKR-Regular.ttf?url";
import notoSansSCBold from "@/app/assets/fonts/noto/NotoSansSC-Bold.ttf?url";
import notoSansSCRegular from "@/app/assets/fonts/noto/NotoSansSC-Regular.ttf?url";
import notoSansTCBold from "@/app/assets/fonts/noto/NotoSansTC-Bold.ttf?url";
import notoSansTCRegular from "@/app/assets/fonts/noto/NotoSansTC-Regular.ttf?url";
import notoSansThaiBold from "@/app/assets/fonts/noto/NotoSansThai-Bold.ttf?url";
import notoSansThaiRegular from "@/app/assets/fonts/noto/NotoSansThai-Regular.ttf?url";
// assets — the latin body of the GitHub print theme (Open Sans; Apache-2.0,
// license alongside). Converted from the theme's WOFF2 to TTF: fontkit decodes
// compressed fonts per glyph, and an uncompressed file keeps export fast.
import openSansRegular from "@/app/assets/fonts/open-sans/open-sans-regular.ttf?url";
import openSansItalic from "@/app/assets/fonts/open-sans/open-sans-italic.ttf?url";
import openSansBold from "@/app/assets/fonts/open-sans/open-sans-700.ttf?url";
import openSansBoldItalic from "@/app/assets/fonts/open-sans/open-sans-700italic.ttf?url";

/** The Open Sans files as react-pdf's Font.register wants them. */
export const OPEN_SANS_FONT_SOURCES = {
  regular: openSansRegular,
  italic: openSansItalic,
  bold: openSansBold,
  boldItalic: openSansBoldItalic,
};

/**
 * Font families used by the in-browser PDF export, and the fallback chains built
 * from them.
 *
 * The vendored Open Sans carries the latin body text of the GitHub theme;
 * symbols it lacks (circled digits, →, ≥, …) fall to Inter, and CJK/kana/Hangul/
 * Thai fall through to the matching Noto subset. React-pdf resolves a
 * `fontFamily` array per code point and picks the first family that has a
 * glyph, which is why these are arrays rather than a single family.
 *
 * Kept in step with the server-side exporter (apps/live/src/lib/pdf/fonts.ts).
 */
export const PDF_FONT_FAMILY = "Open Sans";
export const PDF_SYMBOL_FONT_FAMILY = "Inter";
export const PDF_MONO_FONT_FAMILY = "Courier";
export const PDF_MONO_BOLD_FONT_FAMILY = "Courier-Bold";

/**
 * Vendored Noto Sans subsets, in fallback order.
 *
 * Noto Sans SC carries the common Chinese set plus the full kana, so it also
 * serves Japanese kana and the ideographs the locales share; TC and JP add the
 * ideographs only their locale uses, and KR adds Hangul. Order matters for Han
 * unification: the first family holding a code point wins, so an ideograph
 * shared across locales renders with its Simplified form.
 */
export const NOTO_FONT_SUBSETS = [
  { family: "Noto Sans SC", regular: notoSansSCRegular, bold: notoSansSCBold },
  { family: "Noto Sans TC", regular: notoSansTCRegular, bold: notoSansTCBold },
  { family: "Noto Sans JP", regular: notoSansJPRegular, bold: notoSansJPBold },
  { family: "Noto Sans KR", regular: notoSansKRRegular, bold: notoSansKRBold },
  { family: "Noto Sans Thai", regular: notoSansThaiRegular, bold: notoSansThaiBold },
];

export const NOTO_FONT_FAMILIES: string[] = NOTO_FONT_SUBSETS.map((subset) => subset.family);

export const PDF_TEXT_FONT_FAMILIES: string[] = [PDF_FONT_FAMILY, PDF_SYMBOL_FONT_FAMILY, ...NOTO_FONT_FAMILIES];
export const PDF_CODE_FONT_FAMILIES: string[] = [PDF_MONO_FONT_FAMILY, PDF_SYMBOL_FONT_FAMILY, ...NOTO_FONT_FAMILIES];
export const PDF_CODE_BOLD_FONT_FAMILIES: string[] = [
  PDF_MONO_BOLD_FONT_FAMILY,
  PDF_SYMBOL_FONT_FAMILY,
  ...NOTO_FONT_FAMILIES,
];

/**
 * The same chains, but with the subset order a particular document resolved to
 * (see `resolvePdfFontFamilies` in @plane/utils). Only the Noto order changes.
 */
export const textFontFamiliesFor = (notoFamilies: string[]): string[] => [
  PDF_FONT_FAMILY,
  PDF_SYMBOL_FONT_FAMILY,
  ...notoFamilies,
];

export const codeFontFamiliesFor = (notoFamilies: string[]): string[] => [
  PDF_MONO_FONT_FAMILY,
  PDF_SYMBOL_FONT_FAMILY,
  ...notoFamilies,
];

export const codeBoldFontFamiliesFor = (notoFamilies: string[]): string[] => [
  PDF_MONO_BOLD_FONT_FAMILY,
  PDF_SYMBOL_FONT_FAMILY,
  ...notoFamilies,
];
