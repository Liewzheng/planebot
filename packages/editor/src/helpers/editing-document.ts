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
