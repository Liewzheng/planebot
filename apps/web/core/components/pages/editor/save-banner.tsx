/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { WarningTriangleOutline } from "@makeplane/propel/icons";
// plane imports
import { useTranslation } from "@plane/i18n";
import { cn } from "@plane/utils";
// helpers
import type { TPageDraft, TPageDraftConflict } from "@/helpers/page-draft";
// hooks
import type { TSaveError } from "@/hooks/use-page-save";

type Props = {
  className?: string;
  conflict: TPageDraftConflict | null;
  draft: TPageDraft | null;
  /** the caller's own unpublished revision, kept on the server (PLANE-77) */
  serverDraft: string | null;
  saveError: TSaveError;
  /**
   * The editor is open. The banner's draft offers (restore/discard, load the
   * stashed revision) belong to reading mode, where the user decides what to
   * edit before opening the editor: showing the same actions next to the
   * toolbar's publish/stash buttons stacks two button groups on top of each
   * other. Failure notices stay visible in both modes.
   */
  isEditing: boolean;
  onRestoreDraft: () => void;
  onDiscardDraft: () => void;
  onLoadServerDraft: () => void;
  onDiscardServerDraft: () => void;
  onRetry: () => void;
};

/**
 * Tells the user about a save that did not land.
 *
 * `usePageSave` keeps every unsaved revision in this browser, so nothing is
 * ever lost silently — this is where the user learns about it and decides what
 * to do with the kept copy.
 */
export function PageSaveBanner(props: Props) {
  const {
    className,
    conflict,
    draft,
    serverDraft,
    saveError,
    isEditing,
    onRestoreDraft,
    onDiscardDraft,
    onLoadServerDraft,
    onDiscardServerDraft,
    onRetry,
  } = props;
  const { t } = useTranslation();

  // the conflict, a failed save and a draft from an earlier visit all mean the
  // same thing: an unpublished revision is waiting. The draft offers are
  // reading-mode decisions: while the editor is open the toolbar owns the
  // actions (publish / stash).
  const showDraftOffer = !isEditing;
  // `saveError === "reverted"` is the first-publisher-wins refusal: it may
  // arrive without a `conflict` object (PAGE_VERSION_CONFLICT carries no saved_by),
  // and it must still be explained while the editor is open — otherwise the
  // failure is silent and the draft only surfaces after a reload.
  const message =
    conflict || saveError === "reverted"
      ? t("page_editor.draft_conflict")
      : saveError === "failed"
        ? t("page_editor.save_failed")
        : saveError === "draft-failed"
          ? t("page_editor.draft_failed")
          : serverDraft && showDraftOffer
            ? t("page_editor.server_draft_found")
            : draft && showDraftOffer
              ? t("page_editor.unsaved_draft")
              : null;

  // the stashed revision lives on the server and only this user can see it
  const hasServerDraft = saveError !== "draft-failed" && !!serverDraft;

  if (!message) return null;

  return (
    <div className={cn("flex items-center gap-2 border-b border-subtle-1 bg-layer-2 px-4 py-2.5", className)}>
      <div className="mx-auto flex items-center gap-2 text-secondary">
        <span className="text-amber-500">
          <WarningTriangleOutline />
        </span>
        <span className="text-sm font-medium">{message}</span>
        {conflict?.savedBy && (
          <span className="text-sm text-tertiary">{conflict.savedBy}</span>
        )}
      </div>
      <div className="ml-auto flex items-center gap-2">
        {saveError === "failed" && (
          <button
            type="button"
            onClick={onRetry}
            className="rounded-sm border border-subtle bg-layer-1 px-2 py-1 text-13 font-medium text-secondary transition-colors hover:bg-layer-transparent-hover hover:text-primary"
          >
            {t("page_editor.retry")}
          </button>
        )}
        {showDraftOffer && hasServerDraft && (
          <>
            <button
              type="button"
              onClick={onLoadServerDraft}
              className="rounded-sm border border-subtle bg-layer-1 px-2 py-1 text-13 font-medium text-secondary transition-colors hover:bg-layer-transparent-hover hover:text-primary"
            >
              {t("page_editor.load_draft")}
            </button>
            <button
              type="button"
              onClick={onDiscardServerDraft}
              className="rounded-sm px-2 py-1 text-13 font-medium text-secondary transition-colors hover:bg-layer-transparent-hover hover:text-primary"
            >
              {t("page_editor.discard_draft")}
            </button>
          </>
        )}
        {showDraftOffer && !hasServerDraft && draft && (
          <>
            <button
              type="button"
              onClick={onRestoreDraft}
              className="rounded-sm border border-subtle bg-layer-1 px-2 py-1 text-13 font-medium text-secondary transition-colors hover:bg-layer-transparent-hover hover:text-primary"
            >
              {t("page_editor.restore_draft")}
            </button>
            <button
              type="button"
              onClick={onDiscardDraft}
              className="rounded-sm px-2 py-1 text-13 font-medium text-secondary transition-colors hover:bg-layer-transparent-hover hover:text-primary"
            >
              {t("page_editor.discard_draft")}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
