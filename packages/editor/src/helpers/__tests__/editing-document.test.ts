/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, expect, it } from "vitest";
import * as Y from "yjs";
// helpers
import { createEditingDocument } from "@/helpers/editing-document";
import { getBinaryDataFromDocumentEditorHTMLString } from "@/helpers/yjs-utils";

const textOf = (document: Y.Doc) => document.getXmlFragment("default").toString();

const sharedDocument = (html: string, title = "Published title") => {
  const document = new Y.Doc();
  Y.applyUpdate(document, getBinaryDataFromDocumentEditorHTMLString(html, title));
  return document;
};

const insertInto = (document: Y.Doc, text: string) => {
  const body = document.getXmlFragment("default");
  const paragraph = new Y.XmlElement("paragraph");
  paragraph.insert(0, [new Y.XmlText(text)]);
  document.transact(() => {
    const last = body.get(body.length - 1);
    if (last instanceof Y.XmlElement) last.insert(last.length, [paragraph]);
    else body.insert(body.length, [paragraph]);
  });
};

describe("createEditingDocument", () => {
  it("starts the editing session from the published revision", () => {
    const shared = sharedDocument("<p>published body</p>");
    const editing = createEditingDocument(shared);

    expect(textOf(editing)).toBe(textOf(shared));
    expect(editing.getXmlFragment("title").length).toBeGreaterThan(0);
  });

  it("keeps typing local", () => {
    const shared = sharedDocument("<p>published body</p>");
    const before = textOf(shared);
    const editing = createEditingDocument(shared);

    insertInto(editing, "unpublished work");

    expect(textOf(editing)).toContain("unpublished work");
    // the shared document is untouched: nothing reaches other readers
    expect(textOf(shared)).toBe(before);
  });

  it("starts from a draft without touching the shared document", () => {
    const shared = sharedDocument("<p>published body</p>");
    const before = textOf(shared);

    const editing = createEditingDocument(shared, "<p>my unpublished draft</p>");

    expect(textOf(editing)).toContain("my unpublished draft");
    expect(textOf(editing)).not.toContain("published body");
    // the title survives loading a body draft
    expect(editing.getXmlFragment("title").length).toBeGreaterThan(0);
    expect(textOf(shared)).toBe(before);
  });

  it("loads a draft that repeats the published revision", () => {
    // the user edited a page, changed nothing, and stashed it: the draft is the
    // published revision, down to the ProseMirror JSON it converts back to
    const shared = sharedDocument("<p>published body</p>");
    const before = textOf(shared);

    const editing = createEditingDocument(shared, "<p>published body</p>");

    expect(textOf(editing)).toBe(before);
    expect(editing.getXmlFragment("title").length).toBeGreaterThan(0);
    expect(textOf(shared)).toBe(before);
  });

  it("loads a draft stored in the editor's own html", () => {
    const shared = sharedDocument("<p>published body</p>");
    // what `editor.getDocument().html` writes: block ids and classes the
    // conversion drops, so the draft still repeats the published revision
    const draftHtml = '<p class="editor-paragraph-block" data-id="8a0f2d3c">published body</p>';

    const editing = createEditingDocument(shared, draftHtml);

    expect(textOf(editing)).toContain("published body");
  });

  it("is a different document every time", () => {
    const shared = sharedDocument("<p>published body</p>");

    expect(createEditingDocument(shared).guid).not.toBe(createEditingDocument(shared).guid);
  });
});
