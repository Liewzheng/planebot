/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * The ProseMirror -> Yjs reconcile must not destroy content this client never
 * rendered (PLANE-76).
 *
 * `updateYFragment` runs on every editor transaction and makes the shared Yjs
 * document match the local ProseMirror document. When a client reconnects with a
 * stale local copy while the database holds freshly written content (a CLI / API
 * re-upload), the merged Yjs document has more blocks than the local ProseMirror
 * document, and the reconcile used to delete all of them — wiping the import and
 * broadcasting the deletion. Reproduced with the incident's own documents:
 * 15760 characters (import present) collapsed to 5808 (import gone) and the
 * journal's `deleteSet` matched.
 *
 * `patches/y-prosemirror@1.3.7.patch` (applied by the root `postinstall`)
 * restricts that deletion to children this client actually rendered. These tests
 * fail without the patch.
 *
 * Set `PLANE_INCIDENT_FIXTURES` to a directory holding `v1.bin` / `v2.bin` (the
 * stale copy and the import of the page the incident happened on) to also assert
 * against the real documents.
 */

import { existsSync, readFileSync } from "fs";
import { join } from "path";
import { getSchema } from "@tiptap/core";
import { describe, expect, it } from "vitest";
import { initProseMirrorDoc, updateYFragment } from "y-prosemirror";
import * as Y from "yjs";
// extensions
import {
  CoreEditorExtensionsWithoutProps,
  DocumentEditorExtensionsWithoutProps,
} from "@/extensions/core-without-props";
import {
  convertBase64StringToBinaryData,
  convertHTMLDocumentToAllFormats,
  getBinaryDataFromDocumentEditorHTMLString,
} from "@/helpers/yjs-utils";

const EXTENSIONS = [...CoreEditorExtensionsWithoutProps, ...DocumentEditorExtensionsWithoutProps];
const schema = getSchema(EXTENSIONS);

const textOf = (node: Y.XmlFragment | Y.XmlElement): string => {
  let text = "";
  node.forEach((child) => {
    if (child instanceof Y.XmlText) text += child.toString();
    else if (child instanceof Y.XmlElement) text += textOf(child);
  });
  return text;
};

/**
 * The incident sequence: the client's stale copy is rendered by the editor, then
 * a remote import merges into the same document, then a transaction reconciles
 * the local ProseMirror document back into Yjs.
 */
const staleClientTransaction = (staleCopy: Uint8Array, remoteImport: Uint8Array): string => {
  const doc = new Y.Doc();
  Y.applyUpdate(doc, staleCopy);
  const { doc: renderedDoc, meta } = initProseMirrorDoc(doc.getXmlFragment("default"), schema);
  Y.applyUpdate(doc, remoteImport);

  const fragment = doc.getXmlFragment("default");
  updateYFragment(doc, fragment, renderedDoc, meta);
  return textOf(fragment);
};

const fixtures = process.env.PLANE_INCIDENT_FIXTURES;
const incidentFile = (name: string) => (fixtures ? join(fixtures, name) : null);
const hasIncidentFixtures = !!fixtures && !!incidentFile("v1.bin") && existsSync(incidentFile("v1.bin")!);

describe("collaborative reconcile", () => {
  it("keeps a remote import that arrived after the local copy was rendered", () => {
    const staleHtml = '<h2 class="editor-heading-block">1. 判定链</h2><p class="editor-paragraph-block">基线为 dev 分支。</p>';
    const importHtml =
      "<h2>1. 判定链</h2><p>基线为 dev 分支。</p><blockquote><p>引文一段。</p></blockquote><h2>2. 新章节</h2><p>BGGR 四通道拆分。</p>";
    const imported = convertBase64StringToBinaryData(
      convertHTMLDocumentToAllFormats({
        document_html: importHtml,
        variant: "document",
        document_name: "探针",
      }).description_binary
    );

    const result = staleClientTransaction(getBinaryDataFromDocumentEditorHTMLString(staleHtml), imported);

    // the import survives; only the stale copy's own duplicate blocks are dropped
    expect(result).toContain("2. 新章节");
    expect(result).toContain("BGGR 四通道拆分。");
    expect(result).toContain("引文一段。");
  });

  it("still deletes a paragraph the user removed from the rendered document", () => {
    const doc = new Y.Doc();
    Y.applyUpdate(doc, getBinaryDataFromDocumentEditorHTMLString("<p>第一段。</p><p>第二段。</p>"));
    const fragment = doc.getXmlFragment("default");
    const { doc: renderedDoc, meta } = initProseMirrorDoc(fragment, schema);

    // the user deletes the second paragraph in the editor
    const lastChild = renderedDoc.lastChild;
    expect(lastChild).not.toBeNull();
    const withoutSecond = renderedDoc.type.create(
      null,
      renderedDoc.content.cut(0, renderedDoc.content.size - lastChild!.nodeSize)
    );
    updateYFragment(doc, fragment, withoutSecond, meta);

    expect(textOf(fragment)).toContain("第一段。");
    expect(textOf(fragment)).not.toContain("第二段。");
  });

  it.skipIf(!hasIncidentFixtures)("keeps the import of the page the incident happened on", () => {
    const stale = new Uint8Array(readFileSync(incidentFile("v1.bin")!));
    const imported = new Uint8Array(readFileSync(incidentFile("v2.bin")!));

    const result = staleClientTransaction(stale, imported);

    expect(result).toContain("BGGR");
  });
});
