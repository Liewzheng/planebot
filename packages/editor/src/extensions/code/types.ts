/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

export enum ECodeBlockAttributeNames {
  ID = "id",
  LANGUAGE = "language",
  MERMAID_IMAGE_ID = "mermaidImageId",
  MERMAID_IMAGE_URL = "mermaidImageUrl",
  MERMAID_SOURCE_HASH = "mermaidSourceHash",
  MERMAID_HIDE_SOURCE = "mermaidHideSource",
}

export type TCodeBlockAttributes = {
  [ECodeBlockAttributeNames.ID]: string | null;
  [ECodeBlockAttributeNames.LANGUAGE]: string | null;
  [ECodeBlockAttributeNames.MERMAID_IMAGE_ID]: string | null;
  [ECodeBlockAttributeNames.MERMAID_IMAGE_URL]: string | null;
  [ECodeBlockAttributeNames.MERMAID_SOURCE_HASH]: string | null;
  [ECodeBlockAttributeNames.MERMAID_HIDE_SOURCE]: boolean;
};
