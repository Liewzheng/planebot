/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { forwardRef, useCallback, useImperativeHandle, useRef, useState } from "react";
import { useTranslation } from "@plane/i18n";
import { ImageFileIcon } from "@plane/propel/icons";
import type { AxiosRequestConfig } from "axios";
import type { EFileAssetType, ITestRunAttachment } from "@plane/types";
import { useDropzone } from "react-dropzone";
import type { FileRejection } from "react-dropzone";
// helpers
import { useFileSize } from "@/hooks/use-file-size";
// services
import { FileService } from "@/services/file.service";
// local
import { isAcceptableAttachment } from "../constants";

type Props = {
  workspaceSlug: string;
  /**
   * The entity the upload binds to. The v2 asset endpoint binds the file to
   * the record as it stores it («上传即绑定»), so no second call is needed —
   * and the endpoint refuses a file on a completed batch itself.
   */
  entityType: EFileAssetType.TEST_RUN_STEP_RECORD | EFileAssetType.TEST_RUN_CASE_EVIDENCE;
  /** The id of the step record or of the case record. */
  entityIdentifier: string;
  disabled?: boolean;
  /** Called after a file landed — the caller refreshes the record. */
  onUploaded: () => void;
  /** Called with the served URL of an uploaded image (insert at the caret). */
  onUploadedMarkdown?: (url: string) => void;
  /** The screen the button sits on, for the label of the drop zone. */
  compact?: boolean;
};

type TUploadState = { name: string; progress: number } | null;

/**
 * The upload of the screenshots of one step (its measured value) or of one
 * case record (its evidence).
 *
 * The endpoint allows images only (jpeg/png/webp/jpg/gif) — logs and command
 * output travel as Markdown text in the field next to this control, not as a
 * file. The copy says so, so the gap is visible instead of surprising.
 *
 * Drag-drop, a file picker and the camera of a phone are the three entries:
 * the camera input carries `capture="environment"`, which a phone opens as
 * the rear camera; a desktop ignores the attribute and shows the picker.
 */
export type TTestCaseRunAttachmentUploaderHandle = {
  /** Open the file picker — the image button of the Markdown toolbar. */
  openPicker: () => void;
};

