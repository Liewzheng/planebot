/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useEffect, useState } from "react";
import type { EditorRefApi, CollaborationState } from "@plane/editor";
// plane editor
import { getBinaryDataFromDocumentEditorHTMLString } from "@plane/editor";
// hooks
import type { TPageInstance } from "@/store/pages/base-page";

type TArgs = {
  editorRef: React.RefObject<EditorRefApi | null>;
  fetchPageDescription: () => Promise<ArrayBuffer>;
  collaborationState: CollaborationState | null;
  page: TPageInstance;
  /**
   * When false (e.g. the page is open in reading mode), the document is not
   * seeded from the API copy.
   */
  enabled?: boolean;
};

/**
 * Reads a page while the collaborative session is unavailable.
 *
 * The document usually arrives over the websocket, so an offline client has no
 * content at all: the editor renders an empty page even though the page details
 * (and their html) were fetched over HTTP. When the connection is down and the
 * document is empty, it is seeded from the API copy instead.
 *
 * This used to also write the local document back through the API (on every
 * disconnect and on a 30s timer). That write pushed a stale local copy over
 * content written elsewhere and invented revisions nobody made — PLANE-76. Page
 * content is now saved only on demand (see `usePageSave`); an edit made offline
 * is kept as a local draft until it is saved.
 */
export const usePageFallback = (args: TArgs) => {
  const { editorRef, fetchPageDescription, collaborationState, page, enabled = true } = args;

  const [isFetchingFallbackBinary, setIsFetchingFallbackBinary] = useState(false);

  // Derive connection failure from collaboration state
  const hasConnectionFailed = collaborationState?.stage.kind === "disconnected";

  const seedDocumentFromAPI = useCallback(async () => {
    if (!enabled) return;
    if (!hasConnectionFailed) return;
    const editor = editorRef.current;
    if (!editor) return;
    // an active IME composition owns the editor DOM: a document write now
    // would make the input method commit its raw text. The seed only matters
    // while the document is empty, so skipping one attempt is harmless — the
    // next stage change retries it.
    if (editor.isComposing()) return;

    try {
      setIsFetchingFallbackBinary(true);

      const latestEncodedDescription = await fetchPageDescription();
      let latestDecodedDescription: Uint8Array;
      if (latestEncodedDescription && latestEncodedDescription.byteLength > 0) {
        latestDecodedDescription = new Uint8Array(latestEncodedDescription);
      } else {
        const pageDescriptionHtml = page.description_html;
        latestDecodedDescription = getBinaryDataFromDocumentEditorHTMLString(
          pageDescriptionHtml ?? "<p></p>",
          page.name
        );
      }

      // Only seed an empty document: setProviderDocument applies a Yjs update
      // (merge), so seeding one that already has content duplicates the whole
      // page. Emptiness is tested on the Y.Doc — the editor's JSON can read as
      // empty while the document holds content (a render failure, or a client
      // that has not rendered it yet), and the seed then merges a second copy
      // of the page in.
      if (editor.isDocumentEmpty()) {
        editor.setProviderDocument(latestDecodedDescription);
      }
    } catch (error) {
      console.error(error);
    } finally {
      setIsFetchingFallbackBinary(false);
    }
  }, [editorRef, fetchPageDescription, page.description_html, page.name, enabled, hasConnectionFailed]);

  useEffect(() => {
    if (hasConnectionFailed) {
      seedDocumentFromAPI();
    }
  }, [seedDocumentFromAPI, hasConnectionFailed]);

  return { isFetchingFallbackBinary };
};
