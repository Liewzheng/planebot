/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { observer } from "mobx-react";
// plane imports
import type { CollaborationState, EditorRefApi } from "@plane/editor";
import type { TDocumentPayload, TPage, TPageVersion, TWebhookConnectionQueryParams } from "@plane/types";
// hooks
import { usePageFallback } from "@/hooks/use-page-fallback";
import { usePageSave } from "@/hooks/use-page-save";
import type { PageUpdateHandler, TCustomEventHandlers } from "@/hooks/use-realtime-page-events";
import { usePagesPaneExtensions, useExtendedEditorProps } from "@/hooks/pages";
import type { EPageStoreType } from "@/hooks/store";
// store
import type { TPageInstance } from "@/store/pages/base-page";
// local imports
import { PageNavigationPaneRoot } from "../navigation-pane";
import { PageVersionsOverlay } from "../version";
import { PagesVersionEditor } from "../version/editor";
import { ContentLimitBanner } from "./content-limit-banner";
import { PageEditorBody } from "./editor-body";
import type { TEditorBodyConfig, TEditorBodyHandlers } from "./editor-body";
import { PageSaveBanner } from "./save-banner";
import { PageEditorToolbarRoot } from "./toolbar";

export type TPageRootHandlers = {
  create: (payload: Partial<TPage>) => Promise<Partial<TPage> | undefined>;
  /** the caller's own unpublished revision (PLANE-77) */
  fetchDraft: () => Promise<{ description_html: string | null } | undefined>;
  updateDraft: (payload: { description_html: string }) => Promise<{ description_html: string } | undefined>;
  deleteDraft: () => Promise<void>;
  fetchAllVersions: (pageId: string) => Promise<TPageVersion[] | undefined>;
  fetchDescriptionBinary: () => Promise<ArrayBuffer>;
  fetchVersionDetails: (pageId: string, versionId: string) => Promise<TPageVersion | undefined>;
  restoreVersion: (pageId: string, versionId: string) => Promise<void>;
  updateDescription: (document: TDocumentPayload) => Promise<{ updated_at?: string } | undefined>;
  /** renames the page: the title is part of what an author publishes */
  updateName: (name: string) => Promise<void>;
} & TEditorBodyHandlers;

export type TPageRootConfig = TEditorBodyConfig;

type TPageRootProps = {
  config: TPageRootConfig;
  handlers: TPageRootHandlers;
  page: TPageInstance;
  storeType: EPageStoreType;
  webhookConnectionParams: TWebhookConnectionQueryParams;
  projectId?: string;
  workspaceSlug: string;
  customRealtimeEventHandlers?: TCustomEventHandlers;
};



