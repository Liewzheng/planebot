/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback } from "react";
// plane imports
import type { TFileHandler } from "@plane/editor";
import { getEditorAssetDownloadSrc, getEditorAssetSrc, editorAssetApiVersion } from "@plane/utils";
// hooks
import { useEditorAsset } from "@/hooks/store/use-editor-asset";
// plane web hooks
import { useExtendedEditorConfig } from "@/hooks/editor/use-extended-editor-config";
import { useFileSize } from "@/hooks/use-file-size";
// services
import { FileService } from "@/services/file.service";
const fileService = new FileService();

type TArgs = {
  projectId?: string;
  uploadFile: TFileHandler["upload"];
  duplicateFile: TFileHandler["duplicate"];
  workspaceId: string;
  workspaceSlug: string;
};

export const useEditorConfig = () => {
  // store hooks
  const { assetsUploadPercentage } = useEditorAsset();
  // file size
  const { maxFileSize } = useFileSize();
  const { getExtendedEditorFileHandlers } = useExtendedEditorConfig();

  const getEditorFileHandlers = useCallback(
    (args: TArgs): TFileHandler => {
      const { projectId, uploadFile, duplicateFile, workspaceId, workspaceSlug } = args;

      return {
        assetsUploadStatus: assetsUploadPercentage,
        cancel: fileService.cancelUpload,
        checkIfAssetExists: async (assetId: string) => {
          const res = await fileService.checkIfAssetExists(workspaceSlug, assetId);
          return res?.exists ?? false;
        },
        delete: async (src: string) => {
          // Match the restore routing by URL family: a V2 URL or a bare asset
          // id goes to the v2 endpoint; only the legacy `/api/workspaces/file-assets/`
          // shape goes to the v1 endpoint. The "starts with http" heuristic
          // mis-routed every V2 image (also absolute, once getFileURL adds
          // the API base) into a silent 404 and orphaned the file.
          if (!src) return;
          if (editorAssetApiVersion(src) === "v1") {
            await fileService.deleteOldWorkspaceAsset(workspaceId, src);
          } else {
            await fileService.deleteNewAsset(
              getEditorAssetSrc({
                assetId: src,
                projectId,
                workspaceSlug,
              }) ?? ""
            );
          }
        },
        getAssetDownloadSrc: async (path) => {
          if (!path) return "";
          if (path?.startsWith("http")) {
            return path;
          } else {
            return (
              getEditorAssetDownloadSrc({
                assetId: path,
                projectId,
                workspaceSlug,
              }) ?? ""
            );
          }
        },
        getAssetSrc: async (path) => {
          if (!path) return "";
          if (path?.startsWith("http")) {
            return path;
          } else {
            return (
              getEditorAssetSrc({
                assetId: path,
                projectId,
                workspaceSlug,
              }) ?? ""
            );
          }
        },
        restore: async (src: string) => {
          // The src is one of three things: a bare asset id (the editor's
          // private-bucket short form, written into the document before the
          // editor turns it into a full URL), a full V2 URL, or — for pages
          // authored before V2 shipped — a full V1 URL. The two restore
          // endpoints take very different shapes (V2 wants the workspace slug
          // and asset UUID; V1 wants the workspace UUID and asset key), so
          // route by the URL family rather than by whether the src happens to
          // be absolute — V2 URLs are absolute too once `getFileURL` adds the
          // API base, and the old "starts with http → V1" heuristic made
          // every V2 image silently 404 on draft load.
          if (!src) return;
          if (editorAssetApiVersion(src) === "v1") {
            await fileService.restoreOldEditorAsset(workspaceId, src);
          } else {
            await fileService.restoreNewAsset(workspaceSlug, src);
          }
        },
        upload: uploadFile,
        duplicate: duplicateFile,
        uploadMermaidDiagram: projectId
          ? async (svgBlob: Blob, sourceHash: string) =>
              fileService.uploadMermaidDiagram(workspaceSlug, projectId, svgBlob, sourceHash)
          : undefined,
        validation: {
          maxFileSize,
        },
        ...getExtendedEditorFileHandlers({ projectId, workspaceSlug }),
      };
    },
    [assetsUploadPercentage, getExtendedEditorFileHandlers, maxFileSize]
  );

  return {
    getEditorFileHandlers,
  };
};
