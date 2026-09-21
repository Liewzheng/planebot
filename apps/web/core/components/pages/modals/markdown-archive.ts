/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import {
  collectMarkdownImages,
  createZip,
  getBase64Image,
  getFileExtension,
  parseDataUri,
  replaceMarkdownImageSources,
  resolveBareAssetSources,
} from "@plane/utils";
import type { TZipEntry } from "@plane/utils";

const EXTENSION_BY_MIME_TYPE: Record<string, string> = {
  "image/png": ".png",
  "image/jpeg": ".jpg",
  "image/jpg": ".jpg",
  "image/gif": ".gif",
  "image/webp": ".webp",
  "image/svg+xml": ".svg",
  "image/bmp": ".bmp",
  "image/avif": ".avif",
};

/**
 * A page's markdown together with the images it shows, as one archive.
 *
 * The markdown alone points at this instance: asset endpoints answer only for a
 * signed-in session, and their URLs expire, so the file loses every picture the
 * moment it leaves — which is the whole point of exporting it. Each referenced
 * image is fetched here and written to `assets/`, and the document is rewritten
 * to those relative paths, so the unzipped folder works anywhere.
 *
 * Pages written through the API/CLI store `<img src="…asset id…">`; the
 * serializer carries the bare id into the markdown, so it is resolved through
 * the instance's asset route first — a bare UUID is not a link anywhere.
 *
 * An image that cannot be fetched, or that answers with something that is not
 * an image (a login page, an SPA fallback), keeps its link instead of an empty
 * `![]()`.
 */
export const buildMarkdownArchive = async (args: {
  markdown: string;
  fileName: string;
  /** turn a bare asset id into the instance's asset URL */
  resolveAssetSrc: (assetId: string) => string | undefined;
}): Promise<Blob> => {
  const { markdown, fileName, resolveAssetSrc } = args;
  const document = resolveBareAssetSources(markdown, resolveAssetSrc);
  const images = collectMarkdownImages(document);
  const replacements = new Map<string, string>();
  const files: TZipEntry[] = [];
  let imageIndex = 0;

  for (const image of images) {
    try {
      const absoluteSrc = new URL(image.src, window.location.origin).href;
      const { mimeType, bytes } = parseDataUri(await getBase64Image(absoluteSrc));
      // the asset route can answer with HTML (session expired, SPA fallback):
      // that is not a picture, and shipping it would corrupt the export
      if (!mimeType.startsWith("image/") || !bytes.length) {
        console.warn(`Markdown export: ${absoluteSrc} answered as ${mimeType}, keeping the link`);
        continue;
      }

      imageIndex += 1;
      const extension = EXTENSION_BY_MIME_TYPE[mimeType] ?? getFileExtension(absoluteSrc);
      const path = `assets/image-${imageIndex}${extension}`;
      files.push({ path, data: bytes });
      replacements.set(image.src, path);
    } catch (error) {
      console.error(`Could not include ${image.src} in the markdown export:`, error);
    }
  }

  const rewrittenDocument = replaceMarkdownImageSources(document, replacements);

  return createZip([{ path: `${fileName}.md`, data: new TextEncoder().encode(rewrittenDocument) }, ...files]);
};