export const PageRoot = observer(function PageRoot(props: TPageRootProps) {
  const {
    config,
    handlers,
    page,
    projectId,
    storeType,
    webhookConnectionParams,
    workspaceSlug,
    customRealtimeEventHandlers,
  } = props;
  // states
  const [editorReady, setEditorReady] = useState(false);
  const [collaborationState, setCollaborationState] = useState<CollaborationState | null>(null);
  const [showContentTooLargeBanner, setShowContentTooLargeBanner] = useState(false);
  // reading mode is the default: the page opens read-only and only becomes
  // editable after the user explicitly clicks "Edit"
  const [isEditing, setIsEditing] = useState(false);
  // an unpublished draft the next editing session starts from (PLANE-77)
  const [draftToLoad, setDraftToLoad] = useState<string | null>(null);
  // bumped whenever the editor is (re)created: the save hook re-anchors its
  // baseline and dirty subscription to the new instance (edit mode toggles,
  // draft restores)
  const [editorEpoch, setEditorEpoch] = useState(0);
  // refs
  const editorRef = useRef<EditorRefApi>(null);
  /** the page name as everyone else sees it, so publishing can tell if the title changed (PLANE-78) */
  const publishedTitleRef = useRef<string | null>(null);
  // derived values
  const {
    id: pageId,
    isContentEditable,
    editor: { setEditorRef },
  } = page;
  // a page is editable only when the user has permission AND has entered editing mode
  const isEditorEditable = isContentEditable && isEditing;
  // page fallback
  const { isFetchingFallbackBinary } = usePageFallback({
    editorRef,
    fetchPageDescription: handlers.fetchDescriptionBinary,
    page,
    collaborationState,
  });
  // saving: nothing the editor does is written to the server until the user
  // asks for it (PLANE-76)
  const {
    isDirty,
    isSaving,
    lastSavedAt,
    draft,
    serverDraft,
    saveError,
    draftError,
    save,
    stashAsDraft,
    discardServerDraft,
    discardDraft,
  } = usePageSave({
    pageId: page.id ?? "",
    editorRef,
    enabled: editorReady,
    isEditing,
    editorEpoch,
    updateDescription: handlers.updateDescription,
    fetchDraft: handlers.fetchDraft,
    updateDraft: handlers.updateDraft,
    deleteDraft: handlers.deleteDraft,
    pageUpdatedAt: page.updated_at,
    pageDescriptionHtml: page.description_html,
  });
  const draftConflict = draft?.reason === "conflict" ? (draft.conflict ?? null) : null;
  // Editing starts from the shared document, so it has to be loaded: seeding a
  // session from a document that has not arrived would publish an empty page.
  const isDocumentReady =
    !!collaborationState && (collaborationState.isServerSynced || collaborationState.isServerDisconnected);

  const handleStartEditing = useCallback(() => {
    publishedTitleRef.current = page.name ?? "";
    setDraftToLoad(null);
    setIsEditing(true);
  }, [page.name]);

  // Publishing is the way out of edit mode: one action, so the toolbar does not
  // offer two buttons that both look like "finish" (PLANE-76). A publish that
  // did not land keeps the editor open, with the banner explaining why.
  // `Cmd`/`Ctrl`+`S` publishes without leaving.
  const handlePublishAndFinish = useCallback(async () => {
    const published = await save();
    if (!published) return;

    // The title travels with the content: it is part of the revision being
    // published, and a rename is visible to everybody.
    const title = (page.name ?? "").trim();
    if (title && title !== publishedTitleRef.current) {
      try {
        await handlers.updateName(title);
        publishedTitleRef.current = title;
      } catch (error) {
        console.error("Could not publish the page title:", error);
      }
    }

    setIsEditing(false);
  }, [handlers, page.name, save]);

  // Stashing is the other way out of edit mode: the work is kept for this user
  // only, and the page (what everybody else reads) stays on the published
  // revision.
  const handleStashDraft = useCallback(async () => {
    const stashed = await stashAsDraft();
    if (stashed) setIsEditing(false);
  }, [stashAsDraft]);

  // A draft is loaded INTO a new editing session, never into the shared
  // document: writing it there is what would make it visible to others.
  const handleLoadServerDraft = useCallback(() => {
    if (!serverDraft) return;
    publishedTitleRef.current = page.name ?? "";
    setDraftToLoad(serverDraft);
    setIsEditing(true);
  }, [page.name, serverDraft]);

  const handleDiscardServerDraft = useCallback(() => {
    void discardServerDraft();
  }, [discardServerDraft]);

  const handleRestoreLocalDraft = useCallback(() => {
    if (!draft) return;
    publishedTitleRef.current = page.name ?? "";
    setDraftToLoad(draft.html);
    discardDraft();
    setIsEditing(true);
  }, [discardDraft, draft, page.name]);

  // leave editing mode when navigating to another page
  useEffect(() => {
    setIsEditing(false);
  }, [pageId]);

  const handleEditorReady = useCallback(
    (status: boolean) => {
      setEditorReady(status);
      // every (re)creation of the editor invalidates the save hook's baseline
      // and transaction subscription
      if (status) setEditorEpoch((epoch) => epoch + 1);
      if (editorRef.current && !page.editor.editorRef) {
        setEditorRef(editorRef.current);
      }
    },
    [page.editor.editorRef, setEditorRef]
  );

  useEffect(() => {
    const timer = setTimeout(() => setEditorRef(editorRef.current), 0);
    return () => clearTimeout(timer);
  }, [isContentEditable, isEditorEditable, setEditorRef]);

  // Get extensions and navigation logic from hook
  const {
    editorExtensionHandlers,
    navigationPaneExtensions,
    handleOpenNavigationPane,
    handleCloseNavigationPane,
    isNavigationPaneOpen,
  } = usePagesPaneExtensions({
    page,
    editorRef,
  });

  // Type-safe error handler for content too large errors
  const errorHandler: PageUpdateHandler<"error"> = (params) => {
    const { data } = params;

    // Check if it's content too large error
    if (data.error_code === "content_too_large") {
      setShowContentTooLargeBanner(true);
    }

    // Call original error handler if exists
    customRealtimeEventHandlers?.error?.(params);
  };

  const mergedCustomEventHandlers: TCustomEventHandlers = {
    ...customRealtimeEventHandlers,
    error: errorHandler,
  };

  // Get extended editor extensions configuration
  const extendedEditorProps = useExtendedEditorProps({
    workspaceSlug,
    page,
    storeType,
    fetchEntity: handlers.fetchEntity,
    getRedirectionLink: handlers.getRedirectionLink,
    extensionHandlers: editorExtensionHandlers,
    projectId,
  });

  // Restoring a revision is a write: it goes through the API (`/versions/<id>/restore/`),
  // which rebuilds the document formats from the restored html and drops the
  // collaborative document, so every client reloads the restored revision.
  const handleRestoreVersion = useCallback(
    async (versionId: string) => {
      const targetPageId = page.id;
      if (!targetPageId) return;
      await handlers.restoreVersion(targetPageId, versionId);
    },
    [handlers, page.id]
  );

  // reset editor ref on unmount
  useEffect(
    () => () => {
      setEditorRef(null);
    },
    [setEditorRef]
  );

  return (
    <div className="relative flex size-full overflow-hidden transition-all duration-300 ease-in-out">
      <div className="flex size-full flex-col overflow-hidden">
        <PageVersionsOverlay
          editorComponent={PagesVersionEditor}
          fetchVersionDetails={handlers.fetchVersionDetails}
          handleRestore={handleRestoreVersion}
          pageId={page.id ?? ""}
          pageName={page.name}
          restoreEnabled={isEditorEditable}
          storeType={storeType}
        />
        <PageSaveBanner
          conflict={draftConflict}
          draft={draft}
          serverDraft={serverDraft}
          saveError={draftError ? "draft-failed" : saveError}
          isEditing={isEditing}
          onDiscardDraft={discardDraft}
          onDiscardServerDraft={handleDiscardServerDraft}
          onLoadServerDraft={handleLoadServerDraft}
          onRestoreDraft={handleRestoreLocalDraft}
          onRetry={save}
        />
        <PageEditorToolbarRoot
          handleOpenNavigationPane={handleOpenNavigationPane}
          isDirty={isDirty}
          isEditing={isEditing}
          isNavigationPaneOpen={isNavigationPaneOpen}
          isSaving={isSaving}
          lastSavedAt={lastSavedAt}
          canStartEditing={isDocumentReady}
          onSave={handlePublishAndFinish}
          onStashDraft={handleStashDraft}
          onStartEditing={handleStartEditing}
          page={page}
        />
        {showContentTooLargeBanner && <ContentLimitBanner className="px-page-x" />}
        <PageEditorBody
          config={config}
          customRealtimeEventHandlers={mergedCustomEventHandlers}
          editorReady={editorReady}
          editorForwardRef={editorRef}
          handleEditorReady={handleEditorReady}
          handleOpenNavigationPane={handleOpenNavigationPane}
          handlers={handlers}
          isEditable={isEditorEditable}
          isNavigationPaneOpen={isNavigationPaneOpen}
          page={page}
          projectId={projectId}
          storeType={storeType}
          webhookConnectionParams={webhookConnectionParams}
          workspaceSlug={workspaceSlug}
          extendedEditorProps={extendedEditorProps}
          draftHtml={draftToLoad}
          isFetchingFallbackBinary={isFetchingFallbackBinary}
          onCollaborationStateChange={setCollaborationState}
        />
      </div>
      <PageNavigationPaneRoot
        storeType={storeType}
        handleClose={handleCloseNavigationPane}
        isNavigationPaneOpen={isNavigationPaneOpen}
        page={page}
        versionHistory={{
          fetchAllVersions: handlers.fetchAllVersions,
          fetchVersionDetails: handlers.fetchVersionDetails,
        }}
        extensions={navigationPaneExtensions}
      />
    </div>
  );
});
