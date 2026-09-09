/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { NodeViewProps } from "@tiptap/react";
import { NodeViewWrapper, NodeViewContent } from "@tiptap/react";
import ts from "highlight.js/lib/languages/typescript";
import { common, createLowlight } from "lowlight";
import { CopyOutline, TickOutline } from "@makeplane/propel/icons";
import { useEffect, useMemo, useState } from "react";
// ui
import { Tooltip } from "@plane/propel/tooltip";
// plane utils
import { cn, isLikelyMermaidSource, isMermaidLanguage } from "@plane/utils";
// types
import type { TCodeBlockAttributes } from "./types";
import { ECodeBlockAttributeNames } from "./types";
// components
import { MermaidDiagram } from "./mermaid-diagram";
import { renderMermaidToSVG } from "./mermaid-render";
import { ImageFullScreenModal } from "../custom-image/components/toolbar/full-screen/modal";

// we just have ts support for now
const lowlight = createLowlight(common);
lowlight.register("ts", ts);

const MERMAID_LANGUAGE = "mermaid";

const hashSource = async (source: string): Promise<string> => {
  // crypto.subtle is only available in secure contexts; fall back to a fast
  // string hash so self-hosted HTTP IPs still get cache invalidation.
  if (typeof crypto !== "undefined" && crypto.subtle) {
    try {
      const encoder = new TextEncoder();
      const data = encoder.encode(source);
      const digest = await crypto.subtle.digest("SHA-256", data);
      return Array.from(new Uint8Array(digest))
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");
    } catch {
      // fall through
    }
  }
  let h1 = 0xdeadbeef;
  let h2 = 0x41c6ce57;
  for (let i = 0; i < source.length; i++) {
    const ch = source.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(16);
};

export function CodeBlockComponent(props: NodeViewProps) {
  const { node, editor, updateAttributes, extension } = props;
  const [copied, setCopied] = useState(false);
  const [sourceHash, setSourceHash] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [imageSrc, setImageSrc] = useState<string | undefined>(undefined);
  const [imageDownloadSrc, setImageDownloadSrc] = useState<string | undefined>(undefined);
  const [isPreviewOpen, setIsPreviewOpen] = useState(false);

  // derived values
  const attrs = node.attrs as TCodeBlockAttributes;
  const currentLanguage = attrs[ECodeBlockAttributeNames.LANGUAGE] ?? "";
  const mermaidImageId = attrs[ECodeBlockAttributeNames.MERMAID_IMAGE_ID] ?? null;
  const mermaidImageUrl = attrs[ECodeBlockAttributeNames.MERMAID_IMAGE_URL] ?? null;
  const cachedSourceHash = attrs[ECodeBlockAttributeNames.MERMAID_SOURCE_HASH] ?? null;
  const hideSource = attrs[ECodeBlockAttributeNames.MERMAID_HIDE_SOURCE] ?? false;

  // render as a diagram when the language is mermaid, or when no language is set
  // but the content is unmistakably a mermaid diagram (e.g. pasted without a fence language)
  const renderMermaid =
    isMermaidLanguage(attrs[ECodeBlockAttributeNames.LANGUAGE]) ||
    (!currentLanguage && isLikelyMermaidSource(node.textContent));

  // keep a local hash of the current source so we can detect stale cached images
  useEffect(() => {
    let cancelled = false;
    const compute = async () => {
      if (!renderMermaid) {
        setSourceHash(null);
        return;
      }
      const hash = await hashSource(node.textContent);
      if (!cancelled) setSourceHash(hash);
    };
    void compute();
    return () => {
      cancelled = true;
    };
  }, [node.textContent, renderMermaid]);

  const isImageStale = useMemo(
    () => !!mermaidImageId && !!sourceHash && cachedSourceHash !== sourceHash,
    [mermaidImageId, cachedSourceHash, sourceHash]
  );

  const showImage = renderMermaid && hideSource && !!mermaidImageId && !isImageStale;
  const showLive = renderMermaid && (!hideSource || !mermaidImageId || isImageStale);

  // resolve the cached image URL when displayed. Prefer the stored workspace
  // asset URL (served inline); fall back to resolving by asset id for legacy
  // blocks that only store mermaidImageId.
  useEffect(() => {
    if (!showImage || !mermaidImageId) {
      setImageSrc(undefined);
      setImageDownloadSrc(undefined);
      return;
    }
    if (mermaidImageUrl) {
      setImageSrc(mermaidImageUrl);
      setImageDownloadSrc(mermaidImageUrl);
      return;
    }
    let cancelled = false;
    const resolve = async () => {
      try {
        const getSrc = extension.options.getAssetSrc as (path: string) => Promise<string> | undefined;
        const getDownloadSrc = extension.options.getAssetDownloadSrc as ((path: string) => Promise<string>) | undefined;
        const src = (await getSrc?.(mermaidImageId)) ?? "";
        const downloadSrc = (await getDownloadSrc?.(mermaidImageId)) ?? src;
        if (!cancelled) {
          setImageSrc(src);
          setImageDownloadSrc(downloadSrc);
        }
      } catch (error) {
        console.error("Failed to resolve mermaid image source:", error);
      }
    };
    void resolve();
    return () => {
      cancelled = true;
    };
  }, [
    showImage,
    mermaidImageId,
    mermaidImageUrl,
    extension.options.getAssetSrc,
    extension.options.getAssetDownloadSrc,
  ]);

  // languages supported by lowlight plus mermaid (rendered as a diagram, not highlighted)
  const languageOptions = useMemo(() => {
    const languages = lowlight.listLanguages().toSorted((a, b) => a.localeCompare(b));
    const options = [MERMAID_LANGUAGE, ...languages];
    // keep an explicitly set but unregistered language selectable instead of blanking the picker
    if (currentLanguage && !isMermaidLanguage(currentLanguage) && !options.includes(currentLanguage)) {
      options.push(currentLanguage);
    }
    return options;
  }, [currentLanguage]);

  const copyToClipboard = async (e: React.MouseEvent<HTMLButtonElement, MouseEvent>) => {
    try {
      await navigator.clipboard.writeText(node.textContent);
      setCopied(true);
      setTimeout(() => setCopied(false), 1000);
    } catch {
      setCopied(false);
    }
    e.preventDefault();
    e.stopPropagation();
  };

  const handleLanguageChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const language = e.target.value;
    updateAttributes({ [ECodeBlockAttributeNames.LANGUAGE]: language === "" ? null : language });
  };

  const handleToggleHide = async () => {
    if (!editor.isEditable || !renderMermaid || !sourceHash) return;

    // show source again
    if (hideSource) {
      updateAttributes({ [ECodeBlockAttributeNames.MERMAID_HIDE_SOURCE]: false });
      return;
    }

    // already cached and up-to-date
    if (mermaidImageId && cachedSourceHash === sourceHash) {
      updateAttributes({ [ECodeBlockAttributeNames.MERMAID_HIDE_SOURCE]: true });
      return;
    }

    // render + upload
    const upload = extension.options.uploadMermaidDiagram as
      | ((svgBlob: Blob, sourceHash: string) => Promise<{ assetId: string; assetUrl: string }>)
      | undefined;
    if (!upload) return;

    setIsUploading(true);
    try {
      const themeAttribute = document.documentElement.getAttribute("data-theme");
      const svg = await renderMermaidToSVG(node.textContent, themeAttribute ?? undefined);
      const svgBlob = new Blob([svg], { type: "image/svg+xml" });
      const { assetId, assetUrl } = await upload(svgBlob, sourceHash);
      updateAttributes({
        [ECodeBlockAttributeNames.MERMAID_IMAGE_ID]: assetId,
        [ECodeBlockAttributeNames.MERMAID_IMAGE_URL]: assetUrl,
        [ECodeBlockAttributeNames.MERMAID_SOURCE_HASH]: sourceHash,
        [ECodeBlockAttributeNames.MERMAID_HIDE_SOURCE]: true,
      });
    } catch (error) {
      console.error("Failed to upload mermaid diagram:", error);
    } finally {
      setIsUploading(false);
    }
  };

  const altText = useMemo(() => {
    const firstLine = node.textContent.trim().split("\n", 1)[0];
    return firstLine.length > 60 ? `${firstLine.slice(0, 60)}…` : firstLine;
  }, [node.textContent]);

  return (
    <NodeViewWrapper key={attrs[ECodeBlockAttributeNames.ID]} className="code-block group/code relative">
      {editor.isEditable && (
        <div
          contentEditable={false}
          role="presentation"
          className="absolute top-2 left-2 z-10 flex items-center gap-2"
          onMouseDown={(e) => e.stopPropagation()}
        >
          <select
            value={isMermaidLanguage(currentLanguage) ? MERMAID_LANGUAGE : currentLanguage}
            onChange={handleLanguageChange}
            aria-label="Code language"
            className="h-8 cursor-pointer rounded-md border border-subtle bg-layer-1 px-2 text-11 text-secondary backdrop-blur-sm outline-none hover:text-primary"
          >
            <option value="">Plain text</option>
            {languageOptions.map((language) => (
              <option key={language} value={language}>
                {language}
              </option>
            ))}
          </select>
          {renderMermaid && (
            <button
              type="button"
              onClick={() => void handleToggleHide()}
              disabled={isUploading}
              className="h-8 rounded-md border border-subtle bg-layer-1 px-2 text-11 text-secondary backdrop-blur-sm outline-none hover:text-primary disabled:opacity-60"
            >
              {isUploading ? "Saving…" : hideSource ? "Show source" : "Hide source"}
            </button>
          )}
        </div>
      )}
      <Tooltip tooltipContent="Copy code">
        <button
          type="button"
          className={cn(
            "group/button absolute top-2 right-2 z-10 hidden size-8 items-center justify-center rounded-md border border-subtle bg-layer-1 backdrop-blur-sm transition duration-150 ease-in-out group-hover/code:flex",
            {
              "bg-success-subtle hover:bg-success-subtle-1 active:bg-success-subtle-1": copied,
            }
          )}
          onClick={(e) => void copyToClipboard(e)}
        >
          {copied ? (
            <TickOutline className="h-3 w-3 text-success-primary" />
          ) : (
            <CopyOutline className="h-3 w-3 text-tertiary group-hover/button:text-primary" />
          )}
        </button>
      </Tooltip>

      {showLive && (
        <pre className={cn("my-2 rounded-lg bg-layer-3 p-4 text-primary", { "pt-10": editor.isEditable })}>
          <NodeViewContent as="code" className="whitespace-pre-wrap" />
        </pre>
      )}

      {showLive && renderMermaid && <MermaidDiagram source={node.textContent} />}

      {showImage && imageSrc && (
        <div className="my-2 flex justify-center rounded-lg border border-subtle bg-layer-3 p-4">
          <button type="button" onClick={() => setIsPreviewOpen(true)} className="mermaid-diagram-image-button">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={imageSrc}
              alt={altText}
              className="mermaid-diagram-image max-w-full cursor-zoom-in rounded-md object-contain"
            />
          </button>
        </div>
      )}

      {isPreviewOpen && imageSrc && (
        <ImageFullScreenModal
          src={imageSrc}
          downloadSrc={imageDownloadSrc ?? imageSrc}
          isFullScreenEnabled={isPreviewOpen}
          toggleFullScreenMode={setIsPreviewOpen}
          aspectRatio={16 / 9}
          width="800px"
          isTouchDevice={!!editor.storage.utility?.isTouchDevice}
        />
      )}
    </NodeViewWrapper>
  );
}
