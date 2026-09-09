/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { formatMermaidError, getMermaidTheme } from "@plane/utils";

let mermaidModulePromise: Promise<typeof import("mermaid").default> | undefined;
const loadMermaid = () => {
  mermaidModulePromise ??= import("mermaid").then((module) => module.default);
  return mermaidModulePromise;
};

let initializedTheme: string | undefined;
let renderIdCounter = 0;

/**
 * Render a Mermaid source string to an SVG string.
 * Returns the SVG on success, throws a human-readable error on failure.
 */
export const renderMermaidToSVG = async (source: string, theme?: string): Promise<string> => {
  if (!source.trim()) {
    return "";
  }

  const mermaid = await loadMermaid();
  const resolvedTheme = getMermaidTheme(theme);
  if (initializedTheme !== resolvedTheme) {
    mermaid.initialize({ startOnLoad: false, theme: resolvedTheme });
    initializedTheme = resolvedTheme;
  }

  const { svg } = await mermaid.render(`mermaid-diagram-${++renderIdCounter}`, source);
  return svg;
};

export { formatMermaidError, getMermaidTheme };
