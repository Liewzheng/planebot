/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useEffect, useRef, useState } from "react";
// plane imports
import type { EditorRefApi } from "@plane/editor";
import {
  bodyContentJSON,
  clearPublishInFlight,
  convertBinaryDataToBase64String,
  documentStamp,
  markPublishInFlight,
  writeDocumentStamp,
} from "@plane/editor";
import type { TDocumentPayload } from "@plane/types";
// helpers
import type { TPageDraft, TPageDraftConflict } from "@/helpers/page-draft";
import { clearPageDraft, readPageDraft, writePageDraft } from "@/helpers/page-draft";

/** How long after the last keystroke the unsaved state is recomputed. */
const DIRTY_CHECK_DEBOUNCE = 800;

/**
 * The draft is the crash net, not the indicator: it does not have to follow
 * every pause in typing, and each write serializes the whole document into
 * localStorage.
 */
const DRAFT_WRITE_INTERVAL = 3000;

/** `editorRef.current` may lag the editor's ready flag by a commit; retry a bit. */
const EDITOR_LOOKUP_RETRIES = 20;
const EDITOR_LOOKUP_RETRY_DELAY = 100;

export type TSaveError = "reverted" | "failed" | "draft-failed" | null;

type TArgs = {
  pageId: string;
  editorRef: React.RefObject<EditorRefApi | null>;
  /** the editor exists and its document is loaded */
  enabled: boolean;
  /** the editor is in edit mode (a draft is only kept for the user's own work) */
  isEditing: boolean;
  /** bumped whenever the editor is (re)created: the dirty tracking must re-subscribe */
  editorEpoch: number;
  updateDescription: (document: TDocumentPayload) => Promise<{ updated_at?: string } | undefined>;
  /** the caller's own unpublished revision, kept beside the page (PLANE-77) */
  fetchDraft: () => Promise<{ description_html: string | null } | undefined>;
  /**  of the published page, so the publish can prove its base (PLANE-78) */
  pageUpdatedAt: string | undefined;
  /** the served body, to tell a draft that still matters from one a publish superseded */
  pageDescriptionHtml: string | undefined;
  updateDraft: (payload: { description_html: string }) => Promise<{ description_html: string } | undefined>;
  deleteDraft: () => Promise<void>;
};

/**
 * Keep the draft only when it holds something the page does not.
 *
 * A publish that lands still leaves a draft behind whenever the client cannot
 * finish handling it: the API replaces the collaborative document, the live
 * server force-closes this very client, and the reload can outrun the publish
 * response, so the success path that discards the draft never runs. The page
 * then offers "unpublished changes" that are already in the page. Comparing
 * content identities tells the two apart without guessing.
 */
const readMeaningfulDraft = (pageId: string, publishedHtml?: string | null): TPageDraft | null => {
  const draft = readPageDraft(pageId);
  if (!draft) return null;

  const publishedIdentity = bodyContentJSON(publishedHtml);
  const draftIdentity = bodyContentJSON(draft.html);
  if (publishedIdentity !== undefined && draftIdentity === publishedIdentity) {
    clearPageDraft(pageId);
    return null;
  }

  return draft;
};

/**
 * Explicit save for a page.
 *
 * Nothing the editor does reaches the database on its own: the collaborative
 * session is not persisted (the live server only stores on request), so this
 * hook owns the write. It tracks whether the document differs from the revision
 * this session started from, keeps the unsaved work as a local draft, and saves
 * on demand — the save button, or Cmd/Ctrl+S.
 */
