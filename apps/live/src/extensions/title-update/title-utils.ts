/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { sanitizeHTML } from "@plane/utils";

/**
 * Utility function to extract text from HTML content
 */
export const extractTextFromHTML = (html: string): string => {
  // Use sanitizeHTML to safely extract text and remove all HTML tags
  // This is more secure than regex as it handles edge cases and prevents injection
  // Note: sanitizeHTML trims whitespace, which is acceptable for title extraction
  return sanitizeHTML(html) || "";
};

/** Smallest repeated unit that still counts as a duplicated title */
const MIN_REPEATED_UNIT_LENGTH = 8;

/**
 * Collapse a title that repeats itself.
 *
 * When a stale client copy is union-merged into the live document, Yjs keeps
 * both sides of every fragment — the title ends up as "TitleTitle". Saving that
 * as the page name makes the title grow on every reconnect, so collapse exact
 * repetitions before persisting.
 *
 * "TitleTitle" -> "Title", "ABCABCABC" -> "ABC"; titles that are not an exact
 * repetition of a unit of at least MIN_REPEATED_UNIT_LENGTH characters are
 * returned unchanged.
 */
export const collapseRepeatedTitle = (title: string): string => {
  const value = title.trim();
  if (value.length < MIN_REPEATED_UNIT_LENGTH * 2) return value;

  for (let unit = MIN_REPEATED_UNIT_LENGTH; unit <= value.length / 2; unit += 1) {
    if (value.length % unit !== 0) continue;
    const candidate = value.slice(0, unit);
    let repeated = true;
    for (let offset = unit; offset < value.length; offset += unit) {
      if (value.slice(offset, offset + unit) !== candidate) {
        repeated = false;
        break;
      }
    }
    if (repeated) return candidate;
  }

  return value;
};
