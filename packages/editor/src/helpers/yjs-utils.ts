/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { Buffer } from "buffer";
import type { Extensions, JSONContent } from "@tiptap/core";
import { getSchema } from "@tiptap/core";
import { generateHTML, generateJSON } from "@tiptap/html";
import { prosemirrorJSONToYXmlFragment, yXmlFragmentToProseMirrorRootNode } from "y-prosemirror";
import * as Y from "yjs";
// extensions
import type { TDocumentPayload } from "@plane/types";
import {
  CoreEditorExtensionsWithoutProps,
  DocumentEditorExtensionsWithoutProps,
} from "@/extensions/core-without-props";
import { TitleExtensions } from "@/extensions/title-extension";
import { sanitizeHTML } from "@plane/utils";

// editor extension configs
const RICH_TEXT_EDITOR_EXTENSIONS = CoreEditorExtensionsWithoutProps;
const DOCUMENT_EDITOR_EXTENSIONS = [...CoreEditorExtensionsWithoutProps, ...DocumentEditorExtensionsWithoutProps];
export const TITLE_EDITOR_EXTENSIONS: Extensions = TitleExtensions;
// editor schemas
const richTextEditorSchema = getSchema(RICH_TEXT_EDITOR_EXTENSIONS);
const documentEditorSchema = getSchema(DOCUMENT_EDITOR_EXTENSIONS);

/**
 * @description deterministic Yjs client id derived from the content.
 *
 * Every HTML → Yjs conversion used to build a fresh `Y.Doc` with a random
 * client id, so re-applying the same content to a document that already held
 * it merged as *new* items and duplicated the page (a page could balloon to
 * several times its size). Deriving the client id from the content makes a
 * repeated conversion produce identical items — applying it is idempotent —
 * while different content still gets a different id and cannot collide.
 */
const clientIdForContent = (json: unknown): number => {
  const text = JSON.stringify(json) ?? "";
  let hash = 0x811c9dc5; // FNV-1a
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  // Yjs reserves 0 for "no client id"; keep the value a positive 32-bit int.
  return (hash >>> 0) % 0xffffffff || 1;
};

/**
 * @description convert ProseMirror JSON into a Yjs doc with a content-derived,
 * deterministic client id. See `clientIdForContent`.
 */
const jsonToYDoc = (schema: ReturnType<typeof getSchema>, json: JSONContent, fragment: string): Y.Doc => {
  const ydoc = new Y.Doc();
  ydoc.clientID = clientIdForContent(json);
  prosemirrorJSONToYXmlFragment(schema, json, ydoc.getXmlFragment(fragment));
  return ydoc;
};

/**
 * @description apply updates to a doc and return the updated doc in binary format
 * @param {Uint8Array} document
 * @param {Uint8Array} updates
 * @returns {Uint8Array}
 */
export const applyUpdates = (document: Uint8Array, updates?: Uint8Array): Uint8Array => {
  const yDoc = new Y.Doc();
  Y.applyUpdate(yDoc, document);
  if (updates) {
    Y.applyUpdate(yDoc, updates);
  }

  const encodedDoc = Y.encodeStateAsUpdate(yDoc);
  return encodedDoc;
};

/**
 * @description this function encodes binary data to base64 string
 * @param {Uint8Array} document
 * @returns {string}
 */
export const convertBinaryDataToBase64String = (document: Uint8Array): string =>
  Buffer.from(document).toString("base64");

/**
 * @description this function decodes base64 string to binary data
 * @param {string} document
 * @returns {Buffer<ArrayBuffer>}
 */
export const convertBase64StringToBinaryData = (document: string): Buffer<ArrayBuffer> =>
  Buffer.from(document, "base64");

/**
 * @description this function generates the binary equivalent of html content for the rich text editor
 * @param {string} descriptionHTML
 * @returns {Uint8Array}
 */
export const getBinaryDataFromRichTextEditorHTMLString = (descriptionHTML: string): Uint8Array => {
  // convert HTML to JSON
  const contentJSON = generateJSON(descriptionHTML ?? "<p></p>", RICH_TEXT_EDITOR_EXTENSIONS);
  // convert JSON to Y.Doc format
  const transformedData = jsonToYDoc(richTextEditorSchema, contentJSON, "default");
  // convert Y.Doc to Uint8Array format
  const encodedData = Y.encodeStateAsUpdate(transformedData);
  return encodedData;
};

export const generateTitleProsemirrorJson = (text: string): JSONContent => {
  return {
    type: "doc",
    content: [
      {
        type: "heading",
        attrs: { level: 1 },
        ...(text
          ? {
              content: [
                {
                  type: "text",
                  text,
                },
              ],
            }
          : {}),
      },
    ],
  };
};

/**
 * @description this function generates the binary equivalent of html content for the document editor
 * @param {string} descriptionHTML - The HTML content to convert
 * @param {string} [title] - Optional title to append to the document
 * @returns {Uint8Array}
 */