export const usePageSave = (args: TArgs) => {
  const {
    pageId,
    editorRef,
    enabled,
    isEditing,
    editorEpoch,
    updateDescription,
    fetchDraft,
    updateDraft,
    deleteDraft,
    pageUpdatedAt,
    pageDescriptionHtml,
  } = args;
  // state
  const [isDirty, setIsDirty] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [lastSavedAt, setLastSavedAt] = useState<string | null>(null);
  const [draft, setDraft] = useState<TPageDraft | null>(null);
  /** the stashed revision on the server; only this user ever sees it */
  const [serverDraft, setServerDraft] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<TSaveError>(null);
  const [draftError, setDraftError] = useState(false);
  // the handlers come from a memo higher up; keep them out of effect deps so a
  // re-render does not re-fetch the draft
  const draftHandlersRef = useRef({ fetchDraft, updateDraft, deleteDraft });
  useEffect(() => {
    draftHandlersRef.current = { fetchDraft, updateDraft, deleteDraft };
  }, [fetchDraft, updateDraft, deleteDraft]);
  // refs
  const baselineRef = useRef<string | null>(null);
  const isDirtyRef = useRef(false);
  const saveInFlightRef = useRef(false);
  const serverDraftRef = useRef<string | null>(null);
  // the editor instance the dirty check is subscribed to, so a recreated
  // editor (edit-mode toggle, draft restore) cannot silently kill the tracking
  const subscribedEditorRef = useRef<EditorRefApi | null>(null);
  // read inside the debounced check without restarting it (a restart would
  // recapture the baseline and lose the unsaved state)
  const isEditingRef = useRef(isEditing);
  const lastDraftWriteRef = useRef(0);
  useEffect(() => {
    isEditingRef.current = isEditing;
  }, [isEditing]);

  // the published page's , kept in a ref so a revalidation cannot
  // silently move the publish base mid-edit
  const pageUpdatedAtRef = useRef(pageUpdatedAt);
  // the revision this editing session started from: a publish is refused when
  // the page moved on since (first publisher wins, PLANE-78)
  const baseUpdatedAtRef = useRef<string | undefined>(pageUpdatedAt);

  useEffect(() => {
    pageUpdatedAtRef.current = pageUpdatedAt;
  }, [pageUpdatedAt]);

  // the served body, read when deciding whether a kept draft is still news
  const pageDescriptionHtmlRef = useRef(pageDescriptionHtml);
  useEffect(() => {
    pageDescriptionHtmlRef.current = pageDescriptionHtml;
  }, [pageDescriptionHtml]);

  // The revision this session started from: any divergence from it is unsaved work.
  // Re-runs on every editor (re)creation (`editorEpoch`): the editor instance is
  // recreated when edit mode is entered/left and when a draft is restored, and
  // the subscription and baseline must follow it — otherwise typing is tracked
  // against a destroyed editor and the unsaved state silently freezes.
  useEffect(() => {
    if (!enabled) return;

    let cancelled = false;
    let attempts = 0;
    let subscribeTimer: ReturnType<typeof setTimeout> | null = null;
    let checkTimer: ReturnType<typeof setTimeout> | null = null;
    let unsubscribe: (() => void) | undefined;

    // A draft left by a previous visit (or a save that lost a conflict) is
    // offered back instead of silently applying itself over the saved revision.
    setDraft(readMeaningfulDraft(pageId, pageDescriptionHtmlRef.current));

    const check = () => {
      const editor = editorRef.current;
      if (!editor) return;
      // the editor was recreated since the subscription was made: re-anchor
      if (editor !== subscribedEditorRef.current) {
        start();
        return;
      }
      // an IME composition owns the DOM right now: its text is not part of the
      // document yet, so neither the dirty state nor the draft may read it
      if (editor.isComposing()) return;

      const current = editor.getDocument().html;
      if (typeof current !== "string" || baselineRef.current === null) return;

      const dirty = current !== baselineRef.current;
      if (dirty !== isDirtyRef.current) {
        isDirtyRef.current = dirty;
        setIsDirty(dirty);
      }

      // Keep the draft as fresh as the last pause in typing: it is the only
      // copy of this revision until the user saves. Only while editing —
      // content that arrived from another writer is not this user's draft.
      if (dirty && isEditingRef.current && Date.now() - lastDraftWriteRef.current >= DRAFT_WRITE_INTERVAL) {
        lastDraftWriteRef.current = Date.now();
        writePageDraft(pageId, { html: current, savedAt: new Date().toISOString(), reason: "unsaved" });
      }
    };

    const scheduleCheck = () => {
      if (checkTimer) clearTimeout(checkTimer);
      checkTimer = setTimeout(check, DIRTY_CHECK_DEBOUNCE);
    };

    const start = () => {
      if (cancelled) return;

      const editor = editorRef.current;
      if (!editor) {
        if (attempts++ < EDITOR_LOOKUP_RETRIES) subscribeTimer = setTimeout(start, EDITOR_LOOKUP_RETRY_DELAY);
        return;
      }

      unsubscribe?.();
      // The document as loaded is the last saved revision, and its `updated_at`
      // is the base a publish must match (PLANE-78).
      baselineRef.current = editor.getDocument().html ?? "";
      isDirtyRef.current = false;
      setIsDirty(false);
      subscribedEditorRef.current = editor;
      unsubscribe = editor.onStateChange(scheduleCheck);
    };

    start();

    return () => {
      cancelled = true;
      if (subscribeTimer) clearTimeout(subscribeTimer);
      if (checkTimer) clearTimeout(checkTimer);
      unsubscribe?.();
      subscribedEditorRef.current = null;
    };
  }, [enabled, editorRef, pageId, editorEpoch]);

  /** Returns true when the page holds what the editor holds afterwards. */
  const save = useCallback(async (): Promise<boolean> => {
    const editor = editorRef.current;
    if (!editor) {
      console.error("Could not publish: the editor is not available.");
      setSaveError("failed");
      return false;
    }
    // a save is already on its way: report the current state, not a second write
    if (saveInFlightRef.current) return !isDirtyRef.current;

    // a composition in flight means the document does not hold what the user
    // sees yet: wait for the input method to commit before serializing
    await editor.waitUntilCompositionEnds();

    const { binary, html, json } = editor.getDocument();
    if (!binary || !json || typeof html !== "string") {
      console.error("Could not publish: the document could not be serialized.");
      setSaveError("failed");
      return false;
    }

    saveInFlightRef.current = true;
    setIsSaving(true);
    setSaveError(null);
    // the API replaces the shared document on a successful write, and this
    // client is force-closed with it — possibly before this response lands
    // (see helpers/publish-state in @plane/editor)
    markPublishInFlight(pageId);

    try {
      const response = await updateDescription({
        description_binary: convertBinaryDataToBase64String(binary),
        description_html: html,
        description_json: json,
        save_source: "editor",
        base_updated_at: baseUpdatedAtRef.current,
      });
      // the page now holds this revision: it is the base of the next publish
      if (response?.updated_at) baseUpdatedAtRef.current = response.updated_at;

      // the document is now the saved revision
      baselineRef.current = html;
      // and the local copy descends from it: keep the cache valid
      writeDocumentStamp(pageId, documentStamp(html));
      isDirtyRef.current = false;
      setIsDirty(false);
      setDraft(null);
      clearPageDraft(pageId);
      setLastSavedAt(new Date().toISOString());
      // the published revision supersedes the stashed one: drop it so the
      // banner does not keep offering a draft the page has moved past
      if (serverDraftRef.current !== null) {
        serverDraftRef.current = null;
        setServerDraft(null);
        void draftHandlersRef.current.deleteDraft().catch((error) => {
          console.error("Could not discard the superseded page draft:", error);
        });
      }
      return true;
    } catch (error) {
      const data = ((error as { response?: { data?: unknown } })?.response?.data ?? error) as {
        error_code?: string;
        error_message?: string;
        conflict?: { saved_by?: string; saved_at?: string } | null;
      };

      // The write is kept locally either way: it is the only copy of this work.
      const conflictInfo: TPageDraftConflict | null = data?.conflict?.saved_by
        ? { savedBy: data.conflict.saved_by, savedAt: data.conflict.saved_at ?? "" }
        : null;
      // both refusals mean "the server would not take this revision": keep the
      // work locally and let the user decide
      const isConflict =
        data?.error_code === "CONTENT_REVERTED" ||
        data?.error_code === "CONTENT_DUPLICATED" ||
        data?.error_code === "PAGE_VERSION_CONFLICT";
      const nextDraft: TPageDraft = {
        html,
        savedAt: new Date().toISOString(),
        reason: isConflict ? "conflict" : "unsaved",
        conflict: conflictInfo,
      };

      writePageDraft(pageId, nextDraft);
      setDraft(nextDraft);
      setSaveError(isConflict ? "reverted" : "failed");
      return false;
    } finally {
      saveInFlightRef.current = false;
      setIsSaving(false);
      clearPublishInFlight(pageId);
    }
  }, [editorRef, pageId, updateDescription]);

  /**
   * Park the current revision as my own unpublished draft.
   *
   * The draft is stored beside the page, so nothing that reads the page — other
   * accounts, the API/CLI, the version history, the export — can see it. The
   * document then goes back to the published revision, because an unpublished
   * edit left in the shared document would be visible to every open editor.
   */
  const stashAsDraft = useCallback(async (): Promise<boolean> => {
    const editor = editorRef.current;
    if (!editor) return false;
    const { html } = editor.getDocument();
    if (typeof html !== "string") return false;

    try {
      await draftHandlersRef.current.updateDraft({ description_html: html });
    } catch (error) {
      console.error("Could not stash the page draft:", error);
      setDraftError(true);
      return false;
    }

    serverDraftRef.current = html;
    setServerDraft(html);
    setDraftError(false);
    // back to what everyone else sees (edit mode only: in reading mode the
    // document is the shared one and must never be written to)
    if (isEditingRef.current && baselineRef.current !== null) {
      editor.setEditorValue(baselineRef.current);
    }
    isDirtyRef.current = false;
    setIsDirty(false);
    setDraft(null);
    clearPageDraft(pageId);
    return true;
  }, [editorRef, pageId]);

  const discardServerDraft = useCallback(async (): Promise<boolean> => {
    try {
      await draftHandlersRef.current.deleteDraft();
    } catch (error) {
      console.error("Could not discard the page draft:", error);
      setDraftError(true);
      return false;
    }
    serverDraftRef.current = null;
    setServerDraft(null);
    setDraftError(false);
    return true;
  }, []);

  const discardDraft = useCallback(() => {
    // "discard" only drops the kept copy: an editor that is open keeps what it
    // holds, and in reading mode the document is the shared one — writing the
    // baseline back there would republish a revision nobody asked for.
    clearPageDraft(pageId);
    setDraft(null);
    setSaveError(null);
  }, [pageId]);

  // Deliberately no `beforeunload` prompt: the draft is the safety net, and a
  // native prompt would also block the reload the live session forces when
  // another writer's revision wins — which is exactly when the document is
  // dirty, leaving the client stuck on content the server no longer has.

  // Cmd/Ctrl+S saves, the way every editor does.
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "s") return;
      event.preventDefault();
      event.stopPropagation();
      void save();
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [save]);

  return {
    isDirty,
    isSaving,
    lastSavedAt,
    draft,
    serverDraft,
    saveError,
    draftError,
    save,
    stashAsDraft,
    discardServerDraft,
    discardDraft,
  };
};
