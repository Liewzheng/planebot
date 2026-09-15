/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { PDFParse } from "pdf-parse";
import { getDocument } from "pdfjs-dist/legacy/build/pdf.mjs";

export type PdfPageSize = { width: number; height: number };

export type OutOfBoundsText = {
  page: number;
  text: string;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  pageWidth: number;
  pageHeight: number;
};

export type PdfHealthOptions = {
  /** Every page is expected to have this size in PDF points (±0.5pt). */
  expectedPageSize?: PdfPageSize;
  /** Fragments that must appear in the extracted text (whitespace-insensitive). */
  mustContain?: string[];
};

export type PdfHealthReport = {
  pageCount: number;
  pageSizes: PdfPageSize[];
  text: string;
  outOfBounds: OutOfBoundsText[];
};

const TOLERANCE = 1; // pt; pdfkit rounds coordinates, so allow a small slack

const extractText = async (buffer: Buffer): Promise<string> => {
  const parser = new PDFParse(new Uint8Array(buffer));
  try {
    const result = await parser.getText();
    return result.pages.map((p) => p.text).join("\n");
  } finally {
    await parser.destroy();
  }
};

const extractGeometry = async (
  buffer: Buffer
): Promise<{ pageSizes: PdfPageSize[]; outOfBounds: OutOfBoundsText[] }> => {
  const doc = await getDocument({ data: new Uint8Array(buffer), isEvalSupported: false }).promise;
  try {
    const pageSizes: PdfPageSize[] = [];
    const outOfBounds: OutOfBoundsText[] = [];
    for (let pageNum = 1; pageNum <= doc.numPages; pageNum++) {
      const page = await doc.getPage(pageNum);
      const viewport = page.getViewport({ scale: 1 });
      pageSizes.push({ width: viewport.width, height: viewport.height });
      const content = await page.getTextContent();
      for (const item of content.items) {
        if (!("str" in item) || !item.str) continue;
        const x0 = item.transform[4];
        const y0 = viewport.height - item.transform[5] - item.height; // pdfjs y is bottom-up
        const x1 = x0 + item.width;
        const y1 = y0 + item.height;
        if (x0 < -TOLERANCE || y0 < -TOLERANCE || x1 > viewport.width + TOLERANCE || y1 > viewport.height + TOLERANCE) {
          outOfBounds.push({
            page: pageNum,
            text: item.str,
            x0,
            y0,
            x1,
            y1,
            pageWidth: viewport.width,
            pageHeight: viewport.height,
          });
        }
      }
    }
    return { pageSizes, outOfBounds };
  } finally {
    await doc.destroy();
  }
};

/**
 * Health-check a rendered PDF the way the export defects actually manifest:
 * text extracted (for tofu/content checks) and per-item geometry scanned (for
 * out-of-bounds checks) in one pass. Pure TypeScript — no external tooling —
 * so it can run in vitest on any machine.
 */
export const checkPdfHealth = async (buffer: Buffer, options: PdfHealthOptions = {}): Promise<PdfHealthReport> => {
  const [text, geometry] = await Promise.all([extractText(buffer), extractGeometry(buffer)]);
  const report: PdfHealthReport = {
    pageCount: geometry.pageSizes.length,
    pageSizes: geometry.pageSizes,
    text,
    outOfBounds: geometry.outOfBounds,
  };

  if (report.pageCount === 0) throw new Error("PDF has no pages");
  if (text.includes("\ufffd")) throw new Error("PDF contains U+FFFD replacement characters (missing glyphs)");
  for (const fragment of options.mustContain ?? []) {
    const normalize = (value: string) => value.replace(/\s+/g, "");
    if (!normalize(text).includes(normalize(fragment))) {
      throw new Error(`PDF text is missing expected fragment: ${fragment}`);
    }
  }
  if (options.expectedPageSize) {
    for (const size of report.pageSizes) {
      if (
        Math.abs(size.width - options.expectedPageSize.width) > 0.5 ||
        Math.abs(size.height - options.expectedPageSize.height) > 0.5
      ) {
        throw new Error(
          `Unexpected page size ${size.width}x${size.height}, expected ${options.expectedPageSize.width}x${options.expectedPageSize.height}`
        );
      }
    }
  }
  if (report.outOfBounds.length > 0) {
    const first = report.outOfBounds[0];
    throw new Error(
      `PDF has ${report.outOfBounds.length} out-of-bounds text item(s); first: page ${first.page} ` +
        `(${first.x0.toFixed(1)},${first.y0.toFixed(1)})-(${first.x1.toFixed(1)},${first.y1.toFixed(1)}) ` +
        `on a ${first.pageWidth.toFixed(1)}x${first.pageHeight.toFixed(1)} page: ${first.text.slice(0, 40)}`
    );
  }
  return report;
};
