/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { getSchema } from "@tiptap/core";
import { StarterKit } from "@tiptap/starter-kit";
import { EditorState, TextSelection } from "@tiptap/pm/state";
import type { Node as ProseMirrorNode } from "@tiptap/pm/model";
import { describe, expect, it } from "vitest";
// extensions
import { MathInlineExtension } from "../extension";

const schema = getSchema([StarterKit, MathInlineExtension]);
const mathInlineType = schema.nodes.mathInline;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const plugins = MathInlineExtension.config.addProseMirrorPlugins!.call({ type: mathInlineType } as any);
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const inputRules = MathInlineExtension.config.addInputRules!.call({ type: mathInlineType } as any);

const createState = (doc?: ProseMirrorNode) => EditorState.create({ schema, plugins, doc });

const findMathInlineNodes = (doc: ProseMirrorNode) => {
  const nodes: ProseMirrorNode[] = [];
  doc.descendants((node) => {
    if (node.type === mathInlineType) nodes.push(node);
    return true;
  });
  return nodes;
};

describe("mathInline text conversion (appendTransaction)", () => {
  it("converts a span with text on both sides and preserves the surrounding text", () => {
    let state = createState();
    state = state.apply(state.tr.insertText("混排测试 $x+1$ 尾部文字"));
    const nodes = findMathInlineNodes(state.doc);
    expect(nodes).toHaveLength(1);
    expect(nodes[0].attrs["data-latex"]).toBe("x+1");
    // the text before and after the converted span must survive
    expect(state.doc.textContent).toBe("混排测试  尾部文字");
    const paragraph = state.doc.firstChild!;
    expect(paragraph.child(0).text).toBe("混排测试 ");
    expect(paragraph.child(2).text).toBe(" 尾部文字");
  });

  it("converts content typed between two existing $ delimiters once the cursor leaves the span", () => {
    let state = createState();
    state = state.apply(state.tr.insertText("$$"));
    expect(findMathInlineNodes(state.doc)).toHaveLength(0);
    // cursor between the two dollars, type the content
    state = state.apply(state.tr.setSelection(TextSelection.create(state.doc, 2)));
    state = state.apply(state.tr.insertText("x+1"));
    // still typing inside the span — no conversion yet
    expect(findMathInlineNodes(state.doc)).toHaveLength(0);
    expect(state.doc.textContent).toBe("$x+1$");
    // moving the cursor out of the span converts it
    state = state.apply(state.tr.setSelection(TextSelection.create(state.doc, 6)));
    const nodes = findMathInlineNodes(state.doc);
    expect(nodes).toHaveLength(1);
    expect(nodes[0].attrs["data-latex"]).toBe("x+1");
  });

  it("does not convert currency-like text", () => {
    let state = createState();
    state = state.apply(state.tr.insertText("pay $100$ now"));
    expect(findMathInlineNodes(state.doc)).toHaveLength(0);
    expect(state.doc.textContent).toBe("pay $100$ now");
  });

  it("does not convert text carrying a code mark", () => {
    const doc = schema.node("doc", null, [
      schema.node("paragraph", null, [schema.text("$x$", [schema.marks.code.create()])]),
    ]);
    let state = createState(doc);
    state = state.apply(state.tr.setSelection(TextSelection.create(state.doc, 4)));
    expect(findMathInlineNodes(state.doc)).toHaveLength(0);
    expect(state.doc.textContent).toBe("$x$");
  });

  it("does not convert text inside a code block", () => {
    const doc = schema.node("doc", null, [schema.node("codeBlock", null, [schema.text("$x$")])]);
    let state = createState(doc);
    state = state.apply(state.tr.setSelection(TextSelection.create(state.doc, 4)));
    expect(findMathInlineNodes(state.doc)).toHaveLength(0);
    expect(state.doc.textContent).toBe("$x$");
  });
});

describe("mathInline input rule handler", () => {
  const runHandler = (state: EditorState, range: { from: number; to: number }, matched: string, latex: string) => {
    const match = Object.assign([matched, latex], { index: 0, input: matched }) as unknown as RegExpMatchArray;
    // pin a single transaction: tiptap passes a chainable state whose .tr is
    // stable, while the raw EditorState.tr getter creates a new one per access
    const tr = state.tr;
    const shim = Object.create(state);
    Object.defineProperty(shim, "tr", { get: () => tr });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    inputRules[0].handler({ state: shim, range, match } as any);
    return tr;
  };

  it("converts when the closing $ is typed right after the content", () => {
    // doc contains `$x+1`, the typed closing `$` is not in the doc yet
    const state = createState(schema.node("doc", null, [schema.node("paragraph", null, [schema.text("$x+1")])]));
    const tr = runHandler(state, { from: 1, to: 5 }, "$x+1$", "x+1");
    const nodes = findMathInlineNodes(tr.doc);
    expect(nodes).toHaveLength(1);
    expect(nodes[0].attrs["data-latex"]).toBe("x+1");
  });

  it("does not throw or touch the doc for an inverted range (multi-char insert regression)", () => {
    const state = createState();
    let tr;
    expect(() => {
      tr = runHandler(state, { from: 11, to: 1 }, "$x+1$", "x+1");
    }).not.toThrow();
    expect(tr!.steps).toHaveLength(0);
  });

  it("does not throw or touch the doc for an out-of-document range", () => {
    const state = createState();
    let tr;
    expect(() => {
      tr = runHandler(state, { from: 1, to: 100 }, "$x+1$", "x+1");
    }).not.toThrow();
    expect(tr!.steps).toHaveLength(0);
  });

  it("does not convert when the range covers unrelated text", () => {
    const state = createState(schema.node("doc", null, [schema.node("paragraph", null, [schema.text("hello")])]));
    const tr = runHandler(state, { from: 1, to: 5 }, "$x+1$", "x+1");
    expect(findMathInlineNodes(tr.doc)).toHaveLength(0);
    expect(tr.doc.textContent).toBe("hello");
  });

  it("keeps the $$ block guard: a preceding $ blocks the conversion", () => {
    // doc contains `$$x`, the typed closing `$` is not in the doc yet
    const state = createState(schema.node("doc", null, [schema.node("paragraph", null, [schema.text("$$x")])]));
    const tr = runHandler(state, { from: 2, to: 4 }, "$x$", "x");
    expect(findMathInlineNodes(tr.doc)).toHaveLength(0);
    expect(tr.doc.textContent).toBe("$$x");
  });
});
