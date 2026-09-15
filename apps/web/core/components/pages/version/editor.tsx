/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { useParams } from "next/navigation";
import { useCallback, useRef, useState } from "react";
// plane imports
import { ImageFullScreenModal, type TDisplayConfig } from "@plane/editor";
import type { TPageVersion } from "@plane/types";
import { Loader } from "@plane/ui";
import { cn, getEditorAssetSrc, getPageName } from "@plane/utils";
import DOMPurify from "dompurify";
// hooks
import { usePageFilters } from "@/hooks/use-page-filters";
// plane web hooks
import type { EPageStoreType } from "@/hooks/store";

export type TVersionEditorProps = {
  activeVersion: string | null;
  pageName?: string;
  versionDetails: TPageVersion | undefined;
  storeType: EPageStoreType;
};

// stored srcs can be absolute URLs or bare asset ids — resolve the ids through
// the same helper the editor file handler uses so static and live views agree
const resolveAssetSrc = (src: string, workspaceSlug?: string, projectId?: string): string => {
  if (!src) return "";
  if (src.startsWith("http") || src.startsWith("data:") || src.startsWith("blob:")) return src;
  if (!workspaceSlug) return src;
  return getEditorAssetSrc({ assetId: src, projectId, workspaceSlug }) ?? src;
};

// editor size attrs ("506px", "35%", 506) only work as presentational hints by
// lenient browser parsing; move them to inline styles so the static markup is
// valid and renders like the editor
const applyDimensionStyle = (el: HTMLElement, attr: "width" | "height") => {
  const value = el.getAttribute(attr);
  if (!value) return;
  el.removeAttribute(attr);
  if (value === "auto") return;
  el.style.setProperty(attr, /^\d+(\.\d+)?$/.test(value) ? `${value}px` : value);
};

// static images open the full-screen viewer on click/keyboard activation, so
// they need the same affordances the editor's image node view gets
const makeImageInteractive = (img: HTMLImageElement) => {
  img.classList.add("cursor-zoom-in");
  img.setAttribute("tabindex", "0");
  img.setAttribute("role", "button");
  img.setAttribute("aria-label", "View image full screen");
};

// Version snapshots are immutable, so they are rendered as sanitized static HTML
// instead of mounting a second (read-only) editor instance. The stored description_html
// is class-less server-generated markup, so re-apply the block class hooks the client
// editor adds (starter-kit) to keep the read-mode typography rules matching.
const sanitizeVersionHTML = (html: string, workspaceSlug?: string, projectId?: string): string => {
  const container = document.createElement("div");
  container.innerHTML = DOMPurify.sanitize(html, { FORBID_ATTR: ["style"] });
  container.querySelectorAll("h1, h2, h3, h4, h5, h6").forEach((el) => el.classList.add("editor-heading-block"));
  container.querySelectorAll("p").forEach((el) => el.classList.add("editor-paragraph-block"));
  // resolve bare asset ids in image sources
  container.querySelectorAll("img").forEach((img) => {
    const src = img.getAttribute("src");
    if (src) img.setAttribute("src", resolveAssetSrc(src, workspaceSlug, projectId));
    applyDimensionStyle(img, "width");
    applyDimensionStyle(img, "height");
    makeImageInteractive(img);
  });
  // custom-image nodes serialize as <image-component>; swap them for plain <img>
  container.querySelectorAll("image-component").forEach((component) => {
    const img = document.createElement("img");
    const src = component.getAttribute("src");
    if (src) img.setAttribute("src", resolveAssetSrc(src, workspaceSlug, projectId));
    ["alt", "width", "height"].forEach((attr) => {
      const value = component.getAttribute(attr);
      if (value) img.setAttribute(attr, value);
    });
    applyDimensionStyle(img, "width");
    applyDimensionStyle(img, "height");
    makeImageInteractive(img);
    component.replaceWith(img);
  });
  return container.innerHTML;
};

