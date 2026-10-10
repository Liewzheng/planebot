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
 * The stamp is the page body the cache was last reconciled with. The save path
 * re-stamps with what it just wrote, so a client that simply saves keeps its
 * cache; the sync effect in `use-yjs-setup` also re-stamps when the session
 * reaches the server-synced state, so a client that only reads the page also
 * leaves a usable stamp and the next open does not have to rebuild the cache
 * from scratch.
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
 * An undefined `contentStamp` is "the served revision is not known yet"
 * (the pages-list mount path, where `description_html` is only added by
 * the detail serializer and is fetched after the editor mounts). A
 * guessed wipe in that window is what causes the multi-second "正在同步..."
 * on long pages — wait for the served value and re-decide when it
 * arrives. A known served stamp with no stored stamp is the first-visit
 * case and still resets (we cannot prove the cache belongs to the served
 * revision); a known mismatch still resets (the cache was built from an
 * earlier revision and would merge as a second copy of every block,
 * PLANE-76).
 */
export const shouldResetLocalDocument = (storedStamp: string | null, contentStamp?: string): boolean => {
  if (contentStamp === undefined) return false;
  if (storedStamp === null) return true;
  return storedStamp !== contentStamp;
};

/**
 * True when writing the served stamp preserves the evidence the
 * PLANE-76 guard keys on.
 *
 * The sync-time stamp write must NOT overwrite a stored stamp that
 * already differs from the served value: the reset flag set by the
 * stamp effect is consumed only at the next persistence bind, and
 * overwriting the stored stamp would let the next open skip the reset
 * the guard asked for. A first visit (no stored stamp) writes so the
 * next open has provenance; a matching stored stamp is an idempotent
 * write; an unknown served stamp is a no-op (the caller does not have
 * anything to write).
 */
export const shouldWriteStamp = (storedStamp: string | null, contentStamp: string | undefined): boolean => {
  if (contentStamp === undefined) return false;
  if (storedStamp === null) return true;
  return storedStamp === contentStamp;
};
