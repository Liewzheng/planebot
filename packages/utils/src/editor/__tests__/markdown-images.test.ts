/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, expect, it } from "vitest";
// utils
import { collectMarkdownImages, replaceMarkdownImageSources, resolveBareAssetSources } from "../markdown-images";

const MARKDOWN = [
  "# 论文",
  "",
  "![3f2a-1](/api/assets/v2/workspaces/isletspace/projects/p1/3f2a-1/)",
  "",
  "<img src='/api/assets/v2/workspaces/isletspace/projects/p1/9c8b-2/' />",
  "",
  "![重复](/api/assets/v2/workspaces/isletspace/projects/p1/3f2a-1/)",
  "",
  "![外链](https://example.com/diagram.png)",
].join("\n");

describe("collectMarkdownImages", () => {
  it("finds markdown and raw html images, in order, without duplicates", () => {
    expect(collectMarkdownImages(MARKDOWN)).toEqual([
      { alt: "3f2a-1", src: "/api/assets/v2/workspaces/isletspace/projects/p1/3f2a-1/" },
      { alt: "", src: "/api/assets/v2/workspaces/isletspace/projects/p1/9c8b-2/" },
      { alt: "外链", src: "https://example.com/diagram.png" },
    ]);
  });

  it("keeps a markdown image that carries a title", () => {
    expect(collectMarkdownImages('![图](assets/a.png "标题")')).toEqual([{ alt: "图", src: "assets/a.png" }]);
  });

  it("returns nothing for a document without images", () => {
    expect(collectMarkdownImages("只有文字，还有 <img 不是标签")).toEqual([]);
  });
});

describe("replaceMarkdownImageSources", () => {
  it("points the collected sources at their exported files", () => {
    const rewritten = replaceMarkdownImageSources(
      MARKDOWN,
      new Map([
        ["/api/assets/v2/workspaces/isletspace/projects/p1/3f2a-1/", "assets/image-1.png"],
        ["/api/assets/v2/workspaces/isletspace/projects/p1/9c8b-2/", "assets/image-2.jpg"],
      ])
    );

    expect(rewritten).toContain("![3f2a-1](assets/image-1.png)");
    expect(rewritten).toContain("src='assets/image-2.jpg'");
    // every occurrence is rewritten, not just the first
    expect(rewritten.match(/assets\/image-1\.png/g)).toHaveLength(2);
    // sources that were not collected stay untouched
    expect(rewritten).toContain("![外链](https://example.com/diagram.png)");
  });

  it("leaves the document alone when nothing was collected", () => {
    expect(replaceMarkdownImageSources(MARKDOWN, new Map())).toBe(MARKDOWN);
  });
});

describe("resolveBareAssetSources", () => {
  it("resolves bare asset ids the API/CLI write, and leaves URLs alone", () => {
    const markdown = [
      "![Fig. 1 能力矩阵](7a6797e9-acea-46fe-803e-71dbb47b6b9c)",
      "![Fig. 2](cda22b2f-b996-4865-a163-e1e45ac3f358)",
      "![外链](https://example.com/diagram.png)",
      '<img src="7a6797e9-acea-46fe-803e-71dbb47b6b9c" />',
    ].join("\n");

    const resolved = resolveBareAssetSources(
      markdown,
      (assetId) => `/api/assets/v2/workspaces/isletspace/projects/p1/${assetId}/`
    );

    expect(resolved).toContain(
      "![Fig. 1 能力矩阵](/api/assets/v2/workspaces/isletspace/projects/p1/7a6797e9-acea-46fe-803e-71dbb47b6b9c/)"
    );
    expect(resolved).toContain(
      '<img src="/api/assets/v2/workspaces/isletspace/projects/p1/7a6797e9-acea-46fe-803e-71dbb47b6b9c/" />'
    );
    expect(resolved).toContain("![外链](https://example.com/diagram.png)");
  });

  it("keeps an id the resolver cannot map", () => {
    const markdown = "![图](deadbeef-0000-4000-8000-000000000000)";
    expect(resolveBareAssetSources(markdown, () => undefined)).toBe(markdown);
  });
});
