/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { TLogoProps } from "../common";
import type { EPageAccess } from "../enums";
import type { TPageExtended } from "./extended";

export type TPage = {
  access: EPageAccess | undefined;
  archived_at: string | null | undefined;
  color: string | undefined;
  created_at: Date | undefined;
  created_by: string | undefined;
  description_json: object | undefined;
  description_html: string | undefined;
  id: string | undefined;
  is_favorite: boolean;
  is_locked: boolean;
  label_ids: string[] | undefined;
  name: string | undefined;
  owned_by: string | undefined;
  project_ids?: string[] | undefined;
  updated_at: string | undefined;
  updated_by: string | undefined;
  workspace: string | undefined;
  logo_props: TLogoProps | undefined;
  /** YAML frontmatter parsed off a markdown upload (status/created/verified/…) */
  frontmatter?: Record<string, unknown> | null;
  deleted_at: Date | undefined;
} & TPageExtended;

// page filters
export type TPageNavigationTabs = "public" | "private" | "archived";

export type TPageFiltersSortKey = "name" | "created_at" | "updated_at" | "opened_at";

export type TPageFiltersSortBy = "asc" | "desc";

export type TPageFilterProps = {
  created_at?: string[] | null;
  created_by?: string[] | null;
  favorites?: boolean;
  labels?: string[] | null;
};

export type TPageFilters = {
  searchQuery: string;
  sortKey: TPageFiltersSortKey;
  sortBy: TPageFiltersSortBy;
  filters?: TPageFilterProps;
};

export type TPageEmbedType = "mention" | "issue";

export type TPageVersion = {
  created_at: string;
  created_by: string;
  deleted_at: string | null;
  description_binary?: string | null;
  description_html?: string | null;
  description_json?: object;
  id: string;
  last_saved_at: string;
  owned_by: string;
  page: string;
  updated_at: string;
  updated_by: string;
  workspace: string;
};

export type TDocumentPayload = {
  description_binary: string;
  description_html: string;
  description_json: object;
  /**
   * Marks a publish written by the editor's save action. The API uses this to
   * distinguish a content write from a property-only edit (rename, access,
   * archive) so the version history is not inflated by a property-only
   * transaction and the first-publisher-wins check on `base_updated_at` is
   * only armed for real body writes.
   */
  save_source?: "editor";
  /**
   * The page's `updated_at` at the moment the editor session started. The
   * API compares it against the current value and refuses the write if a
   * concurrent edit landed first; the UI surfaces the conflict and the user
   * resolves it by reloading or accepting.
   */
  base_updated_at?: string;
};

export type TWebhookConnectionQueryParams = {
  documentType: "project_page" | "team_page" | "workspace_page";
  projectId?: string;
  teamId?: string;
  workspaceSlug: string;
};
