/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { createRequire } from "module";
import path from "path";
import { Document, Font, Page, pdf, Text } from "@react-pdf/renderer";
import { createKeyGenerator, renderNode } from "./node-renderers";
import { CJK_FONT_FAMILY, LATIN_FONT_FAMILY, pdfStyles } from "./styles";
import type { PDFExportOptions, TipTapDocument } from "./types";

// Use createRequire for ESM compatibility to resolve font file paths
const require = createRequire(import.meta.url);

// Resolve local font file paths from @fontsource/inter package
const interFontDir = path.dirname(require.resolve("@fontsource/inter/package.json"));

// The vendored CJK subset lives in the package's `assets/`, so resolve it from
// the package root — the same path works from src/ under vitest and from the
// bundled dist/ in the container.
const livePackageRoot = path.dirname(require.resolve("live/package.json"));
const cjkFontDir = path.join(livePackageRoot, "assets/fonts/noto-sans-sc");

Font.register({
  family: LATIN_FONT_FAMILY,
  fonts: [
    {
      src: path.join(interFontDir, "files/inter-latin-400-normal.woff"),
      fontWeight: 400,
    },
    {
      src: path.join(interFontDir, "files/inter-latin-400-italic.woff"),
      fontWeight: 400,
      fontStyle: "italic",
    },
    {
      src: path.join(interFontDir, "files/inter-latin-600-normal.woff"),
      fontWeight: 600,
    },
    {
      src: path.join(interFontDir, "files/inter-latin-600-italic.woff"),
      fontWeight: 600,
      fontStyle: "italic",
    },
    {
      src: path.join(interFontDir, "files/inter-latin-700-normal.woff"),
      fontWeight: 700,
    },
    {
      src: path.join(interFontDir, "files/inter-latin-700-italic.woff"),
      fontWeight: 700,
      fontStyle: "italic",
    },
  ],
});

// Simplified-Chinese subset of Noto Sans SC (SIL OFL 1.1 — see
// assets/fonts/noto-sans-sc/LICENSE), registered as the fallback in styles.ts
// so CJK code points have a glyph to draw.
//
// The subset ships Regular and Bold only, so weight 600 reuses Bold (the PDF
// styles ask for 400/600/700) and the italic slots alias the upright files:
// react-pdf resolves a family per (weight, style) pair and throws when a
// requested style has no source at all. Only CJK code points ever reach this
// family, and there the slant would be cosmetic anyway.
//
// Keep these files as `.woff`. pdfkit embeds a subset of whatever is registered,
// and fontkit's subsetter only prunes WOFF properly — a WOFF2 source silently
// produced multi-megabyte subsets.
const cjkRegular = path.join(cjkFontDir, "NotoSansSC-Regular.woff");
const cjkBold = path.join(cjkFontDir, "NotoSansSC-Bold.woff");

Font.register({
  family: CJK_FONT_FAMILY,
  fonts: [
    { src: cjkRegular, fontWeight: 400 },
    { src: cjkRegular, fontWeight: 400, fontStyle: "italic" },
    { src: cjkBold, fontWeight: 600 },
    { src: cjkBold, fontWeight: 600, fontStyle: "italic" },
    { src: cjkBold, fontWeight: 700 },
    { src: cjkBold, fontWeight: 700, fontStyle: "italic" },
  ],
});

export const createPdfDocument = (doc: TipTapDocument, options: PDFExportOptions = {}) => {
  const { title, author, subject, pageSize = "A4", pageOrientation = "portrait", metadata, noAssets } = options;

  // Merge noAssets into metadata for use in node renderers
  const mergedMetadata = { ...metadata, noAssets };

  const content = doc.content || [];
  const getKey = createKeyGenerator();
  const renderedContent = content.map((node, index) => renderNode(node, "doc", index, mergedMetadata, getKey));

  return (
    <Document title={title} author={author} subject={subject}>
      <Page size={pageSize} orientation={pageOrientation} style={pdfStyles.page}>
        {title && <Text style={pdfStyles.title}>{title}</Text>}
        {renderedContent}
      </Page>
    </Document>
  );
};

export const renderPlaneDocToPdfBuffer = async (
  doc: TipTapDocument,
  options: PDFExportOptions = {}
): Promise<Buffer> => {
  const pdfDocument = createPdfDocument(doc, options);
  const pdfInstance = pdf(pdfDocument);
  const blob = await pdfInstance.toBlob();
  const arrayBuffer = await blob.arrayBuffer();
  return Buffer.from(arrayBuffer);
};

export const renderPlaneDocToPdfBlob = async (doc: TipTapDocument, options: PDFExportOptions = {}): Promise<Blob> => {
  const pdfDocument = createPdfDocument(doc, options);
  const pdfInstance = pdf(pdfDocument);
  return await pdfInstance.toBlob();
};
