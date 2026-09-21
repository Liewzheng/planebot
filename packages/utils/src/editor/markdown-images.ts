/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * The images a markdown document references.
 *
 * A markdown export that only carries the text leaves every picture behind: the
 * sources are instance URLs (asset endpoints that need a session, or signed URLs
 * that expire), so the file is unusable anywhere else. Collecting the sources
 * lets an export fetch them and ship them next to the document; rewriting then
 * points the document at those files.
 *
 * Both spellings are covered in one pass, so the collected order is the order the
 * reader sees: `![alt](src)` from the markdown serializer, and the raw
 * `<img src="…">` a page carries for pictures inserted through the API/CLI.
 */

const IMAGE_PATTERN =
  /!\[([^\]]*)\]\(\s*([^()\s]+)(?:\s+["'][^"']*["'])?\s*\)|<img\b[^>]*?\bsrc=("[^"]*"|'[^']*')[^>]*>/gi;

export type TMarkdownImage = {
  /** Alt text of a markdown image; empty for a raw `<img>`. */
  alt: string;
  /** Source exactly as written in the document. */
  src: string;
};

/** The source a match carries, whichever spelling it is. */
const sourceOf = (markdownSrc?: string, quotedSrc?: string): string | undefined =>
  markdownSrc ?? (quotedSrc ? quotedSrc.slice(1, -1) : undefined);

/** Every image the document references, in document order, without duplicates. */
export const collectMarkdownImages = (markdown: string): TMarkdownImage[] => {
  if (!markdown) return [];

  const images = new Map<string, TMarkdownImage>();
  for (const match of markdown.matchAll(IMAGE_PATTERN)) {
    const src = sourceOf(match[2], match[3]);
    if (src && !images.has(src)) images.set(src, { alt: match[1] ?? "", src });
  }

  return [...images.values()];
};

/**
 * Point every image the map knows at its exported file; sources the map does not
 * know (an image that could not be fetched, or one that is not part of the
 * export) stay as they are — a stale link beats an empty `![]()`.
 */
export const replaceMarkdownImageSources = (markdown: string, replacements: Map<string, string>): string => {
  if (!markdown || replacements.size === 0) return markdown;

  return markdown.replace(
    IMAGE_PATTERN,
    (match: string, alt: string | undefined, markdownSrc: string | undefined, quotedSrc: string | undefined) => {
      const src = sourceOf(markdownSrc, quotedSrc);
      if (!src) return match;

      const replacement = replacements.get(src);
      if (!replacement) return match;

      if (markdownSrc !== undefined) return `![${alt ?? ""}](${replacement})`;
      // keep the quote style the document used
      const quote = quotedSrc?.[0] ?? '"';
      return match.replace(quotedSrc ?? src, `${quote}${replacement}${quote}`);
    }
  );
};

/** A source that already is a URL (absolute or root-relative), or inline data. */
const IS_A_URL = /^(?:https?:|data:|blob:|\/)/i;

/**
 * Rewrite bare asset ids through the resolver.
 *
 * Pages written through the API/CLI carry `<img src="…asset id…">`, and the
 * markdown serializer passes the id through as the picture's "URL" — a bare
 * UUID is not a link anywhere, not even inside this instance. Resolving it
 * gives the export (and any reader inside the instance) something fetchable.
 */
export const resolveBareAssetSources = (markdown: string, resolve: (src: string) => string | undefined): string => {
  if (!markdown) return markdown;

  const replacements = new Map<string, string>();
  for (const image of collectMarkdownImages(markdown)) {
    if (IS_A_URL.test(image.src)) continue;
    const resolved = resolve(image.src);
    if (resolved) replacements.set(image.src, resolved);
  }

  return replaceMarkdownImageSources(markdown, replacements);
};