export const TestCaseRunAttachmentUploader = forwardRef<TTestCaseRunAttachmentUploaderHandle, Props>(
  function TestCaseRunAttachmentUploader(props: Props, ref) {
    const {
      workspaceSlug,
      entityType,
      entityIdentifier,
      disabled = false,
      onUploaded,
      onUploadedMarkdown,
      compact = false,
    } = props;
    const { t } = useTranslation();
    // hooks
    const { maxFileSize } = useFileSize();
    // states
    const [upload, setUpload] = useState<TUploadState>(null);
    const [error, setError] = useState<string | null>(null);
    // refs
    const cameraRef = useRef<HTMLInputElement | null>(null);

    const uploadFile = useCallback(
      async (file: File) => {
        setError(null);

        if (!isAcceptableAttachment(file)) {
          setError(t("project_test_case.execution.attachments.invalid_type"));
          return;
        }
        if (file.size > maxFileSize) {
          setError(
            t("project_test_case.execution.attachments.too_large", { size: Math.round(maxFileSize / 1024 / 1024) })
          );
          return;
        }

        setUpload({ name: file.name, progress: 0 });

        const onProgress: AxiosRequestConfig["onUploadProgress"] = (event) => {
          if (!event.total) return;
          setUpload({ name: file.name, progress: Math.round((event.loaded / event.total) * 100) });
        };

        try {
          // The existing upload primitive of the app: it asks for a presigned
          // URL, uploads the file, and marks the asset uploaded.
          const response = await new FileService().uploadWorkspaceAsset(
            workspaceSlug,
            { entity_identifier: entityIdentifier, entity_type: entityType },
            file,
            onProgress
          );

          const attachment: ITestRunAttachment = {
            id: response.asset_id,
            asset_id: response.asset_id,
            name: file.name,
            size: file.size,
            url: response.asset_url,
          };

          setUpload(null);
          onUploadedMarkdown?.(attachment.url);
          onUploaded();
        } catch (uploadError) {
          setUpload(null);
          const envelope = uploadError as { error?: string } | undefined;
          setError(envelope?.error || t("project_test_case.execution.attachments.upload_failed"));
        }
      },
      [entityIdentifier, entityType, maxFileSize, onUploaded, onUploadedMarkdown, t, workspaceSlug]
    );

    const onDrop = useCallback(
      (acceptedFiles: File[]) => {
        const file = acceptedFiles[0];
        if (!file) return;
        void uploadFile(file);
      },
      [uploadFile]
    );

    // The drop zone refuses an oversize or non-image file before `onDrop`
    // sees it; without this callback the drop would silently do nothing and
    // the tester would think the upload was ignored.
    const onDropRejected = useCallback(
      (rejections: FileRejection[]) => {
        const first = rejections[0];
        if (!first) return;

        const tooLarge = first.errors.some((entry) => entry.code === "file-too-large");
        setError(
          tooLarge
            ? t("project_test_case.execution.attachments.too_large", {
                size: Math.round(maxFileSize / 1024 / 1024),
              })
            : t("project_test_case.execution.attachments.invalid_type")
        );
      },
      [maxFileSize, t]
    );

    const { getRootProps, getInputProps, isDragActive, open } = useDropzone({
      onDrop,
      onDropRejected,
      accept: { "image/*": [".png", ".jpg", ".jpeg", ".webp", ".gif"] },
      maxSize: maxFileSize,
      multiple: false,
      disabled: disabled || Boolean(upload),
      noKeyboard: true,
    });

    useImperativeHandle(ref, () => ({ openPicker: () => open() }), [open]);

    const isBusy = Boolean(upload);

    return (
      <div className="flex min-w-0 flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <div
            {...getRootProps()}
            className={[
              "flex min-w-0 flex-1 items-center justify-center gap-2 rounded-md border border-dashed px-3 py-2 text-11",
              disabled
                ? "cursor-not-allowed border-subtle-1 text-tertiary"
                : "cursor-pointer border-subtle-1 text-tertiary hover:border-accent-strong hover:text-accent-primary",
              isDragActive ? "border-accent-strong bg-accent-primary/5" : "",
              compact ? "" : "min-h-[40px]",
            ].join(" ")}
          >
            <input {...getInputProps()} aria-label={t("project_test_case.execution.attachments.pick_file")} />
            <ImageFileIcon className="size-3.5 shrink-0" />
            <span className="truncate">
              {isDragActive
                ? t("project_test_case.execution.attachments.drop_here")
                : t("project_test_case.execution.attachments.pick_hint")}
            </span>
          </div>

          <button
            type="button"
            disabled={disabled || isBusy}
            onClick={() => cameraRef.current?.click()}
            className="rounded-md border border-subtle-1 px-2.5 py-2 text-11 text-secondary hover:bg-layer-2 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {t("project_test_case.execution.attachments.take_photo")}
          </button>
          <input
            ref={cameraRef}
            type="file"
            accept="image/*"
            capture="environment"
            className="hidden"
            aria-label={t("project_test_case.execution.attachments.take_photo")}
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (file) void uploadFile(file);
            }}
          />
        </div>

        {upload && (
          <div className="flex items-center gap-2 text-11 text-tertiary" role="status" aria-live="polite">
            <span className="truncate">
              {t("project_test_case.execution.attachments.uploading", { name: upload.name, progress: upload.progress })}
            </span>
            <span className="h-1 w-24 overflow-hidden rounded-full bg-layer-3">
              <span className="block h-full bg-accent-primary" style={{ width: `${upload.progress}%` }} />
            </span>
          </div>
        )}

        {error && (
          <p className="text-11 text-danger-primary" role="alert">
            {error}
          </p>
        )}

        <p className="text-11 text-tertiary">{t("project_test_case.execution.attachments.images_only")}</p>
      </div>
    );
  }
);
