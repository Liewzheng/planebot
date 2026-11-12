/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import { API_BASE_URL } from "@plane/constants";
// services
import { APIService } from "@/services/api.service";
// local imports
import type {
  TPlugin,
  TPluginAuditEvent,
  TPluginAuditPage,
  TPluginAuditQuery,
  TPluginManifest,
  TPluginManifestExport,
  TPluginUninstallPayload,
} from "./types";

/** Server-side cap on one audit page (`AUDIT_MAX_LIMIT` in the API). */
export const PLUGIN_AUDIT_MAX_PAGE_SIZE = 500;

/** How many audit rows one page asks for; the next page follows the cursor. */
export const PLUGIN_AUDIT_PAGE_SIZE = 50;

const AUDIT_NEXT_CURSOR_HEADER = "x-audit-next-cursor";
const AUDIT_HAS_MORE_HEADER = "x-audit-has-more";

export class PluginService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  private basePath(workspaceSlug: string): string {
    return `/api/workspaces/${workspaceSlug}/plugins/`;
  }

  async fetchPluginsList(workspaceSlug: string): Promise<TPlugin[]> {
    return this.get(this.basePath(workspaceSlug))
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async installPlugin(workspaceSlug: string, manifest: TPluginManifest): Promise<TPlugin> {
    return this.post(this.basePath(workspaceSlug), { manifest })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * Import a manifest that came from outside this workspace. Same validation as
   * install; the server records it under the `import` audit action instead of
   * `install`, which is the only difference this UI has to preserve.
   */
  async importPlugin(workspaceSlug: string, manifest: TPluginManifest): Promise<TPlugin> {
    return this.post(`${this.basePath(workspaceSlug)}import/`, { manifest })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async enablePlugin(workspaceSlug: string, key: string): Promise<TPlugin> {
    return this.post(`${this.basePath(workspaceSlug)}${key}/enable/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async disablePlugin(workspaceSlug: string, key: string): Promise<TPlugin> {
    return this.post(`${this.basePath(workspaceSlug)}${key}/disable/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async uninstallPlugin(workspaceSlug: string, key: string, data: TPluginUninstallPayload): Promise<TPlugin> {
    return this.post(`${this.basePath(workspaceSlug)}${key}/uninstall/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async fetchPluginManifest(workspaceSlug: string, key: string): Promise<TPluginManifestExport> {
    return this.get(`${this.basePath(workspaceSlug)}${key}/manifest/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * One page of the read-only audit listing, newest first.
   *
   * The body is a plain array; the server puts the continuation in response
   * headers. Custom headers are invisible to browser code across origins
   * unless the API exposes them, so the cursor is also derived from the page
   * itself when the headers are missing: rows are newest-first, so the last
   * row's id is the exclusive cursor, and a full page means "maybe more".
   * The fallback can cost one extra empty request; it can never skip rows.
   */
  async fetchPluginAudit(workspaceSlug: string, query: TPluginAuditQuery = {}): Promise<TPluginAuditPage> {
    const requestedLimit = query.limit ?? PLUGIN_AUDIT_PAGE_SIZE;
    return this.get(this.basePath(workspaceSlug) + "audit/", { params: query })
      .then((response) => {
        const events: TPluginAuditEvent[] = Array.isArray(response?.data) ? response.data : [];
        const headers = response?.headers ?? {};
        const rawHasMore = headers[AUDIT_HAS_MORE_HEADER];
        const rawCursor = headers[AUDIT_NEXT_CURSOR_HEADER];

        // A requested limit above the server cap is clamped, so compare against
        // the cap as well: otherwise a clamped full page would look exhausted.
        const pageSize = Math.min(requestedLimit, PLUGIN_AUDIT_MAX_PAGE_SIZE);
        const hasMore = rawHasMore !== undefined ? rawHasMore === "true" : events.length >= pageSize;
        const lastId = events.length > 0 ? events[events.length - 1].id : null;
        const nextCursor = !hasMore ? null : rawCursor !== undefined && rawCursor !== "" ? Number(rawCursor) : lastId;
        return { events, nextCursor, hasMore };
      })
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}

export const pluginService = new PluginService();
