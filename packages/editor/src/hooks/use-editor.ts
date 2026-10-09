/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEditorState, useEditor as useTiptapEditor } from "@tiptap/react";
import { useImperativeHandle, useEffect } from "react";
import type { MarkdownStorage } from "tiptap-markdown";
import type * as Y from "yjs";
// extensions
import { CoreEditorExtensions } from "@/extensions";
// helpers
import { getEditorRefHelpers } from "@/helpers/editor-ref";
// props
import { CoreEditorProps } from "@/props";
// types
import type { TEditorHookProps } from "@/types";

declare module "@tiptap/core" {
  interface Storage {
    markdown: MarkdownStorage;
  }
}

export const useEditor = (props: TEditorHookProps) => {
  const {
    autofocus = false,
    disabledExtensions,
    editable = true,
    editorClassName = "",
    editorProps = {},
    enableHistory,
    extendedEditorProps,
    extensions = [],
    fileHandler,
    flaggedExtensions,
    forwardedRef,
    getEditorMetaData,
    handleEditorReady,
    id = "",
    initialValue,
    isTouchDevice,
    issueReference,
    mentionHandler,
    onAssetChange,
    onChange,
    onEditorFocus,
    onTransaction,
    placeholder,
    showPlaceholderOnEmpty,
    tabIndex,
    provider,
    value,
  } = props;

  // The Collaboration extension binds a Y.Doc at editor creation. To switch
  // docs (e.g. the live provider.document -> a draft side doc) without
  // carrying the old binding over, the editor must be recreated. We pick
  // out the doc the Collaboration extension was configured with and use
  // its guid as a recreation dep; switching the active doc changes the
  // guid, the deps array changes, tiptap-react recreates the editor.
  const boundDoc = (() => {
    for (const ext of extensions) {
      const name = (ext as { name?: string }).name;
      if (name === "collaboration") {
        const doc = (ext as { options?: { document?: Y.Doc } }).options?.document;
        if (doc) return doc;
      }
    }
    return null;
  })();
  const boundDocGuid = boundDoc?.guid ?? null;

  const editor = useTiptapEditor(
    {
      editable,
      immediatelyRender: false,
      shouldRerenderOnTransaction: false,
      autofocus,
      parseOptions: { preserveWhitespace: true },
      editorProps: {
        ...CoreEditorProps({
          editorClassName,
        }),
        ...editorProps,
      },
      extensions: [
        ...CoreEditorExtensions({
          disabledExtensions,
          editable,
          enableHistory,
          extendedEditorProps,
          fileHandler,
          flaggedExtensions,
          getEditorMetaData,
          isTouchDevice,
          issueReference,
          mentionHandler,
          placeholder,
          showPlaceholderOnEmpty,
          tabIndex,
          provider,
        }),
        ...extensions,
      ],
      content: initialValue,
      onCreate: () => handleEditorReady?.(true),
      onTransaction: () => {
        onTransaction?.();
      },
      onUpdate: ({ editor, transaction }) => {
        // Check if this update is only due to migration update
        const isMigrationUpdate = transaction?.getMeta("uniqueIdOnlyChange") === true;
        onChange?.(editor.getJSON(), editor.getHTML(), { isMigrationUpdate });
      },
      onDestroy: () => handleEditorReady?.(false),
      onFocus: onEditorFocus,
    },
    // boundDocGuid flips when the active document changes (live doc ->
    // draft side doc), which is the cue to recreate the editor against
    // the new binding; a null guid falls back to editable-only changes
    [editable, boundDocGuid]
  );

  // Effect for syncing SWR data
  useEffect(() => {
    // value is null when intentionally passed where syncing is not yet
    // supported and value is undefined when the data from swr is not populated
    if (value == null) return;
    if (editor) {
      const { uploadInProgress: isUploadInProgress } = editor.storage.utility;
      if (!editor.isDestroyed && !isUploadInProgress) {
        try {
          editor.commands.setContent(value, false, {
            preserveWhitespace: true,
          });
          if (editor.state.selection) {
            const docLength = editor.state.doc.content.size;
            const relativePosition = Math.min(editor.state.selection.from, docLength - 1);
            editor.commands.setTextSelection(relativePosition);
          }
        } catch (error) {
          console.error("Error syncing editor content with external value:", error);
        }
      }
    }
  }, [editor, value, id]);

  // update assets upload status
  useEffect(() => {
    if (!editor) return;
    const assetsUploadStatus = fileHandler.assetsUploadStatus;
    editor.commands.updateAssetsUploadStatus?.(assetsUploadStatus);
  }, [editor, fileHandler.assetsUploadStatus]);

  // subscribe to assets list changes
  const assetsList = useEditorState({
    editor,
    selector: ({ editor }) => ({
      assets: editor?.storage.utility?.assetsList ?? [],
    }),
  });
  // trigger callback when assets list changes
  useEffect(() => {
    const assets = assetsList?.assets;
    if (!assets || !onAssetChange) return;
    onAssetChange(assets);
  }, [assetsList?.assets, onAssetChange]);

  useImperativeHandle(
    forwardedRef,
    () =>
      getEditorRefHelpers({
        editor,
        getEditorMetaData,
        provider,
        activeDocument: boundDoc,
      }),
    [editor, getEditorMetaData, provider, boundDoc]
  );

  if (!editor) {
    return null;
  }

  return editor;
};
