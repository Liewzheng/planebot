/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { HocuspocusProvider } from "@hocuspocus/provider";
// react
import { useCallback, useEffect, useRef, useState } from "react";
// indexeddb
import { IndexeddbPersistence } from "y-indexeddb";
// yjs
import * as Y from "yjs";
// helpers
import { createDraftSideDoc } from "@/helpers/editing-document";
import { readDocumentStamp, shouldResetLocalDocument } from "@/helpers/document-stamp";
// types
import type { CollaborationState, CollabStage, CollaborationError } from "@/types/collaboration";

// Helper to check if a close code indicates a forced close
const isForcedCloseCode = (code: number | undefined): boolean => {
  if (!code) return false;
  // All custom close codes (4000-4004) are treated as forced closes
  return code >= 4000 && code <= 4004;
};

/**
 * Close reason sent by the live server when the document was overwritten
 * through the API (page re-upload). The client's in-memory copy and its
 * IndexedDB cache are both stale; the session must be rebuilt from scratch
 * so the new server-side content wins instead of being union-merged with
 * the old local state.
 *
 * Safari does not expose the close reason on the WebSocket close event, so
 * the server also sends the dedicated CONTENT_REPLACED close code (4004) and
 * both signals are accepted.
 */
const CONTENT_REPLACED_REASON = "content_replaced";
const CONTENT_REPLACED_CODE = 4004;

/**
 * FNV-1a over the draft HTML. Used to identify the side doc that backs
 * the editor when a draft is active: a different draft HTML is a
 * different identity, so the editor is recreated against a fresh side
 * doc rather than continuing to edit a stale one.
 */
