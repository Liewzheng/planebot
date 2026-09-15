/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
import { ShowOutline, WarningTriangleOutline } from "@makeplane/propel/icons";
// plane imports
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { TPageVersion } from "@plane/types";
import { renderFormattedDate, renderFormattedTime } from "@plane/utils";
// helpers
import type { EPageStoreType } from "@/hooks/store";
import { usePageFilters } from "@/hooks/use-page-filters";
// local imports
import { getPageContentColumnClassName } from "../page-content-column";
import type { TVersionEditorProps } from "./editor";

type Props = {
  activeVersion: string | null;
  editorComponent: React.FC<TVersionEditorProps>;
  fetchVersionDetails: (pageId: string, versionId: string) => Promise<TPageVersion | undefined>;
  handleClose: () => void;
  handleRestore: (descriptionHTML: string) => Promise<void>;
  pageId: string;
  pageName?: string;
  restoreEnabled: boolean;
  storeType: EPageStoreType;
};

export const PageVersionsMainContent = observer(function PageVersionsMainContent(props: Props) {
  const {
    activeVersion,
    editorComponent,
    fetchVersionDetails,
    handleClose,
    handleRestore,
    pageId,
    pageName,
    restoreEnabled,
    storeType,
  } = props;
  // states
  const [isRestoring, setIsRestoring] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);
  // page filters
  const { isFullWidth } = usePageFilters();

  const {
    data: versionDetails,
    error: versionDetailsError,
    mutate: mutateVersionDetails,
  } = useSWR(
    pageId && activeVersion ? `PAGE_VERSION_${activeVersion}` : null,
    pageId && activeVersion ? () => fetchVersionDetails(pageId, activeVersion) : null
  );

  const handleRestoreVersion = async () => {
    if (!restoreEnabled) return;
    setIsRestoring(true);
    await handleRestore(versionDetails?.description_html ?? "<p></p>")
      .then(() => {
        setToast({
          type: TOAST_TYPE.SUCCESS,
          title: "Page version restored.",
        });
        handleClose();
      })
      .catch(() =>
        setToast({
          type: TOAST_TYPE.ERROR,
          title: "Failed to restore page version.",
        })
      )
      .finally(() => setIsRestoring(false));
  };

  const handleRetry = async () => {
    setIsRetrying(true);
    await mutateVersionDetails();
    setIsRetrying(false);
  };

  const VersionEditor = editorComponent;

  return (
    // id + container name pair this view with the same layout rules (#page-content-container)
    // that govern the live document, so the content column is identical in both modes
    <div id="page-version-container" className="flex flex-grow flex-col overflow-hidden">
      {versionDetailsError ? (
        <div className="grid flex-grow place-items-center">
          <div className="flex flex-col items-center gap-4 text-center">
            <span className="grid size-11 flex-shrink-0 place-items-center text-tertiary">
              <WarningTriangleOutline className="size-10" />
            </span>
            <div>
              <h6 className="text-16 font-semibold">Something went wrong!</h6>
              <p className="text-13 text-tertiary">The version could not be loaded, please try again.</p>
            </div>
            <Button variant="link" onClick={handleRetry} loading={isRetrying}>
              Try again
            </Button>
          </div>
        </div>
      ) : (
        <>
          <div className="flex min-h-14 items-center border-b border-subtle py-3">
            {/* same page-header-container + content column classes as the live page header */}
            <div className="page-header-container flex w-full items-center justify-between gap-2">
              <div className={getPageContentColumnClassName(isFullWidth)}>
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-4">
                    <h6 className="text-14 font-medium">
                      {versionDetails
                        ? `${renderFormattedDate(versionDetails.last_saved_at)} ${renderFormattedTime(versionDetails.last_saved_at)}`
                        : "Loading version details"}
                    </h6>
                    <span className="flex flex-shrink-0 items-center gap-1 rounded-sm bg-accent-primary/20 px-1.5 py-1 text-11 font-medium text-accent-primary">
                      <ShowOutline className="size-3 flex-shrink-0" />
                      View only
                    </span>
                  </div>
                  {restoreEnabled && (
                    <Button
                      variant="primary"
                      className="flex-shrink-0"
                      onClick={handleRestoreVersion}
                      loading={isRestoring}
                    >
                      {isRestoring ? "Restoring" : "Restore"}
                    </Button>
                  )}
                </div>
              </div>
            </div>
          </div>
          <div className="vertical-scrollbar scrollbar-sm h-full overflow-y-scroll pt-8">
            <VersionEditor
              activeVersion={activeVersion}
              pageName={pageName}
              storeType={storeType}
              versionDetails={versionDetails}
            />
          </div>
        </>
      )}
    </div>
  );
});
