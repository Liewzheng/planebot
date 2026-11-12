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
  TPluginAuditQuery,
  TPluginManifest,
  TPluginManifestExport,
  TPluginUninstallPayload,
} from "./types";

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

  async fetchPluginAudit(workspaceSlug: string, query: TPluginAuditQuery = {}): Promise<TPluginAuditEvent[]> {
    return this.get(this.basePath(workspaceSlug) + "audit/", { params: query })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}

export const pluginService = new PluginService();