export const PagesVersionEditor = observer(function PagesVersionEditor(props: TVersionEditorProps) {
  const { pageName, versionDetails } = props;
  // page filters
  const { fontSize, fontStyle, isFullWidth } = usePageFilters();
  // route params
  const { workspaceSlug, projectId } = useParams();
  // full-screen image preview state
  const [fullScreenImage, setFullScreenImage] = useState<{ src: string; aspectRatio: number; width: string } | null>(
    null
  );
  const contentRef = useRef<HTMLDivElement>(null);
  const isTouchDevice = typeof window !== "undefined" && (navigator.maxTouchPoints > 0 || "ontouchstart" in window);

  const openImagePreview = useCallback((img: HTMLImageElement) => {
    const src = img.currentSrc || img.src;
    if (!src) return;
    const { naturalWidth, naturalHeight } = img;
    setFullScreenImage({
      src,
      // fallbacks mirror the mermaid full-screen preview in the code block node view
      aspectRatio: naturalWidth > 0 && naturalHeight > 0 ? naturalWidth / naturalHeight : 16 / 9,
      width: naturalWidth > 0 ? `${naturalWidth}px` : "800px",
    });
  }, []);

  // static markup carries no React handlers, so delegate click/keyboard
  // activation from the container to its images
  const handleContentClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (!(e.target instanceof HTMLElement)) return;
      const img = e.target.closest("img");
      if (!(img instanceof HTMLImageElement) || !contentRef.current?.contains(img)) return;
      openImagePreview(img);
    },
    [openImagePreview]
  );

  const handleContentKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      if (!(e.target instanceof HTMLImageElement) || !contentRef.current?.contains(e.target)) return;
      e.preventDefault();
      openImagePreview(e.target);
    },
    [openImagePreview]
  );

  const displayConfig: TDisplayConfig = {
    fontSize,
    fontStyle,
    wideLayout: isFullWidth,
  };

  if (!versionDetails)
    return (
      <div className="size-full px-5">
        <Loader className="relative space-y-4">
          <Loader.Item width="50%" height="36px" />
          <div className="space-y-2">
            <div className="py-2">
              <Loader.Item width="100%" height="36px" />
            </div>
            <Loader.Item width="80%" height="22px" />
            <div className="relative flex items-center gap-2">
              <Loader.Item width="30px" height="30px" />
              <Loader.Item width="30%" height="22px" />
            </div>
            <div className="py-2">
              <Loader.Item width="60%" height="36px" />
            </div>
            <Loader.Item width="70%" height="22px" />
            <Loader.Item width="30%" height="22px" />
            <div className="relative flex items-center gap-2">
              <Loader.Item width="30px" height="30px" />
              <Loader.Item width="30%" height="22px" />
            </div>
            <div className="py-2">
              <Loader.Item width="50%" height="30px" />
            </div>
            <Loader.Item width="100%" height="22px" />
            <div className="py-2">
              <Loader.Item width="30%" height="30px" />
            </div>
            <Loader.Item width="30%" height="22px" />
            <div className="relative flex items-center gap-2">
              <div className="py-2">
                <Loader.Item width="30px" height="30px" />
              </div>
              <Loader.Item width="30%" height="22px" />
            </div>
          </div>
        </Loader>
      </div>
    );

  const sanitizedDescriptionHTML = sanitizeVersionHTML(versionDetails.description_html ?? "", workspaceSlug, projectId);

  if (!sanitizedDescriptionHTML)
    return (
      <div className="grid h-full place-items-center px-5">
        <p className="text-13 text-tertiary">This version has no content.</p>
      </div>
    );

  return (
    <div className={cn("frame-renderer w-full flex-grow", { "wide-layout": displayConfig.wideLayout })}>
      {/* same title markup/classes as the live page so both modes read as one document */}
      {pageName !== undefined && (
        <div className="relative w-full py-3">
          <div
            className={cn(
              "editor-container page-title-editor relative cursor-text border-none bg-transparent py-3",
              `line-spacing-${displayConfig.lineSpacing ?? "regular"}`,
              displayConfig.fontSize,
              displayConfig.fontStyle
            )}
          >
            <div
              contentEditable={false}
              suppressContentEditableWarning
              className="ProseMirror no-scrollbar placeholder-placeholder w-full resize-none rounded-none border-none bg-transparent p-0 text-[2rem] leading-[2.375rem] font-bold tracking-[-2%] outline-none"
            >
              <h1>{getPageName(pageName)}</h1>
            </div>
          </div>
        </div>
      )}
      <div
        ref={contentRef}
        onClick={handleContentClick}
        onKeyDown={handleContentKeyDown}
        className={cn(
          "editor-container relative cursor-text",
          `line-spacing-${displayConfig.lineSpacing ?? "regular"}`,
          displayConfig.fontSize,
          displayConfig.fontStyle,
          "w-full max-w-full focus:border-0 focus:outline-none sm:rounded-lg",
          "border border-subtle-1",
          "border-none p-0 pb-64",
          "document-editor"
        )}
      >
        {/* contentEditable={false} keeps the read-only affordances of the editor styles (e.g. static checkboxes) */}
        <div
          className="ProseMirror prose-brand prose-headings:font-display font-default max-w-full prose focus:outline-none"
          contentEditable={false}
          suppressContentEditableWarning
          dangerouslySetInnerHTML={{ __html: sanitizedDescriptionHTML }}
        />
      </div>
      <ImageFullScreenModal
        src={fullScreenImage?.src ?? ""}
        downloadSrc={fullScreenImage?.src ?? ""}
        isFullScreenEnabled={!!fullScreenImage}
        isTouchDevice={isTouchDevice}
        aspectRatio={fullScreenImage?.aspectRatio ?? 16 / 9}
        width={fullScreenImage?.width ?? "800px"}
        toggleFullScreenMode={(val) => {
          if (!val) setFullScreenImage(null);
        }}
      />
    </div>
  );
});
