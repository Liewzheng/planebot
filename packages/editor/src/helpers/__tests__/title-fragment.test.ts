/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, expect, it } from "vitest";
import * as Y from "yjs";
// helpers
import { convertBase64StringToBinaryData, convertHTMLDocumentToAllFormats } from "@/helpers/yjs-utils";

const DOCUMENT_HTML = "<p>正文内容</p>";
const DOCUMENT_NAME = "我的页面标题";

const getTitleFragment = (descriptionBinary: string): Y.XmlFragment => {
  const yDoc = new Y.Doc();
  Y.applyUpdate(yDoc, convertBase64StringToBinaryData(descriptionBinary));
  return yDoc.getXmlFragment("title");
};

const getXmlText = (node: Y.XmlFragment | Y.XmlElement): string => {
  let text = "";
  node.forEach((child) => {
    if (child instanceof Y.XmlText) {
      text += child.toString();
    } else if (child instanceof Y.XmlElement) {
      text += getXmlText(child);
    }
  });
  return text;
};

describe("convertHTMLDocumentToAllFormats title fragment", () => {
  it("writes document_name into the title fragment of the binary", () => {
    const payload = convertHTMLDocumentToAllFormats({
      document_html: DOCUMENT_HTML,
      variant: "document",
      document_name: DOCUMENT_NAME,
    });
    const title = getTitleFragment(payload.description_binary);
    expect(title.length).toBeGreaterThan(0);
    expect(getXmlText(title)).toBe(DOCUMENT_NAME);
  });

  it("leaves the title fragment empty when document_name is not provided", () => {
    const payload = convertHTMLDocumentToAllFormats({
      document_html: DOCUMENT_HTML,
      variant: "document",
    });
    const title = getTitleFragment(payload.description_binary);
    expect(title.length).toBe(0);
    expect(getXmlText(title)).toBe("");
  });

  it("treats an empty document_name like an absent one", () => {
    const payload = convertHTMLDocumentToAllFormats({
      document_html: DOCUMENT_HTML,
      variant: "document",
      document_name: "",
    });
    const title = getTitleFragment(payload.description_binary);
    expect(title.length).toBe(0);
  });

  it("does not leak the title into the body formats", () => {
    const payload = convertHTMLDocumentToAllFormats({
      document_html: DOCUMENT_HTML,
      variant: "document",
      document_name: DOCUMENT_NAME,
    });
    expect(payload.description_html).not.toContain(DOCUMENT_NAME);
    expect(JSON.stringify(payload.description_json)).not.toContain(DOCUMENT_NAME);
  });
});
