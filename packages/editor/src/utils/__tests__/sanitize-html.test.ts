// @vitest-environment jsdom
/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, expect, it } from "vitest";
// utils
import { sanitizeHTML, sanitizePageVersionHTML, sanitizeSVG } from "@/utils/sanitize-html";

/**
 * A representative mermaid flowchart SVG (real mermaid 11.x output, trimmed).
 * Covers the constructs the renderer emits: a <style> block, <marker>
 * definitions with marker-end, <foreignObject> HTML labels (nodeLabel span),
 * data-* attributes and role/aria attributes. Sanitizing must not change a
 * single byte, or the rendered diagram would visibly change.
 */
const MERMAID_SVG =
  '<svg id="fixture" width="100%" xmlns="http://www.w3.org/2000/svg" class="flowchart" style="max-width: 136px;" viewBox="-8 -8 136 76" role="graphics-document document" aria-roledescription="flowchart-v2"><style>#fixture{font-family:"trebuchet ms",verdana,arial,sans-serif;font-size:16px;fill:#333;}</style><g><marker id="fixture_flowchart-v2-pointEnd" class="marker flowchart-v2" viewBox="0 0 10 10" refX="5" refY="5" markerUnits="userSpaceOnUse" markerWidth="8" markerHeight="8" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" class="arrowMarkerPath" style="stroke-width: 1; stroke-dasharray: 1,0;"></path></marker><g class="root"><g class="edgePaths"><path d="M128,38L174,38" id="fixture-L_A_B_0" class="edge-thickness-normal flowchart-link" style=";" data-edge="true" marker-end="url(#fixture_flowchart-v2-pointEnd)"></path></g><g class="nodes"><g class="node default" id="fixture-flowchart-A-0" transform="translate(68, 38)"><rect class="basic label-container" style="" x="-30" y="-15" width="60" height="30"></rect><g class="label" style="" transform="translate(0, 0)"><foreignObject width="0" height="0"><div style="display: table-cell; white-space: nowrap; line-height: 1.5; max-width: 200px; text-align: center;" xmlns="http://www.w3.org/1999/xhtml"><span class="nodeLabel"><p>A</p></span></div></foreignObject></g></g></g></g></g><defs><filter id="fixture-drop-shadow" height="130%" width="130%"><fedropshadow dx="4" dy="4" stdDeviation="0" flood-opacity="0.06" flood-color="#000000"></fedropshadow></filter></defs></svg>';

/** Real KaTeX output for "E = mc^2" in display mode (strict, trust: false). */
const KATEX_HTML =
  '<span class="katex-display"><span class="katex"><span class="katex-mathml"><math xmlns="http://www.w3.org/1998/Math/MathML" display="block"><semantics><mrow><mi>E</mi><mo>=</mo><mi>m</mi><msup><mi>c</mi><mn>2</mn></msup></mrow><annotation encoding="application/x-tex">E = mc^2</annotation></semantics></math></span><span class="katex-html" aria-hidden="true"><span class="base"><span class="strut" style="height:0.6833em;"></span><span class="mord mathnormal" style="margin-right:0.05764em;">E</span><span class="mspace" style="margin-right:0.2778em;"></span><span class="mrel">=</span><span class="mspace" style="margin-right:0.2778em;"></span></span><span class="base"><span class="strut" style="height:0.8641em;"></span><span class="mord mathnormal">m</span><span class="mord"><span class="mord mathnormal">c</span><span class="msupsub"><span class="vlist-t"><span class="vlist-r"><span class="vlist" style="height:0.8641em;"><span style="top:-3.113em;margin-right:0.05em;"><span class="pstrut" style="height:2.7em;"></span><span class="sizing reset-size6 size3 mtight"><span class="mord mtight">2</span></span></span></span></span></span></span></span></span></span></span></span>';

describe("sanitizeSVG", () => {
  it("preserves mermaid SVG output byte-for-byte", () => {
    expect(sanitizeSVG(MERMAID_SVG)).toBe(MERMAID_SVG);
  });

  it("strips scripts and event handler attributes from SVG", () => {
    const malicious =
      '<svg viewBox="0 0 10 10"><script>alert(1)</script><rect onload="alert(2)" x="0" y="0" width="5" height="5" fill="red"/><path d="M0 0L5 5" class="edge"/><image onerror="alert(3)" href="x"/></svg>';
    const sanitized = sanitizeSVG(malicious);
    expect(sanitized).not.toContain("<script");
    expect(sanitized).not.toContain("onload");
    expect(sanitized).not.toContain("onerror");
    // structure stays intact
    expect(sanitized).toContain("<svg");
    expect(sanitized).toContain('viewBox="0 0 10 10"');
    expect(sanitized).toContain("<rect");
    expect(sanitized).toContain("<path");
    expect(sanitized).toContain('class="edge"');
  });
});

describe("sanitizeHTML", () => {
  it("preserves KaTeX markup byte-for-byte", () => {
    expect(sanitizeHTML(KATEX_HTML)).toBe(KATEX_HTML);
  });

  it("keeps KaTeX content and strips scripts", () => {
    const sanitized = sanitizeHTML('<span class="katex">E = mc^2</span><script>alert(1)</script>');
    expect(sanitized).toContain('<span class="katex">');
    expect(sanitized).toContain("E = mc^2");
    expect(sanitized).not.toContain("<script");
  });
});

describe("sanitizePageVersionHTML", () => {
  /** An image the editor inserted: serialized as an <image-component> custom element. */
  const EDITOR_IMAGE =
    '<image-component src="e9a1b2c3-0000-4000-8000-000000000000" alt="测试图" width="506" height="284"></image-component>';

  it("keeps the editor's own image markup", () => {
    const sanitized = sanitizePageVersionHTML(`<p>before</p>${EDITOR_IMAGE}<p>after</p>`);
    expect(sanitized).toContain("<image-component");
    expect(sanitized).toContain('src="e9a1b2c3-0000-4000-8000-000000000000"');
    expect(sanitized).toContain('alt="测试图"');
    expect(sanitized).toContain('width="506"');
    expect(sanitized).toContain("<p>before</p>");
  });

  it("still strips scripts, event handlers and inline styles", () => {
    const sanitized = sanitizePageVersionHTML(
      `<div style="position:fixed" onclick="alert(1)"><script>alert(1)</script><img src="x" onerror="alert(2)"></div>`
    );
    expect(sanitized).not.toContain("<script");
    expect(sanitized).not.toContain("onclick");
    expect(sanitized).not.toContain("onerror");
    expect(sanitized).not.toContain("style=");
    expect(sanitized).toContain("<img");
  });
});
