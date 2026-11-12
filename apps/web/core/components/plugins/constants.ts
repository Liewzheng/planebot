/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { TPluginManifest } from "@/services/plugins";

export const PLUGINS_LIST = (workspaceSlug: string) => `PLUGINS_LIST_${workspaceSlug}`;

/**
 * Workspace-wide removal inventory: one row per key that was uninstalled, from
 * the server's `?action=uninstall&distinct=key` answer. Soft-deleted plugins are
 * gone from the list endpoint, so this is the only place they are discoverable.
 */
export const PLUGIN_REMOVALS = (workspaceSlug: string) => `PLUGIN_REMOVALS_${workspaceSlug}`;

export const PLUGIN_AUDIT = (workspaceSlug: string, pluginKey: string) => `PLUGIN_AUDIT_${workspaceSlug}_${pluginKey}`;

export const PLUGIN_MANIFEST = (workspaceSlug: string, pluginKey: string) =>
  `PLUGIN_MANIFEST_${workspaceSlug}_${pluginKey}`;

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
