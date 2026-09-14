/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// assets — vendored Noto Sans subsets (SIL OFL 1.1, license alongside). These are
// fetched by the browser when a PDF is generated, not on page load.
import notoSansJPBold from "@/app/assets/fonts/noto/NotoSansJP-Bold.woff?url";
import notoSansJPRegular from "@/app/assets/fonts/noto/NotoSansJP-Regular.woff?url";
import notoSansKRBold from "@/app/assets/fonts/noto/NotoSansKR-Bold.woff?url";
import notoSansKRRegular from "@/app/assets/fonts/noto/NotoSansKR-Regular.woff?url";
import notoSansSCBold from "@/app/assets/fonts/noto/NotoSansSC-Bold.woff?url";
import notoSansSCRegular from "@/app/assets/fonts/noto/NotoSansSC-Regular.woff?url";
import notoSansTCBold from "@/app/assets/fonts/noto/NotoSansTC-Bold.woff?url";
import notoSansTCRegular from "@/app/assets/fonts/noto/NotoSansTC-Regular.woff?url";
import notoSansThaiBold from "@/app/assets/fonts/noto/NotoSansThai-Bold.woff?url";
import notoSansThaiRegular from "@/app/assets/fonts/noto/NotoSansThai-Regular.woff?url";

/**
 * Font families used by the in-browser PDF export, and the fallback chains built
 * from them.
 *
 * The vendored Inter TTF already carries the latin and Vietnamese glyphs, so it
 * needs no extra latin-ext fallback; everything else (CJK, kana, Hangul, Thai)
 * falls through to the matching Noto subset. React-pdf resolves a `fontFamily`
 * array per code point and picks the first family that has a glyph, which is why
 * these are arrays rather than a single family.
 *
 * Kept in step with the server-side exporter (apps/live/src/lib/pdf/fonts.ts).
 */
export const PDF_FONT_FAMILY = "Inter";
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

export const PDF_TEXT_FONT_FAMILIES: string[] = [PDF_FONT_FAMILY, ...NOTO_FONT_FAMILIES];
export const PDF_CODE_FONT_FAMILIES: string[] = [PDF_MONO_FONT_FAMILY, ...NOTO_FONT_FAMILIES];
export const PDF_CODE_BOLD_FONT_FAMILIES: string[] = [PDF_MONO_BOLD_FONT_FAMILY, ...NOTO_FONT_FAMILIES];
