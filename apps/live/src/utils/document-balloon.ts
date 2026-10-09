/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Detects a document that holds its own content twice.
 *
 * Yjs merges as a union, so a client whose local copy was ballooned (a stale
 * IndexedDB document merged back in) pushes the duplicate into the shared
 * document — and every client then renders and re-syncs it. The database is the
 * only authority that can break that loop, so the live server compares the
 * in-memory document against the stored one and reloads the clients when they
 * disagree (see the balloon guard in `extensions/database`).
 *
 * The check is measured on the content itself rather than on lines: the editor
 * emits an html body without line breaks, so a 20 KB document can be one block
 * and a handful of lines — the failure that let a ballooned page stay in the
 * database for days (PLANE-76). A body only trips it when it repeats a quarter
 * of itself verbatim, which no document does by accident.
 */

import { visibleText } from "./content-replacement";

/** Shortest repeated block that still means "the document is in here twice". */
export const DUPLICATED_BLOCK_MIN_LENGTH = 600;

const HASH_BASE = 1_000_003;

/**
 * Length of the longest block of body text the body repeats verbatim, or 0.
 *
 * `minLength` defaults to a quarter of the body (never below
 * `DUPLICATED_BLOCK_MIN_LENGTH`).
 */
export const duplicatedBlockLength = (html: string, minLength?: number): number => {
  const text = visibleText(html);
  const minimum = minLength ?? Math.max(DUPLICATED_BLOCK_MIN_LENGTH, Math.floor(text.length / 4));
  if (text.length < minimum * 2) return 0;

  // Rolling hash over a window the size of the smallest block we care about, so
  // every offset is compared (a fixed stride misses the second copy when it
  // starts between two sampled positions). The hash is 32-bit — a collision is
  // harmless because a match is confirmed on the text itself before it counts.
  const windowSize = Math.max(minimum, Math.floor(text.length / 8));
  let high = 1;
  for (let index = 0; index < windowSize - 1; index++) high = Math.imul(high, HASH_BASE);

  let hash = 0;
  for (let index = 0; index < windowSize; index++) {
    hash = (Math.imul(hash, HASH_BASE) + text.charCodeAt(index)) | 0;
  }

  const seen = new Map<number, number>([[hash, 0]]);
  for (let index = 1; index + windowSize <= text.length; index++) {
    // slide the window: drop the leaving character, add the entering one
    hash =
      (Math.imul(hash - Math.imul(text.charCodeAt(index - 1), high), HASH_BASE) +
        text.charCodeAt(index + windowSize - 1)) |
      0;

    const previous = seen.get(hash);
    if (previous !== undefined && text.startsWith(text.slice(index, index + windowSize), previous)) {
      // confirmed on the text: measure how far the two blocks really agree
      let length = windowSize;
      while (
        previous + length < text.length &&
        index + length < text.length &&
        text[previous + length] === text[index + length]
      ) {
        length++;
      }
      if (length >= minimum) return length;
    }
    seen.set(hash, index);
  }
  return 0;
};
