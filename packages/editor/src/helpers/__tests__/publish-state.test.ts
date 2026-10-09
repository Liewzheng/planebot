/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, expect, it } from "vitest";
// helpers
import { clearPublishInFlight, isPublishInFlight, markPublishInFlight } from "@/helpers/publish-state";

describe("publish state", () => {
  it("reports a publish only while its request is in flight", () => {
    const pageId = "6d1a0b1e-0000-4000-8000-000000000001";

    expect(isPublishInFlight(pageId)).toBe(false);
    markPublishInFlight(pageId);
    expect(isPublishInFlight(pageId)).toBe(true);
    clearPublishInFlight(pageId);
    expect(isPublishInFlight(pageId)).toBe(false);
  });

  it("keeps pages apart", () => {
    const publishing = "6d1a0b1e-0000-4000-8000-000000000002";
    const other = "6d1a0b1e-0000-4000-8000-000000000003";

    markPublishInFlight(publishing);
    expect(isPublishInFlight(other)).toBe(false);
    clearPublishInFlight(publishing);
  });

  it("ignores an empty id", () => {
    markPublishInFlight("");
    expect(isPublishInFlight("")).toBe(false);
    clearPublishInFlight("");
  });
});
