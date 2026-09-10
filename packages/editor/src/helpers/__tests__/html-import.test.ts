/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, expect, it } from "vitest";
// helpers
import { convertHTMLDocumentToAllFormats } from "@/helpers/yjs-utils";

/**
 * The public API / CLI can write a page with `description_html` only, produced
 * from plain markdown (see PLANE-32). The live service converts that HTML into
 * the document editor's JSON + Yjs binary through this exact function, so
 * anything the importer drops silently disappears from the web editor.
 *
 * The HTML below is the simplified shape a CLI upload produces: headings, lists,
 * blockquote, a fenced code block with a language, inline marks and a table.
 */
const SIMPLIFIED_HTML = [
  "<h1>标题一</h1>",
  "<h2>标题二</h2>",
  "<h6>标题六</h6>",
  "<p>正文 <strong>加粗</strong> 与 <code>inline code</code> 以及 <em>斜体</em>。</p>",
  "<ul><li>无序项 A</li><li>无序项 B</li></ul>",
  "<ol><li>有序项 1</li><li>有序项 2</li></ol>",
  "<blockquote><p>引用段落</p></blockquote>",
  '<pre><code class="language-bash">echo hello\nls -la</code></pre>',
  "<table><tbody><tr><th>列1</th><th>列2</th></tr><tr><td>a</td><td>b</td></tr></tbody></table>",
  "<hr>",
  "<p>结尾</p>",
].join("");

type TJSONNode = {
  type?: string;
  text?: string;
  attrs?: Record<string, unknown>;
  marks?: { type: string }[];
  content?: TJSONNode[];
};

const convert = () => {
  const payload = convertHTMLDocumentToAllFormats({
    document_html: SIMPLIFIED_HTML,
    variant: "document",
  });
  const json = payload.description_json as TJSONNode;
  const nodes: TJSONNode[] = [];
  const collect = (node: TJSONNode) => {
    nodes.push(node);
    (node.content ?? []).forEach(collect);
  };
  collect(json);
  return {
    html: payload.description_html,
    binary: payload.description_binary,
    nodes,
    types: nodes.map((node) => node.type),
    text: nodes
      .filter((node) => node.type === "text")
      .map((node) => node.text ?? "")
      .join(""),
  };
};

describe("simplified HTML import (API/CLI page content)", () => {
  it("keeps every block type the CLI can produce", () => {
    const { types } = convert();
    expect(types.filter((type) => type === "heading")).toHaveLength(3);
    expect(types).toContain("bulletList");
    expect(types).toContain("orderedList");
    expect(types).toContain("blockquote");
    expect(types).toContain("codeBlock");
    expect(types).toContain("table");
    expect(types).toContain("horizontalRule");
  });

  it("preserves heading levels", () => {
    const { nodes } = convert();
    const levels = nodes.filter((node) => node.type === "heading").map((node) => node.attrs?.level);
    expect(levels).toEqual([1, 2, 6]);
  });

  it("keeps bold, italic and inline code marks", () => {
    const { nodes } = convert();
    const marks = nodes
      .filter((node) => node.type === "text")
      .flatMap((node) => (node.marks ?? []).map((mark) => mark.type));
    expect(marks).toContain("bold");
    expect(marks).toContain("italic");
    expect(marks).toContain("code");
  });

  it("keeps the code block text and its fence language", () => {
    const { nodes } = convert();
    const codeBlock = nodes.find((node) => node.type === "codeBlock");
    expect(codeBlock).toBeDefined();
    expect(codeBlock?.attrs?.language).toBe("bash");
    expect((codeBlock?.content ?? []).map((child) => child.text ?? "").join("")).toBe("echo hello\nls -la");
  });

  it("keeps table headers and body cells", () => {
    const { types } = convert();
    expect(types.filter((type) => type === "tableHeader")).toHaveLength(2);
    expect(types.filter((type) => type === "tableCell")).toHaveLength(2);
  });

  it("keeps the text of every block", () => {
    const { text } = convert();
    for (const expected of [
      "标题一",
      "标题六",
      "加粗",
      "inline code",
      "斜体",
      "无序项 A",
      "有序项 2",
      "引用段落",
      "echo hello",
      "列1",
      "b",
      "结尾",
    ]) {
      expect(text).toContain(expected);
    }
  });

  it("round-trips back to HTML and produces a Yjs binary", () => {
    const { html, binary } = convert();
    for (const marker of ["<h1", "<h2", "<h6", "<ul", "<ol", "<blockquote", "<pre", "<table", 'data-type="horizontalRule"']) {
      expect(html).toContain(marker);
    }
    expect(html).toContain("echo hello");
    expect(typeof binary).toBe("string");
    expect(binary.length).toBeGreaterThan(0);
  });

  it("imports the same HTML identically twice (deterministic Yjs binary)", () => {
    const first = convert();
    const second = convert();
    expect(second.html).toBe(first.html);
    expect(second.binary).toBe(first.binary);
  });
});
