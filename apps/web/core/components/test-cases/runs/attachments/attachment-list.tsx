/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { useTranslation } from "@plane/i18n";
import { CloseIcon, ImageFileIcon } from "@plane/propel/icons";
import type { ITestRunAttachment } from "@plane/types";

type Props = {
  attachments: ITestRunAttachment[];
  disabled?: boolean;
  /** Unbind one file. The caller owns the call — and its errors. */
  onRemove: (assetId: string) => Promise<void>;
};

/**
 * The files attached to a step record or to a case record.
 *
 * A removed file is flagged deleted on the server (a row operation), so the
 * Markdown source does not have to be edited along with it. The list says
 * what is attached now; the images inside the Markdown stay where they were
 * written.
 */
export function TestCaseRunAttachmentList(props: Props) {
  const { attachments, disabled = false, onRemove } = props;
  const { t } = useTranslation();
  // states
  const [removing, setRemoving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (attachments.length === 0) return null;

  const remove = async (assetId: string) => {
    setError(null);
    setRemoving(assetId);
    try {
      await onRemove(assetId);
    } catch (removeError) {
      const envelope = removeError as { error?: string; code?: string } | undefined;
      setError(envelope?.error || t("project_test_case.execution.attachments.remove_failed"));
    } finally {
      setRemoving(null);
    }
  };

  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <p className="text-11 font-medium text-secondary">
        {t("project_test_case.execution.attachments.list_label", { count: attachments.length })}
      </p>
      <ul className="flex flex-col gap-1">
        {attachments.map((attachment) => (
          <li
            key={attachment.id}
            className="flex min-w-0 items-center gap-2 rounded-md border border-subtle-1 bg-layer-1 px-2 py-1.5"
          >
            <ImageFileIcon className="size-3.5 shrink-0 text-tertiary" />
            <a
              href={attachment.url}
              target="_blank"
              rel="noopener noreferrer"
              className="min-w-0 flex-1 truncate text-12 text-secondary underline hover:no-underline"
              title={attachment.name}
            >
              {attachment.name || attachment.id}
            </a>
            <button
              type="button"
              disabled={disabled || removing === attachment.asset_id}
              onClick={() => void remove(attachment.asset_id)}
              title={t("project_test_case.execution.attachments.remove")}
              aria-label={t("project_test_case.execution.attachments.remove")}
              className="flex shrink-0 items-center gap-1 rounded-sm px-1.5 py-1 text-11 text-tertiary hover:bg-layer-2 hover:text-danger-primary disabled:cursor-not-allowed disabled:opacity-40"
            >
              <CloseIcon className="size-3" />
              <span className="hidden sm:inline">{t("project_test_case.execution.attachments.remove")}</span>
            </button>
          </li>
        ))}
      </ul>
      {error && (
        <p className="text-11 text-danger-primary" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
