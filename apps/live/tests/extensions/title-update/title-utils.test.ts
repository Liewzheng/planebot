/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, it, expect } from "vitest";
import { collapseRepeatedTitle, extractTextFromHTML } from "@/extensions/title-update/title-utils";

describe("collapseRepeatedTitle", () => {
  const title = "自动曝光算法溯源：formal_calibrate 的来龙去脉";

  it("collapses a title duplicated by a stale client merge", () => {
    expect(collapseRepeatedTitle(title + title)).toBe(title);
  });

  it("collapses three or more copies", () => {
    expect(collapseRepeatedTitle(title + title + title)).toBe(title);
  });

  it("collapses a short repeated unit when it is long enough", () => {
    expect(collapseRepeatedTitle("IncidentIncident")).toBe("Incident");
  });

  it("keeps ordinary titles untouched", () => {
    expect(collapseRepeatedTitle("检测流程性能实测：目标机 5 轮基线（2026-09-14）")).toBe(
      "检测流程性能实测：目标机 5 轮基线（2026-09-14）"
    );
    expect(collapseRepeatedTitle(title)).toBe(title);
  });

  it("ignores short repetitions that are plausible real titles", () => {
    expect(collapseRepeatedTitle("ABAB")).toBe("ABAB");
  });

  it("does not collapse a partially repeated title", () => {
    const value = title + title.slice(0, 10);
    expect(collapseRepeatedTitle(value)).toBe(value);
  });

  it("trims surrounding whitespace", () => {
    expect(collapseRepeatedTitle(`  ${title}  `)).toBe(title);
  });

  it("handles empty input", () => {
    expect(collapseRepeatedTitle("")).toBe("");
  });
});

describe("extractTextFromHTML", () => {
  it("returns plain text for html input", () => {
    expect(extractTextFromHTML("<p>Hello</p>")).toBe("Hello");
  });
});
