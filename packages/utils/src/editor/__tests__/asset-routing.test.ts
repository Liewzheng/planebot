/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, expect, it } from "vitest";
// plane utils
import { editorAssetApiVersion, isV1EditorAssetSrc } from "@plane/utils";
// re-import through the package entry so the test fails if the helper is not
// exported from `@plane/utils`, which is what the editor and the web app see
import { editorAssetApiVersion as editorAssetApiVersionReexported, isV1EditorAssetSrc as isV1EditorAssetSrcReexported } from "../common";

describe("editorAssetApiVersion", () => {
  it("treats a V2 absolute URL as V2", () => {
    const src = "https://api.example.com/api/assets/v2/workspaces/foo/projects/bar/8a0f2d3c-1111-4222-8333-444455556666/";
    expect(editorAssetApiVersion(src)).toBe("v2");
    expect(isV1EditorAssetSrc(src)).toBe(false);
  });

  it("treats a V2 root-relative URL as V2", () => {
    const src = "/api/assets/v2/workspaces/foo/8a0f2d3c-1111-4222-8333-444455556666/";
    expect(editorAssetApiVersion(src)).toBe("v2");
    expect(isV1EditorAssetSrc(src)).toBe(false);
  });

  it("treats a bare asset id as V2 (the editor's private-bucket short form)", () => {
    expect(editorAssetApiVersion("8a0f2d3c-1111-4222-8333-444455556666")).toBe("v2");
    expect(isV1EditorAssetSrc("8a0f2d3c-1111-4222-8333-444455556666")).toBe(false);
  });

  it("treats a V1 absolute URL as V1", () => {
    const src = "https://api.example.com/api/workspaces/file-assets/8a0f2d3c-cccc-4222-8333-444455556666/foo-asset-key/";
    expect(editorAssetApiVersion(src)).toBe("v1");
    expect(isV1EditorAssetSrc(src)).toBe(true);
  });

  it("handles absent input without throwing", () => {
    expect(editorAssetApiVersion(undefined)).toBe("v2");
    expect(editorAssetApiVersion(null)).toBe("v2");
    expect(editorAssetApiVersion("")).toBe("v2");
    expect(isV1EditorAssetSrc(undefined)).toBe(false);
    expect(isV1EditorAssetSrc(null)).toBe(false);
    expect(isV1EditorAssetSrc("")).toBe(false);
  });

  it("is importable from the @plane/utils package entry", () => {
    // regression guard: the helper must be importable from `@plane/utils`,
    // which is the only entry the web app uses. The two import paths can
    // resolve to different function objects (the package entry wraps the
    // module-level export), so the assertion is on behavior, not identity.
    expect(editorAssetApiVersionReexported("https://example.com/api/assets/v2/foo/")).toBe("v2");
    expect(editorAssetApiVersionReexported("https://example.com/api/workspaces/file-assets/foo/bar/")).toBe("v1");
    expect(isV1EditorAssetSrcReexported("https://example.com/api/workspaces/file-assets/foo/bar/")).toBe(true);
  });
});