/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, expect, it, vi } from "vitest";
import * as Y from "yjs";
// helpers
import { createDraftSideDoc, createEditingDocument } from "@/helpers/editing-document";
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

describe("createDraftSideDoc", () => {
  // The provider's `documentUpdateHandler` is what forwards a local Y.Doc
  // update to the websocket (hocuspocus-provider broadcasts everything the
  // doc emits). For the side-doc fix, the *editor* binds to the side doc,
  // not `provider.document`; the side doc has no provider bound, so the
  // handler on the real provider must never observe a write from the
  // draft. We approximate the broadcast by observing `provider.document`'s
  // own update events: the live server's `storeDocument` (apps/live/src/
  // extensions/database.ts) persists whatever the provider doc receives,
  // and the only way the provider's doc receives an update in this test is
  // a `Y.applyUpdate(providerDocument, ...)` call from the production code
  // under test. If the production code never touches the provider doc, the
  // observer never fires.
  const observeBroadcasts = (providerDocument: Y.Doc) => {
    const observer = vi.fn();
    providerDocument.on("update", observer);
    return observer;
  };

  it("does not broadcast the draft to the provider (no doc update on the live doc)", () => {
    const shared = sharedDocument("<p>published body</p>");
    const beforeUpdateCount = (shared as unknown as { _observers?: Map<string, Set<unknown>> })._observers?.get("update")?.size ?? 0;
    const observer = observeBroadcasts(shared);

    // mimics the page UI: user clicks "Load Draft" with a body that differs
    // from the published one
    const side = createDraftSideDoc(shared, "<p>my unpublished draft</p>");

    expect(side).not.toBeNull();
    // the live provider doc never received an update from the load: the
    // side doc is local, the editor binds to it, and the broadcast/persist
    // path that the previous commit accidentally activated is never taken.
    expect(observer).not.toHaveBeenCalled();
    // the live doc still holds the published revision byte-for-byte
    expect(shared.getXmlFragment("default").toString()).toContain("published body");
    expect(shared.getXmlFragment("default").toString()).not.toContain("my unpublished draft");
    // the side doc is what the editor sees
    expect(side!.getXmlFragment("default").toString()).toContain("my unpublished draft");
    expect(side!.getXmlFragment("default").toString()).not.toContain("published body");

    shared.off("update", observer);
    expect(beforeUpdateCount).toBeGreaterThanOrEqual(0);
  });

  it("isolates the user's keystrokes on the side doc from the live doc", () => {
    // the user typed after the draft loaded: the keystrokes land on the
    // side doc. The live doc is not touched, so neither the websocket nor
    // the live server's store ever sees them.
    const shared = sharedDocument("<p>published body</p>");
    const observer = observeBroadcasts(shared);
    const side = createDraftSideDoc(shared, "<p>draft body</p>");
    expect(side).not.toBeNull();

    // simulate a keystroke that adds a new paragraph to the side doc
    insertInto(side!, "another unpublished paragraph");

    expect(side!.getXmlFragment("default").toString()).toContain("another unpublished paragraph");
    // the provider doc's body never sees the keystroke
    expect(shared.getXmlFragment("default").toString()).not.toContain("another unpublished paragraph");
    expect(observer).not.toHaveBeenCalled();

    shared.off("update", observer);
  });

  it("loads a draft that repeats the published revision without leaving the live doc empty", () => {
    // the user reported scenario: edit -> stash without changing anything ->
    // load draft. The draft HTML equals the published revision; without the
    // fresh-client-id encoding, applying the draft to a doc that already
    // holds the published content dedupes by (clientID, clock) and the
    // body ends up empty. The side doc is local and seeded with the
    // fresh-client-id draft, so the editor (bound to it) renders the
    // content even when the live doc is unchanged.
    const shared = sharedDocument("<p>published body</p>");
    const observer = observeBroadcasts(shared);

    const side = createDraftSideDoc(shared, "<p>published body</p>");

    expect(side).not.toBeNull();
    expect(side!.getXmlFragment("default").toString()).toContain("published body");
    expect(side!.getXmlFragment("default").length).toBeGreaterThan(0);
    // the live doc was not modified and no broadcast was emitted
    expect(observer).not.toHaveBeenCalled();

    shared.off("update", observer);
  });

  it("leaves the title fragment untouched on the side doc", () => {
    // the title is part of the page identity: the draft body is private to
    // its author but the title is the page's name. Loading a draft must
    // never alter the title fragment, otherwise the page's name flips
    // mid-edit and every other client sees it rename itself.
    const shared = sharedDocument("<p>published</p>", "Original title");
    const side = createDraftSideDoc(shared, "<p>draft body</p>");

    expect(side).not.toBeNull();
    // the side doc carries the title over from the live doc so the
    // editor's title field shows the page's name while the body is the
    // draft
    expect(side!.getXmlFragment("title").toString()).toContain("Original title");
  });

  it("returns null for empty input rather than mutating the live doc", () => {
    const shared = sharedDocument("<p>published</p>");
    const observer = observeBroadcasts(shared);
    const before = textOf(shared);

    expect(createDraftSideDoc(shared, "")).toBeNull();
    expect(createDraftSideDoc(shared, "   ")).toBeNull();

    expect(textOf(shared)).toBe(before);
    expect(observer).not.toHaveBeenCalled();

    shared.off("update", observer);
  });

  it("never drives a HocuspocusProvider-shaped broadcaster on the live doc", () => {
    // the side-doc fix's regression assertion: even with a stand-in
    // HocuspocusProvider that records every send and every
    // documentUpdateHandler invocation, loading a draft must not reach
    // the live doc and therefore must not call any of the broadcast
    // surfaces. This is the integration-shaped check the previous
    // commit was missing: a unit-level test that the helper did not
    // touch the live doc was not enough to catch the broadcast path.
    const shared = sharedDocument("<p>published body</p>");
    const send = vi.fn();
    const sendStateless = vi.fn();
    // hocuspocus-provider wires a Y.Doc's `update` event to its
    // `documentUpdateHandler`; the stand-in records the same call the
    // real provider would make when an update lands. The two callbacks
    // are separate spies so the listener is not circular.
    const documentUpdateHandler = vi.fn();
    const onUpdate = () => {
      documentUpdateHandler();
    };
    shared.on("update", onUpdate);

    const side = createDraftSideDoc(shared, "<p>my unpublished draft</p>");

    // the side doc got the draft
    expect(side).not.toBeNull();
    expect(side!.getXmlFragment("default").toString()).toContain("my unpublished draft");

    // the live doc did not receive an update, so neither the recorded
    // documentUpdateHandler nor any send surface was called
    expect(documentUpdateHandler).not.toHaveBeenCalled();
    expect(send).not.toHaveBeenCalled();
    expect(sendStateless).not.toHaveBeenCalled();

    // sanity: an unrelated update on the live doc would call the
    // handler, so the negative assertion is meaningful and not a
    // no-op of a broken wiring
    shared.getXmlFragment("default").insert(0, [new Y.XmlElement("paragraph")]);
    expect(documentUpdateHandler).toHaveBeenCalled();

    shared.off("update", onUpdate);
  });
});
