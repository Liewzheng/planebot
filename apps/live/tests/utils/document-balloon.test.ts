/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, expect, it } from "vitest";
// utils
import { DUPLICATED_BLOCK_MIN_LENGTH, duplicatedBlockLength } from "@/utils/document-balloon";

const paragraph = (text: string) => `<p>${text}</p>`;

const longBody = (start = 0) =>
  paragraph(Array.from({ length: 80 }, (_, index) => `第${start + index}段内容，记录一次实测的数据与结论。`).join(""));

describe("duplicatedBlockLength", () => {
  it("reports the repeated block when a body holds itself twice", () => {
    const body = longBody();

    expect(duplicatedBlockLength(body)).toBe(0);
    expect(duplicatedBlockLength(body + body)).toBeGreaterThanOrEqual(DUPLICATED_BLOCK_MIN_LENGTH);
  });

  it("finds a copy that starts between two sampled positions", () => {
    // a stride-based scan misses this: the second copy starts at an odd offset
    const body = longBody();
    const offset = paragraph("补一段单独的说明文字，长度不整齐，正好错开采样步长。");

    expect(duplicatedBlockLength(offset + body + body)).toBeGreaterThanOrEqual(DUPLICATED_BLOCK_MIN_LENGTH);
  });

  it("does not flag a body that repeats a sentence on purpose", () => {
    const body = paragraph("结论：裁剪只在 rkcif 一层执行，其他层级不参与。".repeat(12));

    expect(duplicatedBlockLength(body)).toBe(0);
  });

  it("does not flag a long clean body", () => {
    expect(duplicatedBlockLength(longBody(0) + longBody(200))).toBe(0);
  });

  it("ignores bodies too short to contain a quarter of themselves twice", () => {
    expect(duplicatedBlockLength("<p>短正文</p>")).toBe(0);
    expect(duplicatedBlockLength("")).toBe(0);
  });

  it("measures the block on visible text, not on the markup", () => {
    // the same content twice, serialized two different ways (editor vs import)
    const editorFlavour = longBody()
      .replace(/<p>/g, '<p class="editor-paragraph-block">')
      .replace(/<\/p>/g, "</p>");
    const importFlavour = longBody();

    expect(duplicatedBlockLength(importFlavour + editorFlavour)).toBeGreaterThanOrEqual(
      DUPLICATED_BLOCK_MIN_LENGTH
    );
  });
});
