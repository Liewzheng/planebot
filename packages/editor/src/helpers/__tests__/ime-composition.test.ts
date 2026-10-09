/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * IME composition in the collaborative document editor.
 *
 * While an IME composition is active the browser owns the editor DOM and
 * ProseMirror deliberately ignores it. Any external document change applied
 * mid-composition (a value sync, a draft reset, a fallback seed) re-renders
 * that DOM out from under the input method, and the input method then commits
 * both the raw composition text (the pinyin) and the chosen characters — the
 * page ends up holding "nihao你好". These tests pin the two sides of the
 * contract:
 *
 * - a normal composition round-trip puts exactly the chosen text in the
 *   document (nothing of the composing text leaks in), and
 * - `runOutsideComposition` defers external writes until `compositionend`, so
 *   the write and the composition can never race.
 */

// @vitest-environment jsdom

import { Editor } from "@tiptap/core";
import Collaboration from "@tiptap/extension-collaboration";
import StarterKit from "@tiptap/starter-kit";
import { afterEach, describe, expect, it } from "vitest";
import * as Y from "yjs";
// helpers
import { runOutsideComposition } from "@/helpers/composition";
import { getBinaryDataFromDocumentEditorHTMLString } from "@/helpers/yjs-utils";

const editors: Editor[] = [];

// jsdom has no layout: ProseMirror measures text rectangles when scrolling
// the selection into view. jsdom's stubs throw; replace them with empty
// rectangles on every node kind ProseMirror may measure.
const emptyRects = (): DOMRectList => [] as unknown as DOMRectList;
const emptyRect = () =>
  ({ x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({}) }) as DOMRect;

if (typeof Node !== "undefined") {
  Object.defineProperty(Node.prototype, "getClientRects", { value: emptyRects, writable: true, configurable: true });
  Object.defineProperty(Node.prototype, "getBoundingClientRect", { value: emptyRect, writable: true, configurable: true });
}
if (typeof Range !== "undefined") {
  Object.defineProperty(Range.prototype, "getClientRects", { value: emptyRects, writable: true, configurable: true });
  Object.defineProperty(Range.prototype, "getBoundingClientRect", { value: emptyRect, writable: true, configurable: true });
}

const createCollaborativeEditor = (html: string): { editor: Editor; ydoc: Y.Doc } => {
  const ydoc = new Y.Doc();
  Y.applyUpdate(ydoc, getBinaryDataFromDocumentEditorHTMLString(html));

  // each editor gets its own element: ProseMirror's keyed plugins cannot be
  // instantiated twice against the same host
  const element = document.createElement("div");
  document.body.appendChild(element);

  const editor = new Editor({
    element,
    extensions: [
      StarterKit.configure({ history: false }),
      Collaboration.configure({
        document: ydoc,
        field: "default",
      }),
    ],
  });
  editors.push(editor);
  return { editor, ydoc };
};

/** The visible text of the editor's document, ignoring markup. */
const docText = (editor: Editor): string => editor.state.doc.textBetween(0, editor.state.doc.content.size, "\n");

/** The body text of the bound Y.Doc, ignoring markup. */
const ydocText = (ydoc: Y.Doc): string => {
  const fragment = ydoc.getXmlFragment("default");
  let text = "";
  fragment.forEach((child) => {
    if (child instanceof Y.XmlText) text += child.toString();
    else if (child instanceof Y.XmlElement) {
      const walk = (node: Y.XmlElement | Y.XmlFragment): string => {
        let out = "";
        node.forEach((grandchild) => {
          if (grandchild instanceof Y.XmlText) out += grandchild.toString();
          else if (grandchild instanceof Y.XmlElement) out += walk(grandchild);
        });
        return out;
      };
      text += walk(child);
    }
  });
  return text;
};

/**
 * Simulates the DOM side of an IME round-trip: the composition starts, the
 * input method writes its composing text into the DOM, and before
 * `compositionend` it replaces that text with the chosen characters (Safari's
 * behaviour). ProseMirror must then adopt exactly the chosen characters.
 */
const composeInto = async (editor: Editor, composingText: string, committedText: string): Promise<void> => {
  const { view } = editor;
  const { from } = editor.state.selection;

  view.dom.dispatchEvent(new Event("compositionstart", { bubbles: true }));

  // the input method writes its composing text at the caret. The offset comes
  // from the ProseMirror position (jsdom's DOM ranges are unreliable): the
  // caret position minus the start of its text block.
  const $from = editor.state.doc.resolve(from);
  const insertAt = from - $from.start();
  const paragraph = view.dom.querySelector("p");
  const textNode: Text = paragraph?.firstChild as Text;
  textNode.textContent =
    (textNode.textContent ?? "").slice(0, insertAt) + composingText + (textNode.textContent ?? "").slice(insertAt);
  editor.view.dom.dispatchEvent(new InputEvent("input", { bubbles: true, data: composingText }));

  // the candidate is chosen: the composing text is replaced by the committed
  // one, in place (the surrounding text stays)
  const current = textNode.textContent ?? "";
  textNode.textContent = current.slice(0, insertAt) + committedText + current.slice(insertAt + composingText.length);

  view.dom.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true, data: committedText }));

  // ProseMirror commits the composition on a short timer (it waits out
  // Safari's trailing input events before reading the DOM); wait for it, then
  // restore the caret after the committed text — jsdom does not maintain DOM
  // selections, so the ProseMirror selection has to be moved explicitly
  await new Promise((resolve) => setTimeout(resolve, 40));
  editor.chain().setTextSelection(from + committedText.length).run();
};

const frame = () => new Promise((resolve) => setTimeout(resolve, 20));

afterEach(() => {
  for (const editor of editors.splice(0)) {
    if (!editor.isDestroyed) editor.destroy();
  }
});

describe("IME composition in the collaborative editor", () => {
  it("commits exactly the chosen text, not the composing text", async () => {
    const { editor, ydoc } = createCollaborativeEditor("<p>开头。</p>");
    editor.commands.focus("end", { scrollIntoView: false });

    await composeInto(editor, "nihao", "你好");
    await frame();

    expect(docText(editor)).toBe("开头。你好");
    expect(docText(editor)).not.toContain("nihao");
    expect(ydocText(ydoc)).toBe("开头。你好");
  });

  it("keeps sequential compositions intact", async () => {
    const { editor, ydoc } = createCollaborativeEditor("<p>开头。</p>");
    editor.commands.focus("end", { scrollIntoView: false });

    await composeInto(editor, "ni", "你");
    await composeInto(editor, "hao", "好");
    await frame();

    expect(docText(editor)).toBe("开头。你好");
    expect(ydocText(ydoc)).toBe("开头。你好");
  });

  it("defers an external write until the composition has ended", async () => {
    const { editor, ydoc } = createCollaborativeEditor("<p>开头。</p>");
    editor.commands.focus("end", { scrollIntoView: false });

    editor.view.dom.dispatchEvent(new Event("compositionstart", { bubbles: true }));
    expect(editor.view.composing).toBe(true);

    let applied = false;
    runOutsideComposition(editor, () => {
      applied = true;
      editor.commands.setContent("<p>替换内容。</p>");
    });

    // the write must not run while the composition owns the DOM
    expect(applied).toBe(false);
    expect(docText(editor)).toBe("开头。");

    // ending the composition releases the write on the next frame
    editor.view.dom.dispatchEvent(new Event("compositionend", { bubbles: true }));
    await frame();
    await frame();

    expect(applied).toBe(true);
    expect(docText(editor)).toBe("替换内容。");
    expect(ydocText(ydoc)).toBe("替换内容。");
  });
});