export const getBinaryDataFromDocumentEditorHTMLString = (descriptionHTML: string, title?: string): Uint8Array => {
  // convert HTML to JSON
  const contentJSON = generateJSON(descriptionHTML ?? "<p></p>", DOCUMENT_EDITOR_EXTENSIONS);
  // convert JSON to Y.Doc format
  const transformedData = jsonToYDoc(documentEditorSchema, contentJSON, "default");

  // If title is provided, merge it into the document
  if (title != null) {
    const titleJSON = generateTitleProsemirrorJson(title);
    const titleField = jsonToYDoc(documentEditorSchema, titleJSON, "title");
    // Encode the title YDoc to updates and apply them to the main document
    const titleUpdates = Y.encodeStateAsUpdate(titleField);
    Y.applyUpdate(transformedData, titleUpdates);
  }

  // convert Y.Doc to Uint8Array format
  const encodedData = Y.encodeStateAsUpdate(transformedData);
  return encodedData;
};

/**
 * @description Convert document-editor HTML into a Yjs update authored by a fresh
 * (random) client id.
 *
 * `getBinaryDataFromDocumentEditorHTMLString` derives the client id from the
 * content so a repeated conversion is idempotent — applying the same content to
 * a document that already holds it dedupes by (clientID, clock) and no-ops. The
 * editing session uses that to keep shared / cached documents from ballooning
 * when the server reissues the same revision.
 *
 * A draft body needs the opposite: when the user stashes a draft that repeats
 * the published revision (no edits made), the conversion must NOT dedupe — the
 * editor is starting from a copy of the published revision, and applying the
 * draft should look like new structs that the editor can take ownership of, not
 * a no-op that leaves the body empty. This function uses a fresh client id so
 * the structs carry a (clientID, clock) pair the copy has not integrated yet,
 * and re-applying identical content still produces visible items.
 *
 * The title fragment is handled the same way: it gets its own random client id
 * when present, again so a repeated "no edit" stash still updates the title
 * fragment.
 */
export const getBinaryDataFromDocumentEditorHTMLStringAsNewClient = (
  descriptionHTML: string,
  title?: string
): Uint8Array => {
  const contentJSON = generateJSON(descriptionHTML ?? "<p></p>", DOCUMENT_EDITOR_EXTENSIONS);

  // Build the body doc on a brand-new Y.Doc — Y.Doc() picks a random client id,
  // which is exactly the property we need here.
  const bodyDoc = new Y.Doc();
  prosemirrorJSONToYXmlFragment(documentEditorSchema, contentJSON, bodyDoc.getXmlFragment("default"));

  if (title != null) {
    const titleJSON = generateTitleProsemirrorJson(title);
    const titleDoc = new Y.Doc();
    prosemirrorJSONToYXmlFragment(documentEditorSchema, titleJSON, titleDoc.getXmlFragment("title"));
    Y.applyUpdate(bodyDoc, Y.encodeStateAsUpdate(titleDoc));
  }

  return Y.encodeStateAsUpdate(bodyDoc);
};

/**
 * @description this function generates all document formats for the provided binary data for the rich text editor
 * @param {Uint8Array} description
 * @returns
 */
export const getAllDocumentFormatsFromRichTextEditorBinaryData = (
  description: Uint8Array
): {
  contentBinaryEncoded: string;
  contentJSON: object;
  contentHTML: string;
} => {
  // encode binary description data
  const base64Data = convertBinaryDataToBase64String(description);
  const yDoc = new Y.Doc();
  Y.applyUpdate(yDoc, description);
  // convert to JSON
  const type = yDoc.getXmlFragment("default");
  const contentJSON = yXmlFragmentToProseMirrorRootNode(type, richTextEditorSchema).toJSON();
  // convert to HTML
  const contentHTML = generateHTML(contentJSON, RICH_TEXT_EDITOR_EXTENSIONS);

  return {
    contentBinaryEncoded: base64Data,
    contentJSON,
    contentHTML,
  };
};

/**
 * @description this function generates all document formats for the provided binary data for the document editor
 * @param {Uint8Array} description
 * @returns
 */
export const getAllDocumentFormatsFromDocumentEditorBinaryData = (
  description: Uint8Array,
  updateTitle: boolean
): {
  contentBinaryEncoded: string;
  contentJSON: object;
  contentHTML: string;
  titleHTML?: string;
} => {
  // encode binary description data
  const base64Data = convertBinaryDataToBase64String(description);
  const yDoc = new Y.Doc();
  Y.applyUpdate(yDoc, description);
  // convert to JSON
  const type = yDoc.getXmlFragment("default");
  const contentJSON = yXmlFragmentToProseMirrorRootNode(type, documentEditorSchema).toJSON();
  // convert to HTML
  const contentHTML = generateHTML(contentJSON, DOCUMENT_EDITOR_EXTENSIONS);

  if (updateTitle) {
    const title = yDoc.getXmlFragment("title");
    const titleJSON = yXmlFragmentToProseMirrorRootNode(title, documentEditorSchema).toJSON();
    const titleHTML = extractTextFromHTML(generateHTML(titleJSON, DOCUMENT_EDITOR_EXTENSIONS));

    return {
      contentBinaryEncoded: base64Data,
      contentJSON,
      contentHTML,
      titleHTML,
    };
  } else {
    return {
      contentBinaryEncoded: base64Data,
      contentJSON,
      contentHTML,
    };
  }
};

