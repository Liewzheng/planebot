/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { cn } from "@plane/utils";
// store
import type { TPageInstance } from "@/store/pages/base-page";

type Props = {
  page: TPageInstance;
};

// badge styling per document status (frontmatter `status` field)
const STATUS_STYLES: Record<string, string> = {
  draft: "bg-layer-2 text-tertiary",
  wip: "bg-warning-subtle text-warning-primary",
  verified: "bg-success-subtle text-success-primary",
  deprecated: "bg-danger-subtle text-danger-primary",
};

function readString(source: Record<string, unknown>, key: string): string | undefined {
  const value = source[key];
  return typeof value === "string" && value.trim() ? value : undefined;
}

/**
 * Document metadata written as YAML frontmatter on a markdown upload
 * (`status`, `created`, `verified`). Renders nothing for pages without any,
 * so existing pages are unaffected.
 */
export const PageNavigationPaneInfoTabDocumentMetadata = observer(function PageNavigationPaneInfoTabDocumentMetadata(
  props: Props
) {
  const { page } = props;
  // translation
  const { t } = useTranslation();
  // derived values
  const frontmatter = (page.frontmatter ?? {}) as Record<string, unknown>;
  const status = readString(frontmatter, "status");
  const created = readString(frontmatter, "created");
  const verified = readString(frontmatter, "verified");

  if (!status && !created && !verified) return null;

  return (
    <div className="mt-4 space-y-3">
      {status && (
        <div>
          <p className="text-11 font-medium text-tertiary">
            {t("page_navigation_pane.tabs.info.document_metadata.status")}
          </p>
          <div className="mt-2">
            <span
              className={cn(
                "rounded-sm px-2 py-0.5 text-11 font-medium",
                STATUS_STYLES[status] ?? "bg-layer-2 text-secondary"
              )}
            >
              {status}
            </span>
          </div>
        </div>
      )}
      {created && (
        <div className="flex items-center justify-between gap-2">
          <p className="text-11 font-medium text-tertiary">
            {t("page_navigation_pane.tabs.info.document_metadata.created")}
          </p>
          <span className="text-13 font-medium">{created}</span>
        </div>
      )}
      {verified && (
        <div className="flex items-center justify-between gap-2">
          <p className="text-11 font-medium text-tertiary">
            {t("page_navigation_pane.tabs.info.document_metadata.verified")}
          </p>
          <span className="text-13 font-medium">{verified}</span>
        </div>
      )}
    </div>
  );
});
