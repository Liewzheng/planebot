/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import type { TDisplayConfig } from "@plane/editor";
import type { TPageVersion } from "@plane/types";
import { Loader } from "@plane/ui";
import { cn } from "@plane/utils";
import DOMPurify from "dompurify";
// hooks
import { usePageFilters } from "@/hooks/use-page-filters";
// plane web hooks
import type { EPageStoreType } from "@/hooks/store";

export type TVersionEditorProps = {
  activeVersion: string | null;
  versionDetails: TPageVersion | undefined;
  storeType: EPageStoreType;
};

// Version snapshots are immutable, so they are rendered as sanitized static HTML
// instead of mounting a second (read-only) editor instance.
const sanitizeVersionHTML = (html: string): string => DOMPurify.sanitize(html, { FORBID_ATTR: ["style"] });

export const PagesVersionEditor = observer(function PagesVersionEditor(props: TVersionEditorProps) {
  const { versionDetails } = props;
  // page filters
  const { fontSize, fontStyle } = usePageFilters();

  const displayConfig: TDisplayConfig = {
    fontSize,
    fontStyle,
    wideLayout: true,
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

  const sanitizedDescriptionHTML = sanitizeVersionHTML(versionDetails.description_html ?? "");

  if (!sanitizedDescriptionHTML)
    return (
      <div className="grid h-full place-items-center px-5">
        <p className="text-13 text-tertiary">This version has no content.</p>
      </div>
    );

  return (
    <div className={cn("frame-renderer w-full flex-grow", { "wide-layout": displayConfig.wideLayout })}>
      <div
        className={cn(
          "editor-container relative cursor-text",
          `line-spacing-${displayConfig.lineSpacing ?? "regular"}`,
          displayConfig.fontSize,
          displayConfig.fontStyle,
          "w-full max-w-full focus:border-0 focus:outline-none sm:rounded-lg",
          "border border-subtle-1",
          "relative border-none p-0 pb-3 pb-64 pl-3",
          "document-editor"
        )}
      >
        {/* contentEditable={false} keeps the read-only affordances of the editor styles (e.g. static checkboxes) */}
        <div
          className="ProseMirror pl-10"
          contentEditable={false}
          suppressContentEditableWarning
          dangerouslySetInnerHTML={{ __html: sanitizedDescriptionHTML }}
        />
      </div>
    </div>
  );
});
