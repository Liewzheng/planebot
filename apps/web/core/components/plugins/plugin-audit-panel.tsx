/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// local imports
import { PLUGIN_AUDIT } from "./constants";
import { PluginAuditTimeline } from "./plugin-audit-timeline";
import { usePluginAuditPages } from "./use-plugin-audit-pages";

type Props = {
  pluginKey: string;
  workspaceSlug: string;
};

/**
 * One plugin's trail, fetched newest-first on demand from the read-only audit
 * endpoint. Older pages are requested only when the reader asks for them.
 */
export function PluginAuditPanel({ pluginKey, workspaceSlug }: Props) {
  const { events, isLoading, error, hasOlder, isLoadingOlder, olderError, loadOlder } = usePluginAuditPages({
    workspaceSlug,
    cacheKey: PLUGIN_AUDIT(workspaceSlug, pluginKey),
    query: { key: pluginKey },
  });

  return (
    <PluginAuditTimeline
      events={events}
      isLoading={isLoading}
      error={error}
      hasOlder={hasOlder}
      isLoadingOlder={isLoadingOlder}
      olderError={olderError}
      onLoadOlder={() => void loadOlder()}
    />
  );
}
