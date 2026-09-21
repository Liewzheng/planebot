/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { Styles } from "@react-pdf/renderer";
import { StyleSheet } from "@react-pdf/renderer";
// plane utils
import { convertRemToPixel } from "@plane/utils";

/**
 * The GitHub print theme (Typora's github.css, as chosen by the user for
 * exports): Open Sans on #333 with 1.6 line height, headings bold with a hairline
 * under h1/h2, blockquote with the wide grey rail, tables with grey borders,
 * shaded header and zebra rows, and inline code as small grey boxes — not the
 * editor's orange. Lengths are px (react-pdf treats them as pt).
 *
 * Extracted from the PDF document component so the export layout can be
 * exercised headlessly (bundle this file with esbuild and render the same
 * stylesheet the modal ships).
 */

export const EDITOR_PDF_TYPOGRAPHY_STYLES: Styles = {
  // page title
  "h1.page-title": {
    fontSize: convertRemToPixel(1.5),
    fontWeight: "bold",
    lineHeight: 1.2,
    marginTop: 0,
    marginBottom: convertRemToPixel(0.8),
    paddingBottom: convertRemToPixel(0.3),
    borderBottom: "1px solid #eee",
  },
  // headings — the theme's ratios at the print baseline (13px)
  "h1:not(.page-title)": {
    fontSize: convertRemToPixel(1.5),
    fontWeight: "bold",
    lineHeight: 1.2,
    marginTop: convertRemToPixel(0.7),
    marginBottom: convertRemToPixel(0.7),
    paddingBottom: convertRemToPixel(0.3),
    borderBottom: "1px solid #eee",
  },
  h2: {
    fontSize: convertRemToPixel(1.15),
    fontWeight: "bold",
    lineHeight: 1.225,
    marginTop: convertRemToPixel(0.7),
    marginBottom: convertRemToPixel(0.7),
    paddingBottom: convertRemToPixel(0.3),
    borderBottom: "1px solid #eee",
  },
  h3: {
    fontSize: convertRemToPixel(1),
    fontWeight: "bold",
    lineHeight: 1.43,
    marginTop: convertRemToPixel(0.7),
    marginBottom: convertRemToPixel(0.7),
  },
  h4: {
    fontSize: convertRemToPixel(0.9),
    fontWeight: "bold",
    marginTop: convertRemToPixel(0.7),
    marginBottom: convertRemToPixel(0.7),
  },
  h5: {
    fontSize: convertRemToPixel(0.8),
    fontWeight: "bold",
    marginTop: convertRemToPixel(0.7),
    marginBottom: convertRemToPixel(0.7),
  },
  h6: {
    fontSize: convertRemToPixel(0.8),
    fontWeight: "bold",
    color: "#777777",
    marginTop: convertRemToPixel(0.7),
    marginBottom: convertRemToPixel(0.7),
  },
  // paragraph
  "p:not(table p)": {
    fontSize: convertRemToPixel(0.75),
    lineHeight: 1.5,
  },
  "p:not(ol p, ul p, table p)": {
    marginTop: convertRemToPixel(0.4),
    marginBottom: convertRemToPixel(0.4),
  },
};

export const EDITOR_PDF_LIST_STYLES: Styles = {
  "ul, ol": {
    fontSize: convertRemToPixel(0.75),
    marginHorizontal: -20,
  },
  "ol p, ul p": {
    marginVertical: 0,
    lineHeight: 1.6,
  },
  "ol li, ul li": {
    marginTop: convertRemToPixel(0.45),
  },
  "ul ul, ul ol, ol ol, ol ul": {
    marginVertical: 0,
  },
  "ul[data-type='taskList']": {
    position: "relative",
  },
  "div.input-checkbox": {
    position: "absolute",
    top: convertRemToPixel(0.15),
    left: -convertRemToPixel(1.2),
    height: convertRemToPixel(0.75),
    width: convertRemToPixel(0.75),
    borderWidth: "1.5px",
    borderStyle: "solid",
    borderRadius: convertRemToPixel(0.125),
  },
  "div.input-checkbox:not(.checked)": {
    backgroundColor: "#ffffff",
    borderColor: "#171717",
  },
  "div.input-checkbox.checked": {
    backgroundColor: "#3f76ff",
    borderColor: "#3f76ff",
  },
  "ul li[data-checked='true'] p": {
    color: "#a3a3a3",
  },
};

