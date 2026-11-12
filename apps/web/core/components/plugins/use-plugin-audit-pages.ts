/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import useSWR from "swr";
// services
import { PLUGIN_AUDIT_PAGE_SIZE, pluginService } from "@/services/plugins";
import type { TPluginAuditEvent, TPluginAuditQuery } from "@/services/plugins";

type Params = {
  workspaceSlug: string;
  /** SWR cache key for the newest page; must identify `query`. */
  cacheKey: string;
  /** Query for every page; `limit` and `before_id` are supplied by the hook. */
  query: TPluginAuditQuery;
  pageSize?: number;
  enabled?: boolean;
};

type OlderChain = {
  /**
   * Oldest id on the first page when this chain was started. The chain is only
   * shown while the first page still ends there: if the first page is
   * revalidated after a new event, one row falls off its bottom and the rows
   * this chain holds would start *below* the gap. Dropping the chain then is
   * what keeps the rendered trail contiguous.
   */
  baseId: number;
  events: TPluginAuditEvent[];
  cursor: number | null;
  hasMore: boolean;
};

/**
 * Newest page plus the older pages a reader explicitly asked for.
 *
 * Only pages that were requested are held, so this is not a client-side pass
 * over the whole trail: the server decides what "older" means and how much of
 * it exists (`X-Audit-Has-More` + `X-Audit-Next-Cursor`), and the hook appends.
 * Cursors are exclusive, so no row can repeat between two pages.
 */
export const usePluginAuditPages = ({
  workspaceSlug,
  cacheKey,
  query,
  pageSize = PLUGIN_AUDIT_PAGE_SIZE,
  enabled = true,
}: Params) => {
  const { data, isLoading, error } = useSWR(enabled ? cacheKey : null, () =>
    pluginService.fetchPluginAudit(workspaceSlug, { ...query, limit: pageSize })
  );

  const [chain, setChain] = useState<OlderChain | null>(null);
  const [isLoadingOlder, setIsLoadingOlder] = useState(false);
  const [olderError, setOlderError] = useState<unknown>(null);

  const firstPageEvents = data?.events ?? [];
  const firstPageOldestId = firstPageEvents.length > 0 ? firstPageEvents[firstPageEvents.length - 1].id : null;
  const activeChain = chain && chain.baseId === firstPageOldestId ? chain : null;

  const nextCursor = activeChain ? activeChain.cursor : (data?.nextCursor ?? null);
  const hasMore = activeChain ? activeChain.hasMore : (data?.hasMore ?? false);

  const loadOlder = async () => {
    if (!nextCursor || firstPageOldestId === null || isLoadingOlder) return;
    const baseId = firstPageOldestId;
    setIsLoadingOlder(true);
    setOlderError(null);
    try {
      const page = await pluginService.fetchPluginAudit(workspaceSlug, {
        ...query,
        limit: pageSize,
        before_id: nextCursor,
      });
      setChain((previous) =>
        previous && previous.baseId === baseId
          ? {
              ...previous,
              events: [...previous.events, ...page.events],
              cursor: page.nextCursor,
              hasMore: page.hasMore,
            }
          : {
              baseId,
              events: page.events,
              cursor: page.nextCursor,
              hasMore: page.hasMore,
            }
      );
    } catch (loadError) {
      setOlderError(loadError);
    } finally {
      setIsLoadingOlder(false);
    }
  };

  return {
    events: [...firstPageEvents, ...(activeChain?.events ?? [])],
    isLoading,
    error,
    hasOlder: hasMore && nextCursor !== null,
    isLoadingOlder,
    olderError,
    loadOlder,
  };
};
