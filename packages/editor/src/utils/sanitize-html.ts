/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import DOMPurify from "dompurify";
import type { Config } from "dompurify";

// Mirrors mermaid's own strict-mode DOMPurify setup (mermaid sanitizes label
// HTML with ADD_TAGS ["foreignObject"] and ADD_ATTR ["dominant-baseline"]) so
// that sanitizing a rendered diagram can never alter its output.
const SVG_SANITIZE_CONFIG: Config = {
  ADD_TAGS: ["foreignobject"],
  ADD_ATTR: ["dominant-baseline"],
  // DOMPurify does not model <foreignObject> as an HTML integration point and
  // removes its HTML children by default; mermaid renders flowchart/state/
  // class/er node labels as HTML inside <foreignObject>, so those must stay.
  // (The option replaces DOMPurify's default list, which only holds
  // annotation-xml, so the default entry is repeated here.)
  HTML_INTEGRATION_POINTS: { annotationxml: true, foreignobject: true },
};

// KaTeX wraps its MathML output in <semantics> with an <annotation> fallback
// for assistive tech; neither is in DOMPurify's default allowlist.
const HTML_SANITIZE_CONFIG: Config = {
  ADD_TAGS: ["semantics", "annotation"],
};

// Page-version snapshots hold editor markup: an image the author inserted
// serializes as an `<image-component>` custom element. DOMPurify allows no
// custom element by default and drops it with its children, so sanitizing a
// snapshot silently lost exactly those images — the `<img>` markup written
// through the API/CLI was the only kind that survived. `style` is dropped
// because the version view re-applies dimensions itself (the editor stores
// them as presentational attrs, which the static markup cannot carry).
const PAGE_VERSION_SANITIZE_CONFIG: Config = {
  FORBID_ATTR: ["style"],
  ADD_TAGS: ["image-component"],
};

/**
 * Sanitize an SVG string before injecting it into the DOM. Keeps the SVG
 * structure mermaid emits (foreignObject labels, style blocks, marker
 * attributes) while stripping scripts and event handler attributes.
 */
export const sanitizeSVG = (svg: string): string => DOMPurify.sanitize(svg, SVG_SANITIZE_CONFIG);

/**
 * Sanitize an HTML string before injecting it into the DOM. Tuned for KaTeX
 * output so rendered formulas survive unchanged.
 */
export const sanitizeHTML = (html: string): string => DOMPurify.sanitize(html, HTML_SANITIZE_CONFIG);

/**
 * Sanitize a stored page-version snapshot before rendering it as static HTML.
 * Keeps the editor's `<image-component>` blocks so the version view can resolve
 * and show them; everything else follows DOMPurify's HTML allowlist.
 */
export const sanitizePageVersionHTML = (html: string): string => DOMPurify.sanitize(html, PAGE_VERSION_SANITIZE_CONFIG);
