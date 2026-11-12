/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import useSWR from "swr";
// services
import { pluginService } from "@/services/plugins";
// local imports
import { PLUGIN_AUDIT } from "./constants";
import { PluginAuditTimeline } from "./plugin-audit-timeline";

type Props = {
  pluginKey: string;
  workspaceSlug: string;
};

/** One plugin's trail, fetched on demand from the read-only audit endpoint. */
export function PluginAuditPanel({ pluginKey, workspaceSlug }: Props) {
  const { data, isLoading, error } = useSWR(PLUGIN_AUDIT(workspaceSlug, pluginKey), () =>
    pluginService.fetchPluginAudit(workspaceSlug, { key: pluginKey })
  );

  return <PluginAuditTimeline events={data ?? []} isLoading={isLoading} error={error} />;
}
