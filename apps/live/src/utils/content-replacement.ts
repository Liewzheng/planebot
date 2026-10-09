/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Guard for the "external write silently reverted" failure mode.
 *
 * When content is written outside the live server (public API / CLI re-upload),
 * the live server drops its in-memory document and every client is asked to
 * reload. A client that still holds an older copy can push it back into the
 * shared document and delete the freshly written content — measured on a real
 * page: every Yjs struct of the external revision (8990 of them) was deleted and
 * the pre-upload text came back, which the store then persisted.
 *
 * Comparing visible-text length is a coarse signal, but it is only armed for the
 * short window right after an external replacement, where a client legitimately
 * dropping most of the page is far less likely than a stale copy winning. A
 * refused store closes the clients instead, so they reload the external content
 * from the database rather than keeping a local copy that cannot be saved.
 *
 * State is per process: this deployment runs a single live instance. A
 * multi-instance deployment would have to share it through Redis.
 */

/**
 * Fraction of the externally written text a store must keep to be accepted.
 *
 * Deliberately loose: the guard only has text length to work with, and a user
 * who legitimately deletes a big part of a page right after a CLI re-upload must
 * not be blocked. It therefore only catches a drastic drop (the reported
 * incident kept 57% of the external text) and is cleared by the first accepted
 * store, so at most one store per external write can be refused.
 */
export const CONTENT_SHRINK_TOLERANCE = 0.7;

/** How long a replacement stays armed. */
export const CONTENT_REPLACEMENT_TTL_MS = 10 * 60 * 1000;

type TContentReplacement = {
  /** Smallest accepted visible-text length, already scaled by the tolerance. */
  minTextLength: number;
  expiresAt: number;
};

const replacements = new Map<string, TContentReplacement>();

/**
 * Visible text of an HTML body: attribute-only differences (the editor's markup
 * vs the import path's) do not change it, so it is comparable across the two.
 */
export const visibleText = (html: string): string =>
  (html ?? "")
    .replace(/<[^>]*>/g, "")
    .replace(/\s+/g, "");

/** Visible-text length, the quantity compared on both sides of a store. */
export const contentTextLength = (html: string): number => visibleText(html).length;

/** Arm the guard for a document whose database content was just replaced. */
export const recordContentReplacement = (docId: string, textLength: number): void => {
  // Drop expired entries first: the map would otherwise keep a document that is
  // replaced and never opened again until something touches its key.
  const now = Date.now();
  for (const [key, entry] of replacements) {
    if (entry.expiresAt <= now) replacements.delete(key);
  }
  replacements.set(docId, {
    minTextLength: Math.floor(textLength * CONTENT_SHRINK_TOLERANCE),
    expiresAt: now + CONTENT_REPLACEMENT_TTL_MS,
  });
};

/** Smallest accepted text length for a document, or null when nothing was replaced recently. */
export const getContentReplacementFloor = (docId: string): number | null => {
  const entry = replacements.get(docId);
  if (!entry) return null;
  if (entry.expiresAt <= Date.now()) {
    replacements.delete(docId);
    return null;
  }
  return entry.minTextLength;
};

/** The replacement has been applied to the shared document: stop guarding it. */
export const clearContentReplacement = (docId: string): void => {
  replacements.delete(docId);
};

/** Armed documents; diagnostics and tests. */
export const contentReplacementCount = (): number => replacements.size;

/* ------------------------------------------------------------------------- *
 * Duplicated-growth guard
 *
 * The other failure mode: a stale client copy merges in and Yjs keeps the union,
 * so the document ends up holding the page twice. A duplicated document used to
 * slip past the line-uniqueness heuristic when its ratio landed just above the
 * threshold (observed: 0.58 against 0.6) and was then persisted. This guard
 * compares against the content the server last saw for that document, which is
 * exact: the previous content appears verbatim inside a much longer body.
 * ------------------------------------------------------------------------- */

/** How much longer the body must get before the previous content counts as copied. */
export const CONTENT_GROWTH_TOLERANCE = 1.3;

/** Above this long-line uniqueness the body still reads as distinct prose. */
export const CONTENT_LINE_UNIQUENESS_FLOOR = 0.85;

/** Long lines needed before the duplication ratio means anything. */
const MIN_COMPARABLE_LINES = 10;

/** Bodies shorter than this are not compared: small pages rewrite wholesale. */
const MIN_COMPARABLE_TEXT_LENGTH = 200;

/** Longer than the replacement window: a stale copy can arrive much later. */
const CONTENT_MEMORY_TTL_MS = 24 * 60 * 60 * 1000;

const lastSeenContent = new Map<string, { text: string; expiresAt: number }>();

/** Remember the content the server last loaded or stored for a document. */
export const recordDocumentContent = (docId: string, html: string): void => {
  const now = Date.now();
  for (const [key, entry] of lastSeenContent) {
    if (entry.expiresAt <= now) lastSeenContent.delete(key);
  }
  lastSeenContent.set(docId, { text: visibleText(html), expiresAt: now + CONTENT_MEMORY_TTL_MS });
};

/**
 * Long lines (over 20 characters) of a body, the unit the duplication ratio is
 * measured on. Mirrors `detectDocumentDuplication`.
 */
const longLines = (html: string): string[] =>
  html
    .replace(/<[^>]+>/g, "\n")
    .split("\n")
    .map((line) => line.replace(/\s+/g, " ").trim())
    .filter((line) => line.length > 20);

/**
 * True when `html` reads as the previously known content plus a second copy — the
 * shape a stale client merge produces.
 *
 * A union interleaves the two copies, so the previous text is not a contiguous
 * substring (measured on the incident's own documents); what survives is the
 * growth (1.51x) together with a long-line uniqueness ratio near 0.65 against
 * 0.95+ for healthy pages.
 */
export const looksLikeDuplicatedGrowth = (
  docId: string,
  html: string
): { duplicated: boolean; ratio: number; uniqueRatio: number } => {
  const entry = lastSeenContent.get(docId);
  const lines = longLines(html);
  const uniqueRatio = lines.length > 0 ? new Set(lines).size / lines.length : 1;

  if (!entry || entry.expiresAt <= Date.now()) return { duplicated: false, ratio: 1, uniqueRatio };
  const previous = entry.text;
  if (previous.length < MIN_COMPARABLE_TEXT_LENGTH) return { duplicated: false, ratio: 1, uniqueRatio };

  const ratio = visibleText(html).length / previous.length;
  if (ratio < CONTENT_GROWTH_TOLERANCE) return { duplicated: false, ratio, uniqueRatio };
  if (lines.length < MIN_COMPARABLE_LINES) return { duplicated: false, ratio, uniqueRatio };

  return { duplicated: uniqueRatio <= CONTENT_LINE_UNIQUENESS_FLOOR, ratio, uniqueRatio };
};

/** Documents remembered for duplication checks; diagnostics and tests. */
export const rememberedDocumentCount = (): number => lastSeenContent.size;
