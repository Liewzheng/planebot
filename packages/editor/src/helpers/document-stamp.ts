/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Keeps the locally cached collaborative document honest.
 *
 * Yjs merges as a union and never deletes, so a cached copy that was built from
 * an *earlier* version of a page merges into the current one as a second copy of
 * everything — the page then shows its content twice, and every client that
 * connects adopts that copy (PLANE-76). The database is the only authority that
 * can break the cycle, but the client can refuse to feed it: the cache is used
 * only when it provably descends from the revision the server is serving.
 *
 * The stamp is the page body the cache was last reconciled with. A client's own
 * unsaved edits do not change it (the stored body only changes when someone
 * saves), and the save path re-stamps with what it just wrote, so a client that
 * is simply editing keeps its cache.
 */

const STAMP_KEY_PREFIX = "plane:doc-stamp:";

/** FNV-1a over the body, plus its length: cheap and stable across sessions. */
const hashBody = (body: string): string => {
  let hash = 0x811c9dc5;
  for (let index = 0; index < body.length; index++) {
    hash ^= body.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return `${body.length}:${(hash >>> 0).toString(36)}`;
};

/** Stamp of a page body, or undefined when there is no body to stamp yet. */
export const documentStamp = (body?: string | null): string | undefined =>
  typeof body === "string" ? hashBody(body) : undefined;

const stampKey = (docId: string) => `${STAMP_KEY_PREFIX}${docId}`;

export const readDocumentStamp = (docId: string): string | null => {
  if (typeof window === "undefined" || !docId) return null;
  try {
    return window.localStorage.getItem(stampKey(docId));
  } catch (error) {
    console.error("Error reading the document stamp:", error);
    return null;
  }
};

export const writeDocumentStamp = (docId: string, stamp?: string): void => {
  if (typeof window === "undefined" || !docId || !stamp) return;
  try {
    window.localStorage.setItem(stampKey(docId), stamp);
  } catch (error) {
    console.error("Error storing the document stamp:", error);
  }
};

/**
 * True when the cached document must not be merged into the server's copy.
 *
 * Both unknown cases reset the cache on purpose: without a stamp there is no way
 * to prove the cache belongs to the revision being served, and merging a copy
 * that does not belong is exactly what duplicates a page. The reset costs the
 * offline cache of one page, which the server refills on the next sync.
 */
export const shouldResetLocalDocument = (storedStamp: string | null, contentStamp?: string): boolean => {
  if (!contentStamp) return true;
  if (storedStamp === null) return true;
  return storedStamp !== contentStamp;
};
