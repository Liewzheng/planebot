/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { createRequire } from "module";
import path from "path";
import { Document, Font, Page, pdf, Text } from "@react-pdf/renderer";
import { createKeyGenerator, renderNode } from "./node-renderers";
import { LATIN_EXT_FONT_FAMILY, LATIN_FONT_FAMILY, NOTO_FONT_SUBSETS, VIETNAMESE_FONT_FAMILY } from "./fonts";
import { pdfStyles } from "./styles";
import type { PDFExportOptions, TipTapDocument } from "./types";

// Use createRequire for ESM compatibility to resolve font file paths
const require = createRequire(import.meta.url);

// Resolve local font file paths from @fontsource/inter package
const interFontDir = path.dirname(require.resolve("@fontsource/inter/package.json"));

// The vendored subsets live in the package's `assets/`, so resolve them from the
// package root — the same path works from src/ under vitest and from the bundled
// dist/ in the container.
const livePackageRoot = path.dirname(require.resolve("live/package.json"));
const fontAssetDir = path.join(livePackageRoot, "assets/fonts");

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

/**
 * Register a fallback family: one source per weight, with the italic slots
 * aliasing the upright file. React-pdf resolves a family per (weight, style)
 * pair and throws when a requested style has no source at all, and these
 * families only ever serve non-latin code points, which are drawn upright
 * regardless.
 */
const registerFallbackFamily = (family: string, sources: Array<[number, string]>) => {
  Font.register({
    family,
    fonts: sources.flatMap(([fontWeight, src]) => [
      { src, fontWeight },
      { src, fontWeight, fontStyle: "italic" },
    ]),
  });
};

// Latin extended + Vietnamese, straight from the @fontsource/inter package:
// `latin-ext` carries the extra letters (ơ ư đ …) and `vietnamese` the tone
// marks, and neither is complete on its own.
registerFallbackFamily(LATIN_EXT_FONT_FAMILY, [
  [400, path.join(interFontDir, "files/inter-latin-ext-400-normal.woff")],
  [600, path.join(interFontDir, "files/inter-latin-ext-600-normal.woff")],
  [700, path.join(interFontDir, "files/inter-latin-ext-700-normal.woff")],
]);
registerFallbackFamily(VIETNAMESE_FONT_FAMILY, [
  [400, path.join(interFontDir, "files/inter-vietnamese-400-normal.woff")],
  [600, path.join(interFontDir, "files/inter-vietnamese-600-normal.woff")],
  [700, path.join(interFontDir, "files/inter-vietnamese-700-normal.woff")],
]);

// Vendored Noto Sans subsets (SIL OFL 1.1 — see assets/fonts/LICENSE-NotoSans-OFL.txt).
//
// Keep these files as `.woff`. pdfkit embeds a subset of the registered font,
// and fontkit's subsetter only prunes WOFF properly — a WOFF2 source silently
// produced multi-megabyte subsets.
for (const subset of NOTO_FONT_SUBSETS) {
  const dir = path.join(fontAssetDir, subset.dir);
  registerFallbackFamily(subset.family, [
    [400, path.join(dir, subset.regular)],
    [700, path.join(dir, subset.bold)],
  ]);
}

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
