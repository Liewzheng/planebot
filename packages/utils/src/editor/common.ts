/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// local imports
import { getFileURL } from "../file";

type TEditorSrcArgs = {
  assetId: string;
  projectId?: string;
  workspaceSlug: string;
};

/**
 * @description generate the file source using assetId
 * @param {TEditorSrcArgs} args
 */
export const getEditorAssetSrc = (args: TEditorSrcArgs): string | undefined => {
  const { assetId, projectId, workspaceSlug } = args;
  let url: string | undefined = "";
  if (projectId) {
    url = getFileURL(`/api/assets/v2/workspaces/${workspaceSlug}/projects/${projectId}/${assetId}/`);
  } else {
    url = getFileURL(`/api/assets/v2/workspaces/${workspaceSlug}/${assetId}/`);
  }
  return url;
};

/**
 * Which endpoint family an image's src belongs to. Pages authored before V2
 * shipped carry V1 URLs (`/api/workspaces/file-assets/<ws>/<asset>/`); pages
 * authored now carry V2 URLs (`/api/assets/v2/workspaces/<slug>/...`). The
 * two restore / delete endpoints take very different arguments, so callers
 * that look at an editor image src must route based on this — and "starts
 * with http" is not a reliable heuristic, because V2 URLs are also absolute
 * once `getFileURL` adds the API base.
 */
export type TEditorAssetApiVersion = "v1" | "v2";

const V1_ASSET_PATH = "/api/workspaces/file-assets/";

/**
 * True when the image src carries an absolute V1 file-asset URL — the only
 * shape the legacy `restoreOldEditorAsset` endpoint will accept. Anything
 * else (a V2 URL, a bare asset id, an empty string, a domain mismatch) routes
 * to the V2 endpoint.
 */
export const isV1EditorAssetSrc = (src: string | null | undefined): boolean => !!src && src.includes(V1_ASSET_PATH);

/**
 * Pick the API family for an image src. A bare id (the editor's private-bucket
 * short form) is V2 by construction; an absolute URL that contains the V1
 * legacy file-asset path is V1; anything else falls back to V2 (the only
 * active family for new uploads).
 */
export const editorAssetApiVersion = (src: string | null | undefined): TEditorAssetApiVersion =>
  isV1EditorAssetSrc(src) ? "v1" : "v2";

/**
 * @description generate the file source using assetId
 * @param {TEditorSrcArgs} args
 */
export const getEditorAssetDownloadSrc = (args: TEditorSrcArgs): string | undefined => {
  const { assetId, projectId, workspaceSlug } = args;
  let url: string | undefined = "";
  if (projectId) {
    url = getFileURL(`/api/assets/v2/workspaces/${workspaceSlug}/projects/${projectId}/download/${assetId}/`);
  } else {
    url = getFileURL(`/api/assets/v2/workspaces/${workspaceSlug}/download/${assetId}/`);
  }
  return url;
};

export const getTextContent = (jsx: React.ReactNode | null | undefined): string => {
  if (!jsx) return "";

  const div = document.createElement("div");
  div.innerHTML = jsx.toString();
  return div.textContent?.trim() ?? "";
};

export const isEditorEmpty = (description: string | undefined): boolean =>
  !description ||
  description === "<p></p>" ||
  description === `<p class="editor-paragraph-block"></p>` ||
  description.trim() === "";

export enum CORE_EXTENSIONS {
  BLOCKQUOTE = "blockquote",
  BOLD = "bold",
  BULLET_LIST = "bulletList",
  CALLOUT = "calloutComponent",
  CHARACTER_COUNT = "characterCount",
  CODE_BLOCK = "codeBlock",
  CODE_INLINE = "code",
  CUSTOM_COLOR = "customColor",
  CUSTOM_IMAGE = "imageComponent",
  CUSTOM_LINK = "link",
  DOCUMENT = "doc",
  DROP_CURSOR = "dropCursor",
  ENTER_KEY = "enterKey",
  GAP_CURSOR = "gapCursor",
  HARD_BREAK = "hardBreak",
  HEADING = "heading",
  HEADINGS_LIST = "headingsList",
  HISTORY = "history",
  HORIZONTAL_RULE = "horizontalRule",
  IMAGE = "image",
  ITALIC = "italic",
  LIST_ITEM = "listItem",
  MARKDOWN_CLIPBOARD = "markdownClipboard",
  MENTION = "mention",
  ORDERED_LIST = "orderedList",
  PARAGRAPH = "paragraph",
  PLACEHOLDER = "placeholder",
  SIDE_MENU = "editorSideMenu",
  SLASH_COMMANDS = "slash-command",
  STRIKETHROUGH = "strike",
  TABLE = "table",
  TABLE_CELL = "tableCell",
  TABLE_HEADER = "tableHeader",
  TABLE_ROW = "tableRow",
  TASK_ITEM = "taskItem",
  TASK_LIST = "taskList",
  TEXT_ALIGN = "textAlign",
  TEXT_STYLE = "textStyle",
  TYPOGRAPHY = "typography",
  UNDERLINE = "underline",
  UTILITY = "utility",
  WORK_ITEM_EMBED = "issue-embed-component",
  EMOJI = "emoji",
}

export enum ADDITIONAL_EXTENSIONS {}
