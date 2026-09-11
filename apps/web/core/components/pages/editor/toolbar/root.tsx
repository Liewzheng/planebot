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
  isEditing: boolean;
  isNavigationPaneOpen: boolean;
  onFinishEditing: () => void;
  onStartEditing: () => void;
  page: TPageInstance;
};

export const PageEditorToolbarRoot = observer(function PageEditorToolbarRoot(props: Props) {
  const { handleOpenNavigationPane, isEditing, isNavigationPaneOpen, onFinishEditing, onStartEditing, page } = props;
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

  return (
    <>
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
              {isEditing && (
                <button
                  type="button"
                  onClick={onFinishEditing}
                  className="flex items-center gap-1 rounded-sm border border-subtle bg-layer-1 px-2 py-1 text-13 font-medium text-secondary transition-colors hover:bg-layer-transparent-hover hover:text-primary"
                >
                  <TickOutline className="size-3.5" />
                  {t("page_editor.finish_editing")}
                </button>
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
        <div className="absolute top-0 right-0 z-10 flex h-[52px] items-center gap-2 px-page-x">
          {isContentEditable && !isEditing && (
            <Tooltip label={t("page_editor.start_editing")}>
              <button
                type="button"
                onClick={onStartEditing}
                className="flex items-center gap-1 rounded-sm border border-subtle bg-layer-1 px-2 py-1 text-13 font-medium text-secondary transition-colors hover:bg-layer-transparent-hover hover:text-primary"
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
    </>
  );
});
