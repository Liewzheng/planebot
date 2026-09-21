/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { PageProps } from "@react-pdf/renderer";
import { Document, Font, Page } from "@react-pdf/renderer";
import { Html } from "react-pdf-html";
// assets — the symbol-capable fallback (① → ≥ …) that Open Sans lacks
import interBold from "@/app/assets/fonts/inter/bold.ttf?url";
import interRegular from "@/app/assets/fonts/inter/regular.ttf?url";
// plane imports
import { pdfWordBreakParts, resolvePdfFontFamilies } from "@plane/utils";
// local imports
import {
  NOTO_FONT_SUBSETS,
  OPEN_SANS_FONT_SOURCES,
  codeBoldFontFamiliesFor,
  codeFontFamiliesFor,
  textFontFamiliesFor,
} from "./fonts";
import { EDITOR_PDF_DOCUMENT_STYLESHEET, EDITOR_PDF_INLINE_CODE_STYLE } from "./stylesheet";

/** Plain text of the exported HTML, used to detect which script the page is in. */
const htmlToText = (html: string): string => html.replace(/<[^>]*>/g, " ");

Font.register({
  // the GitHub theme's latin body; only two weights ship, so the intermediate
  // ones alias them (a stylesheet asking for them resolves instead of throwing)
  family: "Open Sans",
  fonts: [
    { src: OPEN_SANS_FONT_SOURCES.regular, fontWeight: "thin" },
    { src: OPEN_SANS_FONT_SOURCES.regular, fontWeight: "ultralight" },
    { src: OPEN_SANS_FONT_SOURCES.regular, fontWeight: "light" },
    { src: OPEN_SANS_FONT_SOURCES.regular, fontWeight: "normal" },
    { src: OPEN_SANS_FONT_SOURCES.italic, fontWeight: "normal", fontStyle: "italic" },
    { src: OPEN_SANS_FONT_SOURCES.regular, fontWeight: "medium" },
    { src: OPEN_SANS_FONT_SOURCES.bold, fontWeight: "semibold" },
    { src: OPEN_SANS_FONT_SOURCES.bold, fontWeight: "bold" },
    { src: OPEN_SANS_FONT_SOURCES.boldItalic, fontWeight: "bold", fontStyle: "italic" },
    { src: OPEN_SANS_FONT_SOURCES.bold, fontWeight: "ultrabold" },
    { src: OPEN_SANS_FONT_SOURCES.bold, fontWeight: "heavy" },
  ],
});

// symbols Open Sans does not carry (circled digits, →, ≥, …) resolve to Inter
Font.register({
  family: "Inter",
  fonts: [
    { src: interRegular, fontWeight: "normal" },
    { src: interRegular, fontWeight: "normal", fontStyle: "italic" },
    { src: interBold, fontWeight: "bold" },
    { src: interBold, fontWeight: "bold", fontStyle: "italic" },
  ],
});

// Break long unspaced runs (CJK sentences, URLs, code tokens) instead of
// letting them overflow the page — see pdfWordBreakParts in @plane/utils.
Font.registerHyphenationCallback(pdfWordBreakParts);

/**
 * Register each vendored subset as a fallback family: one source per weight, with
 * the italic slots aliasing the upright file. React-pdf resolves a family per
 * (weight, style) pair and throws when a requested style has no source at all,
 * and these families only ever serve non-latin code points, which are drawn
 * upright regardless.
 */
for (const subset of NOTO_FONT_SUBSETS) {
  Font.register({
    family: subset.family,
    fonts: [
      { src: subset.regular, fontWeight: "normal" },
      { src: subset.regular, fontWeight: "normal", fontStyle: "italic" },
      { src: subset.bold, fontWeight: "semibold" },
      { src: subset.bold, fontWeight: "semibold", fontStyle: "italic" },
      { src: subset.bold, fontWeight: "bold" },
      { src: subset.bold, fontWeight: "bold", fontStyle: "italic" },
    ],
  });
}

type Props = {
  content: string;
  pageFormat: PageProps["size"];
};

export function PDFDocument(props: Props) {
  const { content, pageFormat } = props;

  // Han unification: the subset that comes first decides the glyph form for
  // ideographs the locales share, so order the chain by the document's language.
  const notoFamilies = resolvePdfFontFamilies(htmlToText(content));
  // applyStylesheets resolves same-element rules in reverse object order, so
  // the mono override for inline <code> must precede the universal text-family
  // rule or the latter would win and code would render in the body face.
  const stylesheet = {
    code: {
      ...EDITOR_PDF_INLINE_CODE_STYLE,
      fontFamily: codeFontFamiliesFor(notoFamilies),
    },
    ...EDITOR_PDF_DOCUMENT_STYLESHEET,
    "*:not(.courier, .courier-bold)": { fontFamily: textFontFamiliesFor(notoFamilies) },
    ".courier": { fontFamily: codeFontFamiliesFor(notoFamilies) },
    ".courier-bold": { fontFamily: codeBoldFontFamiliesFor(notoFamilies) },
  };

  return (
    <Document>
      <Page
        size={pageFormat}
        style={{
          backgroundColor: "#ffffff",
          padding: 64,
        }}
      >
        <Html stylesheet={stylesheet}>{content}</Html>
      </Page>
    </Document>
  );
}
