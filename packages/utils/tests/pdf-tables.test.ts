/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, expect, it } from "vitest";
// utils
import { applyPdfTableLayoutToHtml, tableColumnWidths } from "../src/pdf-tables";

describe("tableColumnWidths", () => {
  it("sizes columns by content and fills the content width", () => {
    const widths = tableColumnWidths(
      [
        ["短", "长列，长列，长列，长列"],
        ["短", "长列，长列，长列"],
      ],
      467.28
    );
    expect(widths).toHaveLength(2);
    // the wide column must be notably wider than the key column...
    expect(widths[1]).toBeGreaterThan(widths[0] * 2);
    // ...and the total exactly covers the page content box
    expect(widths[0] + widths[1]).toBeCloseTo(467.28, 6);
  });

  it("returns nothing for a table without rows", () => {
    expect(tableColumnWidths([], 467.28)).toEqual([]);
  });
});

describe("applyPdfTableLayoutToHtml", () => {
  const table = [
    "<table>",
    "  <thead><tr><th>项</th><th>值</th></tr></thead>",
    "  <tbody>",
    "    <tr><td>发布基线</td><td>v0.5.4</td></tr>",
    "    <tr><td>当前 HEAD</td><td>gfb810cf</td></tr>",
    "    <tr><td>构建版本串</td><td>NONE</td></tr>",
    "  </tbody>",
    "</table>",
  ].join("");

  it("stamps the table width and per-cell pixel widths", () => {
    const out = applyPdfTableLayoutToHtml(table, 467.28);
    expect(out).toContain('<table style="width: 100%">');
    // header and body cells all carry a content-derived width
    const stamped = (out.match(/<t[dh][^>]*style="width: \d+px/g) ?? []).length;
    expect(stamped).toBe(8);
  });

  it("paints zebra rows on every other body row", () => {
    const out = applyPdfTableLayoutToHtml(table, 467.28);
    expect(out).toContain("background-color: #f8f8f8");
    const zebraCells = (out.match(/<td[^>]*style="[^"]*background-color: #f8f8f8/g) ?? []).length;
    // three body rows shade the middle one only — two cells
    expect(zebraCells).toBe(2);
  });

  it("merges with an existing cell style instead of replacing it", () => {
    const styled = '<table><tr><td style="text-align: right">k</td><td>v</td></tr></table>';
    const out = applyPdfTableLayoutToHtml(styled, 467.28);
    expect(out).toContain('style="text-align: right; width:');
  });

  it("leaves tables without rows alone", () => {
    const html = "<table></table>";
    expect(applyPdfTableLayoutToHtml(html, 467.28)).toBe(html);
  });

  it("returns the input untouched when there are no tables", () => {
    const html = "<p>nothing here</p>";
    expect(applyPdfTableLayoutToHtml(html, 467.28)).toBe(html);
  });
});
