/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, it, expect } from "vitest";
import { renderPlaneDocToPdfBuffer } from "@/lib/pdf";
import type { TipTapDocument } from "@/lib/pdf";
import { checkPdfHealth } from "./pdf-health";

// A4 in PDF points, as rendered by pdfkit
const A4 = { width: 595.28, height: 841.89 };

const paragraph = (text: string) => ({
  type: "paragraph",
  content: [{ type: "text", text }],
});

const cell = (text: string) => ({
  type: "tableCell",
  content: [paragraph(text)],
});

describe("PDF export health checks", () => {
  it("keeps an unspaced CJK sentence inside a narrow table cell on the page", async () => {
    // Regression: react-pdf splits text on spaces only, so a CJK run without
    // spaces was one unbreakable box — it overflowed the page edge and the
    // clipped remainder was silently dropped from the output.
    const longCjkSentence = "切换成本按帧计，帧率翻倍则每次切换的绝对代价减半（会上提过的兜底方案）";
    const doc: TipTapDocument = {
      type: "doc",
      content: [
        {
          type: "table",
          content: [
            {
              type: "tableRow",
              content: ["口径", "说明", "结论", "备注"].map((text) => ({
                type: "tableHeader",
                content: [paragraph(text)],
              })),
            },
            {
              type: "tableRow",
              content: [cell("P4"), cell("光源稳定时间"), cell("更低"), cell(longCjkSentence)],
            },
          ],
        },
      ],
    };

    const buffer = await renderPlaneDocToPdfBuffer(doc);
    const report = await checkPdfHealth(buffer, {
      expectedPageSize: A4,
      mustContain: [longCjkSentence],
    });

    expect(report.pageCount).toBe(1);
    expect(report.outOfBounds).toHaveLength(0);
    expect(report.text).not.toContain("\ufffd");
  });

  it("wraps a long URL inside the page bounds", async () => {
    const url =
      "https://example.com/this/is/a/very/long/url/that/used/to/overflow/the/right/page/boundary/in/pdf/exports";
    const doc: TipTapDocument = { type: "doc", content: [paragraph(`see ${url} for details`)] };

    const buffer = await renderPlaneDocToPdfBuffer(doc);
    const report = await checkPdfHealth(buffer, { expectedPageSize: A4, mustContain: [url] });

    expect(report.outOfBounds).toHaveLength(0);
  });

  it("wraps long code tokens inside the page bounds", async () => {
    const hash = "4f8bdb2a9c1e7f3d5b6a0c8e2f4a6b8c0d2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a";
    const identifier = "CONFIG_JUDGER_LIGHT_STABILIZE_TIMEOUT_THRESHOLD_MICROSECONDS";
    const doc: TipTapDocument = {
      type: "doc",
      content: [
        {
          type: "codeBlock",
          content: [{ type: "text", text: `const hash = "${hash}";\n#define CFG ${identifier}` }],
        },
      ],
    };

    const buffer = await renderPlaneDocToPdfBuffer(doc);
    const report = await checkPdfHealth(buffer, {
      expectedPageSize: A4,
      mustContain: [hash.slice(0, 20), identifier.slice(0, 20)],
    });

    expect(report.outOfBounds).toHaveLength(0);
  });

  it("renders a simple document with exactly one A4 page and complete content", async () => {
    const doc: TipTapDocument = {
      type: "doc",
      content: [
        { type: "heading", attrs: { level: 1 }, content: [{ type: "text", text: "标题 Heading" }] },
        paragraph("正文内容 body text，包含中文与 English 混排。"),
      ],
    };

    const buffer = await renderPlaneDocToPdfBuffer(doc, { title: "Health Check Doc" });
    const report = await checkPdfHealth(buffer, {
      expectedPageSize: A4,
      mustContain: ["标题 Heading", "正文内容 body text，包含中文与 English 混排。"],
    });

    expect(report.pageCount).toBe(1);
    expect(report.pageSizes[0].width).toBeCloseTo(A4.width, 0);
    expect(report.pageSizes[0].height).toBeCloseTo(A4.height, 0);
  });
});
