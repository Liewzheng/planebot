/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Matches work item identifiers like PLANE-1 (uppercase letters/digits, a
 * dash, then a number). Same pattern is passed to the editor's link extension
 * via `getIssueReferenceConfig`.
 */
export const ISSUE_REFERENCE_REGEX = /\b[A-Z][A-Z0-9]+-\d+\b/;

/**
 * Config for the editor's custom link extension: while typing and on paste,
 * identifiers matching the pattern are linked to the work item browse route.
 */
export const getIssueReferenceConfig = (workspaceSlug: string) => ({
  pattern: ISSUE_REFERENCE_REGEX,
  resolve: (identifier: string) => `/${workspaceSlug}/browse/${identifier}`,
});

// Nodes whose text must not be rewritten (existing links, code, mentions).
const SKIP_SELECTOR = "a, code, pre, mention-component";

/**
 * One-time preprocessing for already-persisted HTML (e.g. old comments): the
 * autolink plugin only runs on doc changes, so identifiers saved as plain text
 * would never become links on their own. Wraps each identifier text node in an
 * anchor pointing at the work item browse route. The href is built from the
 * `[A-Z0-9-]` identifier only, so no attacker-controlled value reaches it.
 */
export const convertIssueReferencesToLinks = (htmlContent: string, workspaceSlug: string): string => {
  if (!htmlContent || !ISSUE_REFERENCE_REGEX.test(htmlContent)) return htmlContent;

  const document = new DOMParser().parseFromString(htmlContent, "text/html");
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);

  // Collect first: mutating the DOM while walking it would skip nodes.
  const textNodes: Text[] = [];
  let currentNode = walker.nextNode();
  while (currentNode) {
    if (!currentNode.parentElement?.closest(SKIP_SELECTOR)) {
      textNodes.push(currentNode as Text);
    }
    currentNode = walker.nextNode();
  }

  textNodes.forEach((textNode) => {
    const text = textNode.nodeValue ?? "";
    const globalPattern = new RegExp(ISSUE_REFERENCE_REGEX.source, "g");
    const fragment = document.createDocumentFragment();
    let lastIndex = 0;
    let replaced = false;
    let match: RegExpExecArray | null;
    while ((match = globalPattern.exec(text)) !== null) {
      if (match.index > lastIndex) {
        fragment.appendChild(document.createTextNode(text.slice(lastIndex, match.index)));
      }
      const anchor = document.createElement("a");
      anchor.setAttribute("href", `/${workspaceSlug}/browse/${match[0]}`);
      anchor.textContent = match[0];
      fragment.appendChild(anchor);
      lastIndex = match.index + match[0].length;
      replaced = true;
    }
    if (!replaced) return;
    if (lastIndex < text.length) {
      fragment.appendChild(document.createTextNode(text.slice(lastIndex)));
    }
    textNode.parentNode?.replaceChild(fragment, textNode);
  });

  return document.body.innerHTML;
};
