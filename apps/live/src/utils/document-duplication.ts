/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Detection for the "page content duplicated" failure mode.
 *
 * When a client holds a stale copy of a document (IndexedDB cache or an old
 * tab that stayed open) and reconnects, Yjs takes the union of both states:
 * the whole document ends up in the Y.Doc two or more times. The document
 * then balloons on every load / re-upload.
 *
 * The signature is a very low ratio of unique long lines: real documents are
 * overwhelmingly made of unique text, while a union-duplicated document has
 * most of its lines repeated. Used as a last-resort guard before persisting.
 */

const MIN_LINES = 120;
const MIN_UNIQUE_RATIO = 0.6;

export type TDocumentDuplication = {
  duplicated: boolean;
  totalLines: number;
  uniqueLines: number;
};

export const detectDocumentDuplication = (descriptionHTML: string): TDocumentDuplication => {
  const lines = descriptionHTML
    .replace(/<[^>]+>/g, "\n")
    .split("\n")
    .map((line) => line.replace(/\s+/g, " ").trim())
    .filter((line) => line.length > 20);

  if (lines.length < MIN_LINES) {
    return { duplicated: false, totalLines: lines.length, uniqueLines: new Set(lines).size };
  }

  const uniqueLines = new Set(lines).size;
  return {
    duplicated: uniqueLines / lines.length < MIN_UNIQUE_RATIO,
    totalLines: lines.length,
    uniqueLines,
  };
};
