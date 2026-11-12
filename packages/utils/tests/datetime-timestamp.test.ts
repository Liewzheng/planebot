/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, expect, it } from "vitest";
// utils
import { renderFormattedTimestamp } from "../src/datetime";

/**
 * The audit API answers with full ISO instants; two events twenty minutes
 * apart. A formatter that keeps only the date renders both as midnight and
 * makes them look like one event.
 */
const FIRST_EVENT = "2026-11-12T01:25:34.996080Z";
const SECOND_EVENT = "2026-11-12T01:45:34.996080Z";

/** The clock of an instant, read the way the formatter reads it. */
const clockOf = (instant: string) =>
  new Intl.DateTimeFormat("en", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(instant));

describe("renderFormattedTimestamp", () => {
  it("tells two events twenty minutes apart apart", () => {
    const first = renderFormattedTimestamp(FIRST_EVENT, "en");
    const second = renderFormattedTimestamp(SECOND_EVENT, "en");

    expect(first).toBeDefined();
    expect(second).toBeDefined();
    expect(first).not.toBe(second);
  });

  it("keeps the time of day of the event instead of midnight", () => {
    // The clock is read in the reader's timezone, so the expectation is built
    // from the same instant rather than from a hardcoded wall-clock time.
    const rendered = renderFormattedTimestamp(FIRST_EVENT, "en");

    expect(rendered).toContain(clockOf(FIRST_EVENT));
    expect(rendered).not.toContain("00:00");
  });

  it("renders the date the event happened on", () => {
    const rendered = renderFormattedTimestamp(FIRST_EVENT, "en");

    expect(rendered).toContain("2026");
    expect(rendered).toMatch(/\w{3}\s+\d{2},\s+2026\s+\d{2}:\d{2}/);
  });

  it("returns nothing for a missing or unparsable timestamp", () => {
    expect(renderFormattedTimestamp(undefined)).toBeUndefined();
    expect(renderFormattedTimestamp(null)).toBeUndefined();
    expect(renderFormattedTimestamp("")).toBeUndefined();
    expect(renderFormattedTimestamp("not-a-date")).toBeUndefined();
  });

  it("localizes the month and the day to the given language", () => {
    const english = renderFormattedTimestamp(FIRST_EVENT, "en");
    const chinese = renderFormattedTimestamp(FIRST_EVENT, "zh-CN");

    expect(english).toBeDefined();
    expect(chinese).toBeDefined();
    expect(chinese).not.toBe(english);
    expect(chinese).toContain("月");
  });

  it("falls back to English for a language tag the runtime does not know", () => {
    // The app's language list carries tags the runtime has never heard of
    // ("ua" for Ukrainian); those must fall back instead of throwing.
    expect(renderFormattedTimestamp(FIRST_EVENT, "ua")).toBe(renderFormattedTimestamp(FIRST_EVENT, "en"));
  });
});
