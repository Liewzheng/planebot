/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { EditOutline, RightSidePaneOutline, TickOutline } from "@makeplane/propel/icons";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Tooltip } from "@makeplane/propel/components/tooltip";
import { cn } from "@plane/utils";
// components
import { PageToolbar } from "@/components/pages/editor/toolbar";
// hooks
import { usePageFilters } from "@/hooks/use-page-filters";
// store
import type { TPageInstance } from "@/store/pages/base-page";

type Props = {
  handleOpenNavigationPane: () => void;
  isDirty: boolean;
  isEditing: boolean;
  isNavigationPaneOpen: boolean;
  isSaving: boolean;
  lastSavedAt: string | null;
  /** publishes the page and leaves edit mode */
  onSave: () => void;
  /** the document is loaded, so editing can start from the published revision */
  canStartEditing: boolean;
  /** keeps the work as the caller's own unpublished draft, then leaves edit mode */
  onStashDraft: () => void;
  onStartEditing: () => void;
  page: TPageInstance;
};

export const PageEditorToolbarRoot = observer(function PageEditorToolbarRoot(props: Props) {
  const {
    handleOpenNavigationPane,
    isDirty,
    isEditing,
    isNavigationPaneOpen,
    isSaving,
    lastSavedAt,
    canStartEditing,
    onSave,
    onStashDraft,
    onStartEditing,
    page,
  } = props;
  // translation
  const { t } = useTranslation();
  // derived values
  const {
    isContentEditable,
    editor: { editorRef },
  } = page;
  // page filters
  const { isFullWidth, isStickyToolbarEnabled } = usePageFilters();
  // derived values
  // the rich toolbar is only shown while actively editing; reading mode gets the slim corner bar
  const shouldHideToolbar = !isStickyToolbarEnabled || !isContentEditable || !isEditing;
  // the page is only ever written to the server on demand, so the unsaved state
  // has to be visible while it lasts
  const saveStatus = isSaving
    ? t("page_editor.saving")
    : isDirty
      ? t("page_editor.unsaved_status")
      : lastSavedAt
        ? t("page_editor.published_status")
        : null;

  return (
    // relative: the slim corner bar below is positioned against the toolbar's
    // own block, so it never overlaps content rendered above it (the save banner)
    <div className="relative">
      <div
        id="page-toolbar-container"
        className={cn("max-h-[52px] overflow-auto transition-all duration-300 ease-linear", {
          "max-h-0 overflow-hidden": shouldHideToolbar,
        })}
      >
        <div
          className={cn(
            "page-toolbar-content relative hidden min-h-[52px] items-center px-page-x transition-all duration-200 ease-in-out md:flex",
            {
              "wide-layout": isFullWidth,
            }
          )}
        >
          <div className="flex w-full max-w-full items-center justify-between">
            <div className="flex-1">{editorRef && <PageToolbar editorRef={editorRef} />}</div>
            <div className="flex items-center gap-2">
              {saveStatus && (
                <span
                  className={cn("text-13", {
                    "text-secondary": !isDirty || isSaving,
                    "text-amber-500": isDirty && !isSaving,
                  })}
                >
                  {saveStatus}
                </span>
              )}
              {isEditing && (
                <button
                  type="button"
                  onClick={onStashDraft}
                  disabled={isSaving}
                  className="rounded-sm px-2 py-1 text-13 font-medium text-secondary transition-colors hover:bg-layer-transparent-hover hover:text-primary disabled:opacity-60"
                >
                  {t("page_editor.stash_draft")}
                </button>
              )}
              {isEditing && (
                <Tooltip label={t("page_editor.publish_and_finish")}>
                  <button
                    type="button"
                    onClick={onSave}
                    disabled={isSaving}
                    aria-label={t("page_editor.publish_and_finish")}
                    className="flex items-center gap-1 rounded-sm border border-subtle bg-layer-1 px-2 py-1 text-13 font-medium text-secondary transition-colors hover:bg-layer-transparent-hover hover:text-primary disabled:opacity-60"
                  >
                    <TickOutline className="size-3.5" />
                    {isSaving ? t("page_editor.publishing") : t("page_editor.publish")}
                  </button>
                </Tooltip>
              )}
              {!isNavigationPaneOpen && (
                <button
                  type="button"
                  className="grid size-6 shrink-0 place-items-center rounded-sm text-secondary transition-colors hover:bg-layer-transparent-hover hover:text-primary"
                  onClick={handleOpenNavigationPane}
                >
                  <RightSidePaneOutline className="size-3.5" />
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
      {shouldHideToolbar && (
        <div className="flex h-[52px] items-center justify-end gap-2 px-page-x">
          {isContentEditable && !isEditing && (
            <Tooltip label={t("page_editor.start_editing")}>
              <button
                type="button"
                onClick={onStartEditing}
                disabled={!canStartEditing}
                className="flex items-center gap-1 rounded-sm border border-subtle bg-layer-1 px-2 py-1 text-13 font-medium text-secondary transition-colors hover:bg-layer-transparent-hover hover:text-primary disabled:opacity-60"
                aria-label={t("page_editor.start_editing")}
              >
                <EditOutline className="size-3.5" />
                {t("page_editor.start_editing")}
              </button>
            </Tooltip>
          )}
          {!isNavigationPaneOpen && (
            <Tooltip label={t("page_navigation_pane.open_button")}>
              <button
                type="button"
                className="grid size-6 shrink-0 place-items-center rounded-sm text-secondary transition-colors hover:bg-layer-transparent-hover hover:text-primary"
                onClick={handleOpenNavigationPane}
                aria-label={t("page_navigation_pane.open_button")}
              >
                <RightSidePaneOutline className="size-3.5" />
              </button>
            </Tooltip>
          )}
        </div>
      )}
    </div>
  );
});