// inline code — the theme's code: a small grey box, dark text. The editor's
// html carries plain <code> tags (no data-node-type), so the rule selects the
// tag itself; the monospace fontFamily is merged in by the document component,
// which owns the font registration.
export const EDITOR_PDF_INLINE_CODE_STYLE = {
  margin: 0,
  paddingVertical: convertRemToPixel(0.25 / 4 + 0.25 / 8),
  paddingHorizontal: convertRemToPixel(0.375),
  border: "1px solid #e7eaed",
  borderRadius: convertRemToPixel(0.2),
  backgroundColor: "#f3f4f4",
  color: "#333333",
  fontSize: convertRemToPixel(0.7),
};

export const EDITOR_PDF_CODE_STYLES: Styles = {
  // code block — the theme's md-fences
  "[data-node-type='code-block']": {
    marginVertical: 15,
    padding: convertRemToPixel(1),
    borderRadius: convertRemToPixel(0.25),
    backgroundColor: "#f8f8f8",
    fontSize: convertRemToPixel(0.7),
  },
  "code, [data-node-type='inline-code-block']": EDITOR_PDF_INLINE_CODE_STYLE,
};

export const EDITOR_PDF_DOCUMENT_STYLESHEET = StyleSheet.create({
  ...EDITOR_PDF_TYPOGRAPHY_STYLES,
  ...EDITOR_PDF_LIST_STYLES,
  ...EDITOR_PDF_CODE_STYLES,
  // quote block — the theme's wide grey rail with a light fill
  blockquote: {
    borderLeft: "4px solid #dfe2e5",
    paddingLeft: 15,
    paddingVertical: convertRemToPixel(0.3),
    marginTop: convertRemToPixel(0.8),
    marginBottom: convertRemToPixel(0.8),
    marginHorizontal: 0,
    backgroundColor: "#f8f8f8",
    color: "#777777",
  },
  a: {
    color: "#4183c4",
  },
  img: {
    marginVertical: 0,
    borderRadius: convertRemToPixel(0.375),
    // editor display widths are CSS pixels and routinely exceed the page
    // content box (e.g. 1101px on A4) — clamp to the container so wide
    // images scale down instead of running off the page
    maxWidth: "100%",
  },
  // divider — the theme's 2px soft grey
  "div[data-type='horizontalRule']": {
    marginVertical: 16,
    height: 2,
    width: "100%",
    backgroundColor: "#e7e7e7",
  },
  // mention block
  "[data-node-type='mention-block']": {
    margin: 0,
    color: "#3f76ff",
    backgroundColor: "#3f76ff33",
    paddingHorizontal: convertRemToPixel(0.375),
  },
  // table — the theme's grey borders, generous cell padding. react-pdf-html
  // renders tables as flex Views: columns split evenly unless a cell's inline
  // style pins a width, and `flexGrow`/`flexShrink` must be 0 for that width
  // to stick. The export preprocessing stamps per-cell widths inline.
  table: {
    marginTop: convertRemToPixel(0.8),
    marginBottom: convertRemToPixel(0.8),
    marginHorizontal: 0,
    width: "100%",
  },
  "table thead, table tbody": {
    width: "100%",
  },
  "table tr": {
    width: "100%",
  },
  "table td": {
    paddingVertical: 3,
    paddingHorizontal: 10,
    border: "1px solid #ddd",
    fontSize: convertRemToPixel(0.75),
    lineHeight: 1.45,
    width: "auto",
    flexGrow: 0,
    flexShrink: 0,
  },
  // header cells read as a header: shaded and bold (zebra rows are stamped
  // inline by the export preprocessing — this engine does not do nth-child)
  "table th": {
    paddingVertical: 3,
    paddingHorizontal: 10,
    border: "1px solid #ddd",
    backgroundColor: "#f8f8f8",
    fontWeight: "bold",
    fontSize: convertRemToPixel(0.75),
    lineHeight: 1.45,
    width: "auto",
    flexGrow: 0,
    flexShrink: 0,
  },
  "table p": {
    fontSize: convertRemToPixel(0.75),
    lineHeight: 1.45,
    marginVertical: 0,
  },
});
