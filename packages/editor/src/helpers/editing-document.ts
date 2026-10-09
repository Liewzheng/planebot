/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import * as Y from "yjs";
// helpers
import { getBinaryDataFromDocumentEditorHTMLStringAsNewClient } from "@/helpers/yjs-utils";

/**
 * The document an editor works on while a page is being edited.
 *
 * It starts as a copy of the shared (published) document and is left alone
 * afterwards: keystrokes land here, so nothing reaches the live server or any
 * other reader until the page is published. Editing the shared document instead
 * is what let one client's unpublished state merge into the published one and
 * duplicate a page (PLANE-76/78).
 *
 * `draftHtml` starts the session from an unpublished revision — the author's own
 * draft — and replaces the body: the copy carries the title fragment over, so
 * the page keeps its name while the draft body is loaded.
 */
export const createEditingDocument = (sharedDocument: Y.Doc, draftHtml?: string | null): Y.Doc => {
  const document = new Y.Doc();
  // continue from the published revision: applying it to an empty document
  // cannot duplicate anything
  Y.applyUpdate(document, Y.encodeStateAsUpdate(sharedDocument));

  if (!draftHtml) return document;

  const body = document.getXmlFragment("default");
  document.transact(() => {
    if (body.length > 0) body.delete(0, body.length);
  });
  // The draft is authored by a client id of its own, never by the content-derived id a conversion
  // of the published revision carries: a draft that repeats that revision — the user stashed
  // without changing anything — would arrive as structs the copy already integrated, and Yjs drops
  // those, leaving the body empty.
  Y.applyUpdate(document, getBinaryDataFromDocumentEditorHTMLStringAsNewClient(draftHtml));

  return document;
};

/**
 * Where the editor's keystrokes should land for a draft session.
 *
 * A draft body is the author's own unpublished revision (PLANE-77): it must
 * never reach the live server or any other reader until the user publishes.
 * The previous attempt wrote the draft straight into `provider.document` so
 * the editor — bound to that doc — would render the draft, but the
 * Hocuspocus provider broadcasts every update and the live server's
 * `storeDocument` then writes the (still-unpublished) body to the database;
 * a discarded draft would leave a leaked revision behind. Bind the editor
 * to a side doc instead: a local Y.Doc the side helper builds via
 * `createEditingDocument`, which copies the published revision state in and
 * applies the draft with a fresh client id so the user's edits do not
 * deduplicate with the already-synced published content. The side doc is
 * dropped when the draft is discarded or the publish lands; the editor
 * rebinds to `provider.document` for the next session.
 */
export const createDraftSideDoc = (providerDocument: Y.Doc, draftHtml: string): Y.Doc | null => {
  if (!providerDocument) return null;
  // an empty/whitespace-only draft carries no body, so there is nothing
  // for the side doc to back the editor with. Treat it as no draft at
  // all: the editor rebinds to provider.document and the draft flow
  // stays a no-op rather than spinning up a doc that only ever holds the
  // title fragment.
  if (typeof draftHtml !== "string" || draftHtml.trim() === "") return null;
  return createEditingDocument(providerDocument, draftHtml);
};