type TConvertHTMLDocumentToAllFormatsArgs = {
  document_html: string;
  variant: "rich" | "document";
  /**
   * Optional page name. When the variant is "document" and a non-empty name is
   * given, the conversion embeds it in the title fragment of the binary so the
   * page carries its name without putting it in the body HTML/JSON (where it
   * would render as a duplicate heading). An empty string is treated as
   * absent: callers that pass the page name only when they have one can do so
   * unconditionally.
   */
  document_name?: string;
};

/**
 * @description Converts HTML content to all supported document formats (JSON, HTML, and binary)
 * @param {TConvertHTMLDocumentToAllFormatsArgs} args - Arguments containing HTML content and variant type
 * @param {string} args.document_html - The HTML content to convert
 * @param {"rich" | "document"} args.variant - The type of editor variant to use for conversion
 * @param {string} [args.document_name] - Optional page name written into the title fragment
 * @returns {TDocumentPayload} Object containing the document in all supported formats
 * @throws {Error} If an invalid variant is provided
 */
export const convertHTMLDocumentToAllFormats = (args: TConvertHTMLDocumentToAllFormatsArgs): TDocumentPayload => {
  const { document_html, variant, document_name } = args;

  let allFormats: TDocumentPayload;

  if (variant === "rich") {
    // Convert HTML to binary format for rich text editor
    const contentBinary = getBinaryDataFromRichTextEditorHTMLString(document_html);
    // Generate all document formats from the binary data
    const { contentBinaryEncoded, contentHTML, contentJSON } =
      getAllDocumentFormatsFromRichTextEditorBinaryData(contentBinary);
    allFormats = {
      description_json: contentJSON,
      description_html: contentHTML,
      description_binary: contentBinaryEncoded,
    };
  } else if (variant === "document") {
    // Treat an empty string as absent: callers that look the name up from a
    // page that just had its title cleared should not produce an empty title
    // fragment when there is nothing to write.
    const trimmedName = document_name?.trim();
    // Convert HTML to binary format for document editor
    const contentBinary = getBinaryDataFromDocumentEditorHTMLString(document_html, trimmedName || undefined);
    // Generate all document formats from the binary data
    const { contentBinaryEncoded, contentHTML, contentJSON } = getAllDocumentFormatsFromDocumentEditorBinaryData(
      contentBinary,
      false
    );
    allFormats = {
      description_json: contentJSON,
      description_html: contentHTML,
      description_binary: contentBinaryEncoded,
    };
  } else {
    throw new Error(`Invalid variant provided: ${variant}`);
  }

  return allFormats;
};

export const extractTextFromHTML = (html: string): string => {
  // Use DOMPurify to safely extract text and remove all HTML tags
  // This is more secure than regex as it handles edge cases and prevents injection
  // Note: sanitizeHTML trims whitespace, which is acceptable for title extraction
  const sanitizedText = sanitizeHTML(html); // sanitize the string to remove all HTML tags
  return sanitizedText.trim() || ""; // trim the string to remove leading and trailing whitespaces
};

/**
 * @description deterministic content identity for a document-editor body.
 *
 * The editor writes block ids (`data-id`) and presentation classes
 * (`editor-paragraph-block`, `editor-heading-block`, …) that the API's stored
 * HTML does not carry, so a plain HTML diff between "what the editor sees" and
 * "what the page stores" reports them as different revisions of the same body.
 * Strip those editor-only attributes before stringifying, so two copies of one
 * body — however it was authored — produce the same identity and a draft that
 * repeats the published revision is recognised and dropped instead of kept as
 * an "unsaved" copy.
 *
 * The identity is intentionally a stable JSON string rather than a hash: a
 * hash would still tell two bodies apart correctly, but a JSON string keeps the
 * function dependency-free and makes tests easier to reason about. The body
 * sizes that reach this function are well under the cost of stringify.
 *
 * Returns `undefined` for absent input (`null` / `undefined`) so a missing
 * revision does not collide with an empty body (`""` / `"<p></p>"`).
 */
const stripEditorBodyAttrs = (node: unknown): unknown => {
  if (!node || typeof node !== "object") return node;
  const record = node as Record<string, unknown>;
  if (record.attrs && typeof record.attrs === "object") {
    const attrs = { ...(record.attrs as Record<string, unknown>) };
    delete attrs.class;
    delete attrs["data-id"];
    record.attrs = attrs;
  }
  if (Array.isArray(record.content)) {
    record.content = record.content.map(stripEditorBodyAttrs);
  }
  return record;
};

export const bodyContentJSON = (html?: string | null): string | undefined => {
  if (html == null) return undefined;
  // Empty input is normalized to an empty paragraph: a body that lost its only
  // block and a body that was never written collapse to the same identity.
  const json = generateJSON(html || "<p></p>", DOCUMENT_EDITOR_EXTENSIONS);
  const stripped = stripEditorBodyAttrs(json) as object;
  return JSON.stringify(stripped);
};
