/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * @description the language identifier used for Mermaid diagram code blocks
 */
export const MERMAID_LANGUAGE = "mermaid";

/**
 * @description check if a code block language attribute refers to a Mermaid diagram
 * @param {unknown} language
 */
export const isMermaidLanguage = (language: unknown): boolean =>
  typeof language === "string" && language.trim().toLowerCase() === MERMAID_LANGUAGE;

/**
 * @description Mermaid diagram type keywords that unambiguously identify a diagram source
 * when the code block has no explicit language set
 */
const MERMAID_DIAGRAM_TYPE_PATTERN =
  /^(flowchart|graph)\s+(TD|TB|BT|LR|RL|SN|SF|MS|MB)\b|^(sequenceDiagram|classDiagram|stateDiagram|erDiagram|requirementDiagram|gantt|pie\b|journey|mindmap|timeline|quadrantChart|gitGraph|C4Context|C4Container|C4Component|C4Dynamic|C4Deployment|sankey-beta|xychart-beta|block-beta|packet-beta|kanban|architecture-beta|radar|treemap)\b/;

/**
 * @description check if code block content without a language attribute looks like a Mermaid diagram
 * @param {string} source
 */
export const isLikelyMermaidSource = (source: string): boolean => {
  if (!source) return false;
  const firstLine = source.trimStart().split("\n", 1)[0].trim();
  return MERMAID_DIAGRAM_TYPE_PATTERN.test(firstLine);
};

export type TMermaidTheme = "default" | "dark";

/**
 * @description resolve the Mermaid theme from a `data-theme` attribute value
 * (e.g. set by next-themes). Values containing "dark" (dark, dark-contrast)
 * map to Mermaid's dark theme; everything else maps to the default theme.
 * @param {string | null | undefined} themeAttribute
 */
export const getMermaidTheme = (themeAttribute: string | null | undefined): TMermaidTheme =>
  !!themeAttribute && themeAttribute.includes("dark") ? "dark" : "default";

/**
 * @description extract a human-readable message from a Mermaid render failure
 * @param {unknown} error
 */
export const formatMermaidError = (error: unknown): string => {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  if (typeof error === "string" && error) {
    return error;
  }
  return "Unable to render the diagram. Please check the Mermaid syntax.";
};
