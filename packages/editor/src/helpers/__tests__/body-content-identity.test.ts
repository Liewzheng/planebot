/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, expect, it } from "vitest";
// helpers
import { bodyContentJSON } from "@/helpers/yjs-utils";

describe("bodyContentJSON", () => {
  /** What the editor writes: block classes and ids the stored html does not carry. */
  const EDITOR_HTML =
    '<p class="editor-paragraph-block" data-id="8a0f2d3c-1111-4222-8333-444455556666">正文</p>' +
    '<h2 class="editor-heading-block" data-id="8a0f2d3c-2222-4222-8333-444455556666">标题</h2>';
  const STORED_HTML = "<p>正文</p><h2>标题</h2>";

  it("gives the editor's copy and the stored copy of one revision the same identity", () => {
    expect(bodyContentJSON(EDITOR_HTML)).toBe(bodyContentJSON(STORED_HTML));
  });

  it("tells different content apart", () => {
    expect(bodyContentJSON(STORED_HTML)).not.toBe(bodyContentJSON("<p>正文</p><h2>另一个标题</h2>"));
  });

  it("tells an empty body from a missing one", () => {
    expect(bodyContentJSON("")).toBe(bodyContentJSON("<p></p>"));
    expect(bodyContentJSON(undefined)).toBeUndefined();
    expect(bodyContentJSON(null)).toBeUndefined();
  });
});
