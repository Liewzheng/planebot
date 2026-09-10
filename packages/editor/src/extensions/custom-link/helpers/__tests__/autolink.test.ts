/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { Fragment, Schema } from "@tiptap/pm/model";
import type { Plugin, Transaction } from "@tiptap/pm/state";
import { EditorState } from "@tiptap/pm/state";
import { describe, expect, it } from "vitest";
// helpers
import { autolink } from "@/extensions/custom-link/helpers/autolink";
import type { TIssueReferenceConfig } from "@/extensions/custom-link/extension";

// Minimal schema with the marks the autolink plugin knows about.
const schema = new Schema({
  nodes: {
    doc: { content: "block+" },
    paragraph: { group: "block", content: "inline*" },
    text: { group: "inline" },
  },
  marks: {
    link: {
      attrs: { href: { default: null } },
    },
    code: {},
  },
});

const ISSUE_REFERENCE: TIssueReferenceConfig = {
  pattern: /\b[A-Z][A-Z0-9]+-\d+\b/,
  resolve: (identifier: string) => `/plane/browse/${identifier}`,
};

// Simulates typing a trailing space after `text`, then runs the plugin's
// appendTransaction exactly like the editor would.
const runAutolink = (
  text: string,
  options: Omit<Parameters<typeof autolink>[0], "type">,
  markedText?: { text: string; mark: "code" | "link" }
) => {
  const paragraphContent = markedText
    ? Fragment.fromArray([
        schema.text(text),
        schema.text(markedText.text, [
          schema.marks[markedText.mark].create(markedText.mark === "link" ? { href: "/existing" } : {}),
        ]),
      ])
    : schema.text(text);
  const doc = schema.nodes.doc.createAndFill(null, [schema.nodes.paragraph.create(null, paragraphContent)]);
  if (!doc) throw new Error("failed to build doc");
  const oldState = EditorState.create({ schema, doc });
  const endPos = 1 + text.length + (markedText ? markedText.text.length : 0);
  const tr = oldState.tr.insertText(" ", endPos, endPos);
  const newState = oldState.apply(tr);

  const plugin: Plugin = autolink({ type: schema.marks.link, ...options });
  const append = plugin.spec.appendTransaction;
  const appendFn = Array.isArray(append) ? append[0] : append;
  const resultTr = appendFn?.([tr], oldState, newState);

  return {
    resultTr,
    newState,
  };
};

const getLinkHrefs = (state: EditorState) => {
  const hrefs: { text: string; href: string | null }[] = [];
  state.doc.descendants((node) => {
    const linkMark = node.marks.find((mark) => mark.type === schema.marks.link);
    if (node.isText && linkMark) {
      hrefs.push({ text: node.text ?? "", href: (linkMark.attrs.href as string | null) ?? null });
    }
  });
  return hrefs;
};

describe("autolink issue reference branch", () => {
  it("links a work item identifier when a whitespace is typed after it", () => {
    const { resultTr, newState } = runAutolink("see PLANE-1", { issueReference: ISSUE_REFERENCE });

    expect(resultTr).toBeDefined();
    const resultState = newState.apply(resultTr as Transaction);
    expect(getLinkHrefs(resultState)).toEqual([{ text: "PLANE-1", href: "/plane/browse/PLANE-1" }]);
  });

  it("links multiple identifiers in the same word boundary pass", () => {
    const { resultTr, newState } = runAutolink("PLANE-1 and PLANE-2", { issueReference: ISSUE_REFERENCE });

    const resultState = newState.apply(resultTr as Transaction);
    expect(getLinkHrefs(resultState)).toEqual([{ text: "PLANE-2", href: "/plane/browse/PLANE-2" }]);
  });

  it("ignores identifiers inside a code mark", () => {
    const { resultTr } = runAutolink("call ", { issueReference: ISSUE_REFERENCE }, { text: "PLANE-1", mark: "code" });

    expect(resultTr).toBeUndefined();
  });

  it("ignores identifiers that already carry a link mark", () => {
    const { resultTr } = runAutolink("see ", { issueReference: ISSUE_REFERENCE }, { text: "PLANE-1", mark: "link" });

    expect(resultTr).toBeUndefined();
  });

  it("does nothing for identifiers when issueReference is not configured", () => {
    const { resultTr } = runAutolink("see PLANE-1", {});

    expect(resultTr).toBeUndefined();
  });

  it("still autolinks urls when issueReference is configured", () => {
    // mirrors the real option: only accept values that look like http urls
    const validate = (url: string) => /^(https?:\/\/)?[\w-]+(\.[\w-]+)+/.test(url);
    const { resultTr, newState } = runAutolink("visit https://plane.so", { validate, issueReference: ISSUE_REFERENCE });

    const resultState = newState.apply(resultTr as Transaction);
    expect(getLinkHrefs(resultState).length).toBeGreaterThan(0);
  });
});
