/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { TPluginManifest } from "@/services/plugins";

export const PLUGINS_LIST = (workspaceSlug: string) => `PLUGINS_LIST_${workspaceSlug}`;

/** Workspace-wide trail — the only place a soft-deleted plugin is still discoverable. */
export const PLUGIN_AUDIT_LIST = (workspaceSlug: string) => `PLUGIN_AUDIT_LIST_${workspaceSlug}`;

export const PLUGIN_AUDIT = (workspaceSlug: string, pluginKey: string) => `PLUGIN_AUDIT_${workspaceSlug}_${pluginKey}`;

export const PLUGIN_MANIFEST = (workspaceSlug: string, pluginKey: string) =>
  `PLUGIN_MANIFEST_${workspaceSlug}_${pluginKey}`;

/** The audit endpoint caps a page at 500 rows; the removed-plugin section needs the whole page. */
export const PLUGIN_AUDIT_PAGE_LIMIT = 500;

/**
 * Offered by the install dialog so the page can be exercised end to end without
 * hand-writing a manifest. It satisfies every rule the kernel checks today
 * (schema version 0.1, a kebab-case key, semver, `kind: builtin`, a contract
 * range covering 0.1, a granted scope and a contributions object).
 */
export const EXAMPLE_PLUGIN_MANIFEST: TPluginManifest = {
  manifest_version: "0.1",
  key: "example-builtin",
  name: "Example built-in plugin",
  description: "A valid manifest used to exercise install, enable, disable and uninstall.",
  version: "1.0.0",
  kind: "builtin",
  kernel_contract: ">=0.1 <0.2",
  publisher: { name: "Plane" },
  scopes: ["pages.content:read"],
  contributions: { settings_pages: [], export_renderers: [] },
  config_schema: { type: "object", properties: {} },
};