const hashDraft = (html: string): string => {
  let hash = 0x811c9dc5;
  for (let index = 0; index < html.length; index += 1) {
    hash ^= html.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return `${html.length}:${(hash >>> 0).toString(36)}`;
};

type UseYjsSetupArgs = {
  docId: string;
  serverUrl: string;
  authToken: string;
  onStateChange?: (state: CollaborationState) => void;
  options?: {
    maxConnectionAttempts?: number;
  };
  /**
   * Body HTML for the author's own unpublished draft, set by the page UI when
   * the user clicks "Load Draft". Applied to the provider document so the
   * collaborative editor renders the draft instead of the published revision.
   * Cleared (set back to null) when the draft is discarded or saved.
   */
  draftHtml?: string | null;
  /**
   * The body stamp the API last served for this page. Used to refuse a
   * locally cached document that no longer descends from the served
   * revision (PLANE-76): a stale cache would merge as a duplicate copy of
   * every block.
   */
  contentStamp?: string;
};

const DEFAULT_MAX_RETRIES = 3;

export const useYjsSetup = ({ docId, serverUrl, authToken, onStateChange, draftHtml, contentStamp }: UseYjsSetupArgs) => {
  // Current collaboration stage
  const [stage, setStage] = useState<CollabStage>({ kind: "initial" });

  // Cache readiness state
  const [hasCachedContent, setHasCachedContent] = useState(false);
  const [isCacheReady, setIsCacheReady] = useState(false);

  // Provider and Y.Doc in state (nullable until effect runs)
  const [yjsSession, setYjsSession] = useState<{ provider: HocuspocusProvider; ydoc: Y.Doc } | null>(null);

  // Side doc for an active draft session. The editor binds to this doc (not
  // `provider.document`) so the user's unpublished body stays local: the
  // Hocuspocus provider must never observe a write from the draft or the
  // live server's `storeDocument` would persist the unpublished body to
  // the database and a discarded draft would leave a leaked revision
  // behind (PLANE-77; the prior commit's `applyDraftToProviderDocument`
  // got this wrong by writing the draft straight into the live doc).
  // `null` when no draft is active: the editor binds to `provider.document`.
  const [draftSideDoc, setDraftSideDoc] = useState<Y.Doc | null>(null);

  // Bumped when the session must be rebuilt from scratch (e.g. the document
  // was replaced server-side): a new provider + fresh Y.Doc is created and
  // the stale IndexedDB cache is cleared before rebinding.
  const [sessionEpoch, setSessionEpoch] = useState(0);

  // Use refs for values that need to be mutated from callbacks
  const retryCountRef = useRef(0);
  const forcedCloseSignalRef = useRef(false);
  const isDisposedRef = useRef(false);
  const stageRef = useRef<CollabStage>({ kind: "initial" });
  const lastReconnectTimeRef = useRef(0);
  const clearCacheOnNextSessionRef = useRef(false);
  /**
   * Identity of the draft the current side doc was built from. Lets the
   * side-doc effect tell "the same draft is still active" (no-op) apart
   * from "a fresh draft arrived" (rebuild).
   */
  const activeDraftIdentityRef = useRef<string | null>(null);

  // Create/destroy provider in effect (not during render)
  useEffect(() => {
    // Reset refs when creating new provider (e.g., document switch)
    retryCountRef.current = 0;
    isDisposedRef.current = false;
    forcedCloseSignalRef.current = false;
    stageRef.current = { kind: "initial" };

    const provider = new HocuspocusProvider({
      name: docId,
      token: authToken,
      url: serverUrl,
      onAuthenticationFailed: () => {
        if (isDisposedRef.current) return;
        const error: CollaborationError = { type: "auth-failed", message: "Authentication failed" };
        const newStage = { kind: "disconnected" as const, error };
        stageRef.current = newStage;
        setStage(newStage);
      },
      onConnect: () => {
        if (isDisposedRef.current) {
          provider?.disconnect();
          return;
        }
        retryCountRef.current = 0;
        // After successful connection, transition to awaiting-sync (onSynced will move to synced)
        const newStage = { kind: "awaiting-sync" as const };
        stageRef.current = newStage;
        setStage(newStage);
      },
      onStatus: ({ status: providerStatus }) => {
        if (isDisposedRef.current) return;
        if (providerStatus === "connecting") {
          // Derive whether this is initial connect or reconnection from retry count
          const isReconnecting = retryCountRef.current > 0;
          setStage(isReconnecting ? { kind: "reconnecting", attempt: retryCountRef.current } : { kind: "connecting" });
        } else if (providerStatus === "disconnected") {
          // Do not transition here; let handleClose decide the final stage
        } else if (providerStatus === "connected") {
          // Connection succeeded, move to awaiting-sync
          const newStage = { kind: "awaiting-sync" as const };
          stageRef.current = newStage;
          setStage(newStage);
        }
      },
      onSynced: () => {
        if (isDisposedRef.current) return;
        retryCountRef.current = 0;
        // Document sync complete
        const newStage = { kind: "synced" as const };
        stageRef.current = newStage;
        setStage(newStage);
      },
    });

    const pauseProvider = () => {
      const wsProvider = provider.configuration.websocketProvider;
      if (wsProvider) {
        try {
          wsProvider.shouldConnect = false;
          wsProvider.disconnect();
        } catch (error) {
          console.error(`Error pausing websocketProvider:`, error);
        }
      }
    };

    const permanentlyStopProvider = () => {
      isDisposedRef.current = true;

      const wsProvider = provider.configuration.websocketProvider;
      if (wsProvider) {
        try {
          wsProvider.shouldConnect = false;
          wsProvider.disconnect();
          wsProvider.destroy();
        } catch (error) {
          console.error(`Error tearing down websocketProvider:`, error);
        }
      }
      try {
        provider.destroy();
      } catch (error) {
        console.error(`Error destroying provider:`, error);
      }
    };

    const handleClose = (closeEvent: { event?: { code?: number; reason?: string } }) => {
      if (isDisposedRef.current) return;

      const closeCode = closeEvent.event?.code;
      const closeReason = closeEvent.event?.reason;
      const wsProvider = provider.configuration.websocketProvider;
      const shouldConnect = wsProvider.shouldConnect;
      const isForcedClose = isForcedCloseCode(closeCode) || forcedCloseSignalRef.current || shouldConnect === false;

      if (isForcedClose) {
        // The document was replaced server-side (API re-upload). Our in-memory
        // copy and IndexedDB cache are stale: rebuilding the session (fresh
        // Y.Doc + cleared cache) makes the new server content authoritative
        // instead of union-merging the old content back on reconnect.
        if (closeReason === CONTENT_REPLACED_REASON || closeCode === CONTENT_REPLACED_CODE) {
          clearCacheOnNextSessionRef.current = true;
          const error: CollaborationError = {
            type: "content-replaced",
            message: "This page was updated elsewhere. Reloading the latest content.",
          };
          const newStage = { kind: "disconnected" as const, error };
          stageRef.current = newStage;
          setStage(newStage);
          setSessionEpoch((epoch) => epoch + 1);
          // Rebuilding the session is not enough on its own: the page can end
          // up stuck on "syncing". Clear the stale IndexedDB copy here (the
          // reload below would otherwise lose the pending clear) and reload
          // the page so the server document is loaded from scratch. Guarded by
          // sessionStorage so a repeated replacement cannot cause a reload
          // loop.
          void (async () => {
            // let the session-rebuild effect run its cleanup first, so the
            // previous persistence instance cannot write the stale state back
            await new Promise((resolve) => setTimeout(resolve, 50));
            try {
              const cleaner = new IndexeddbPersistence(docId, new Y.Doc());
              await cleaner.clearData();
              cleaner.destroy();
            } catch (cleanError) {
              console.error(`Error clearing stale IndexedDB cache for ${docId}:`, cleanError);
            }
            const reloadKey = `plane:content-replaced:${docId}`;
            const lastReload = Number(window.sessionStorage.getItem(reloadKey) ?? 0);
            if (Date.now() - lastReload > 30_000) {
              window.sessionStorage.setItem(reloadKey, String(Date.now()));
              window.location.reload();
            } else {
              console.warn(`Skipping reload for ${docId}: already reloaded in the last 30s`);
            }
          })();
          return;
        }

        // Determine if this is a manual disconnect or a permanent error
        const isManualDisconnect = shouldConnect === false;

        const error: CollaborationError = {
          type: "forced-close",
          code: closeCode || 0,
          message: isManualDisconnect ? "Manually disconnected" : "Server forced connection close",
        };
        const newStage = { kind: "disconnected" as const, error };
        stageRef.current = newStage;
        setStage(newStage);

        retryCountRef.current = 0;
        forcedCloseSignalRef.current = false;

        // Only pause if it's a real forced close (not manual disconnect)
        // Manual disconnect leaves it as is (shouldConnect=false already set if manual)
        if (!isManualDisconnect) {
          pauseProvider();
        }
      } else {
        // Transient connection loss: attempt reconnection
        retryCountRef.current++;

        if (retryCountRef.current >= DEFAULT_MAX_RETRIES) {
          // Exceeded max retry attempts
          const error: CollaborationError = {
            type: "max-retries",
            message: `Failed to connect after ${DEFAULT_MAX_RETRIES} attempts`,
          };
          const newStage = { kind: "disconnected" as const, error };
          stageRef.current = newStage;
          setStage(newStage);

          pauseProvider();
        } else {
          // Still have retries left, move to reconnecting
          const newStage = { kind: "reconnecting" as const, attempt: retryCountRef.current };
          stageRef.current = newStage;
          setStage(newStage);
        }
      }
    };

    provider.on("close", handleClose);

    setYjsSession({ provider, ydoc: provider.document });

    // Handle page visibility changes (sleep/wake, tab switching)
    const handleVisibilityChange = (event?: Event) => {
      if (isDisposedRef.current) return;

      const isVisible = document.visibilityState === "visible";
      const isFocus = event?.type === "focus";

      if (isVisible || isFocus) {
        // Throttle reconnection attempts to avoid double-firing (visibility + focus)
        const now = Date.now();
        if (now - lastReconnectTimeRef.current < 1000) {
          return;
        }

        const wsProvider = provider.configuration.websocketProvider;
        if (!wsProvider) return;

        const ws = wsProvider.webSocket;
        const isStale = ws?.readyState === WebSocket.CLOSED || ws?.readyState === WebSocket.CLOSING;

        // If disconnected or stale, re-enable reconnection and force reconnect
        if (isStale || stageRef.current.kind === "disconnected") {
          lastReconnectTimeRef.current = now;

          // Re-enable connection on tab focus (even if manually disconnected before sleep)
          wsProvider.shouldConnect = true;

          // Reset retry count for fresh reconnection attempt
          retryCountRef.current = 0;

          // Move to connecting state
          const newStage = { kind: "connecting" as const };
          stageRef.current = newStage;
          setStage(newStage);

          wsProvider.disconnect();
          wsProvider.connect();
        }
      }
    };

    // Handle online/offline events
    const handleOnline = () => {
      if (isDisposedRef.current) return;

      const wsProvider = provider.configuration.websocketProvider;
      if (wsProvider) {
        wsProvider.shouldConnect = true;
        wsProvider.disconnect();
        wsProvider.connect();
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("focus", handleVisibilityChange);
    window.addEventListener("online", handleOnline);

    return () => {
      try {
        provider.off("close", handleClose);
      } catch (error) {
        console.error(`Error unregistering close handler:`, error);
      }

      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("focus", handleVisibilityChange);
      window.removeEventListener("online", handleOnline);

      permanentlyStopProvider();
    };
  }, [docId, serverUrl, authToken, sessionEpoch]);

  // IndexedDB persistence lifecycle
  useEffect(() => {
    if (!yjsSession) return;

    let idbPersistence: IndexeddbPersistence | null = null;
    let cancelled = false;

    const setupPersistence = async () => {
      // A content-replaced close means the stored cache holds the old
      // document: clear it BEFORE binding so the stale state cannot merge
      // into the fresh Y.Doc (yjs takes the union, which would duplicate
      // the whole page).
      if (clearCacheOnNextSessionRef.current) {
        clearCacheOnNextSessionRef.current = false;
        const cleaner = new IndexeddbPersistence(docId, new Y.Doc());
        try {
          await cleaner.clearData();
        } catch (error) {
          console.error(`Error clearing stale IndexedDB cache for ${docId}:`, error);
        }
        cleaner.destroy();
      }

      if (cancelled) return;

      idbPersistence = new IndexeddbPersistence(docId, yjsSession.provider.document);

      const onIdbSynced = () => {
        const yFragment = idbPersistence!.doc.getXmlFragment("default");
        const docLength = yFragment?.length ?? 0;
        setIsCacheReady(true);
        setHasCachedContent(docLength > 0);
      };

      idbPersistence.on("synced", onIdbSynced);
    };

    setupPersistence();

    return () => {
      cancelled = true;
      if (idbPersistence) {
        try {
          idbPersistence.destroy();
        } catch (error) {
          console.error(`Error destroying local provider:`, error);
        }
      }
    };
  }, [docId, yjsSession, sessionEpoch]);

  // Observe Y.Doc content changes to update hasCachedContent (catches fallback scenario)
  useEffect(() => {
    if (!yjsSession || !isCacheReady) return;

    const fragment = yjsSession.ydoc.getXmlFragment("default");
    let lastHasContent = false;

    const updateCachedContentFlag = () => {
      const len = fragment?.length ?? 0;
      const hasContent = len > 0;

      // Only update state if the boolean value actually changed
      if (hasContent !== lastHasContent) {
        lastHasContent = hasContent;
        setHasCachedContent(hasContent);
      }
    };
    // Initial check (handles fallback content loaded before this effect runs)
    updateCachedContentFlag();

    // Use observeDeep to catch nested changes (keystrokes modify Y.XmlText inside Y.XmlElement)
    fragment.observeDeep(updateCachedContentFlag);

    return () => {
      try {
        fragment.unobserveDeep(updateCachedContentFlag);
      } catch (error) {
        console.error("Error unobserving fragment:", error);
      }
    };
  }, [yjsSession, isCacheReady]);

  // Manage the draft side doc: create one whenever a non-null `draftHtml`
  // arrives, drop it when the draft is cleared (publish landed, draft
  // discarded). The side doc is local — no Hocuspocus provider is bound
  // to it — so the user's unpublished body never reaches the live server
  // or the database. The collaborative editor binds to whichever doc the
  // hook currently exposes via `activeDocument` and is recreated via a
  // `key` change whenever the identity flips. The side doc's identity
  // comes from the draft hash, not the Y.Doc guid, so a content change
  // while a draft is active still re-binds (a non-null hash but a
  // different one is a fresh side doc).
  const draftIdentity = draftHtml ? hashDraft(draftHtml) : null;
  useEffect(() => {
    if (!yjsSession) return;
    if (!draftIdentity) {
      if (draftSideDoc !== null) {
        activeDraftIdentityRef.current = null;
        setDraftSideDoc(null);
      }
      return;
    }
    if (draftSideDoc && activeDraftIdentityRef.current === draftIdentity) {
      return;
    }
    const sideDoc = createDraftSideDoc(yjsSession.ydoc, draftHtml!);
    if (!sideDoc) {
      console.error(`Could not create draft side doc for ${docId}`);
      activeDraftIdentityRef.current = null;
      setDraftSideDoc(null);
      return;
    }
    activeDraftIdentityRef.current = draftIdentity;
    setDraftSideDoc(sideDoc);
  }, [draftIdentity, yjsSession, docId, draftHtml, draftSideDoc]);

  // Tear down the side doc on unmount so the next session does not see a
  // stale instance.
  useEffect(() => {
    return () => {
      activeDraftIdentityRef.current = null;
      setDraftSideDoc(null);
    };
  }, []);

  // Refuse a locally cached document that no longer descends from the served
  // revision (PLANE-76 ballooning defense). The cache writes the served
  // stamp on every save; on a later visit, if the stamp we have locally
  // does not match the stamp the API just served, the cache is from an
  // earlier revision and a merge would duplicate every block. Clear the
  // cache before the next session binds to it.
  useEffect(() => {
    if (!yjsSession) return;
    const stored = readDocumentStamp(docId);
    if (!shouldResetLocalDocument(stored, contentStamp)) return;
    clearCacheOnNextSessionRef.current = true;
  }, [contentStamp, yjsSession, docId]);

  // Notify state changes callback (use ref to avoid dependency on handler)
  const stateChangeCallbackRef = useRef(onStateChange);
  stateChangeCallbackRef.current = onStateChange;

  useEffect(() => {
    if (!stateChangeCallbackRef.current) return;

    const isServerSynced = stage.kind === "synced";
    const isServerDisconnected = stage.kind === "disconnected";

    const state: CollaborationState = {
      stage,
      isServerSynced,
      isServerDisconnected,
    };

    stateChangeCallbackRef.current(state);
  }, [stage]);

  // Derived values for convenience
  const isServerSynced = stage.kind === "synced";
  const isServerDisconnected = stage.kind === "disconnected";
  const isDocReady = isServerSynced || isServerDisconnected || (isCacheReady && hasCachedContent);

  const signalForcedClose = useCallback((value: boolean) => {
    forcedCloseSignalRef.current = value;
  }, []);

  // Don't return anything until provider is ready - guarantees non-null provider
  if (!yjsSession) {
    return null;
  }

  return {
    provider: yjsSession.provider,
    ydoc: yjsSession.ydoc,
    /**
     * The doc the editor should bind to. The side doc when a draft is
     * active, `provider.document` otherwise. The collaborative editor
     * re-binds whenever the identity here changes (via `key`).
     */
    activeDocument: draftSideDoc ?? yjsSession.ydoc,
    /**
     * True while a draft body is being edited on a side doc. The page UI
     * uses this to know whether the next save is publishing a draft (a
     * `save_source: "editor"` write, with the side doc as the source) or
     * an ordinary edit to the live doc.
     */
    isDraftSession: draftSideDoc !== null,
    state: {
      stage,
      hasCachedContent,
      isCacheReady,
      isServerSynced,
      isServerDisconnected,
      isDocReady,
    },
    actions: {
      signalForcedClose,
    },
  };
};
