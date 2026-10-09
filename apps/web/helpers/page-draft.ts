/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Local drafts for pages.
 *
 * Page content is written to the database only when the editor's save button is
 * pressed, so an edit that has not been saved yet exists nowhere else. The draft
 * is kept in the browser: it survives a reload, a crash, and the reload the live
 * session forces when another writer's revision wins (the server drops the
 * collaborative document and every client rebuilds from the saved copy).
 */

export type TPageDraftReason = "unsaved" | "conflict";

export type TPageDraftConflict = {
  /** who saved the page before this draft did */
  savedBy: string;
  /** ISO timestamp of that save */
  savedAt: string;
};

export type TPageDraft = {
  /** the editor's HTML when the draft was stored */
  html: string;
  /** ISO timestamp of the draft itself */
  savedAt: string;
  reason: TPageDraftReason;
  conflict?: TPageDraftConflict | null;
};

const draftKey = (pageId: string) => `plane:page-draft:${pageId}`;

/** The editor refuses oversized documents anyway; a draft past this is dropped. */
const MAX_DRAFT_LENGTH = 4_000_000;

export const readPageDraft = (pageId: string): TPageDraft | null => {
  if (typeof window === "undefined" || !pageId) return null;

  try {
    const raw = window.localStorage.getItem(draftKey(pageId));
    if (!raw) return null;

    const draft = JSON.parse(raw) as TPageDraft;
    if (!draft?.html) return null;

    return draft;
  } catch (error) {
    console.error("Error reading the local page draft:", error);
    return null;
  }
};

export const writePageDraft = (pageId: string, draft: TPageDraft): void => {
  if (typeof window === "undefined" || !pageId) return;
  if (draft.html.length > MAX_DRAFT_LENGTH) return;

  try {
    window.localStorage.setItem(draftKey(pageId), JSON.stringify(draft));
  } catch (error) {
    // A full quota must not break editing: the draft is a safety net, not a
    // requirement.
    console.error("Error storing the local page draft:", error);
  }
};

export const clearPageDraft = (pageId: string): void => {
  if (typeof window === "undefined" || !pageId) return;

  try {
    window.localStorage.removeItem(draftKey(pageId));
  } catch (error) {
    console.error("Error clearing the local page draft:", error);
  }
};
