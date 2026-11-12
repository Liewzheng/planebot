/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo } from "react";
import useSWR from "swr";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Badge } from "@plane/propel/badge";
import { renderFormattedDate } from "@plane/utils";
// hooks
import { useMember } from "@/hooks/store/use-member";
// services
import { pluginService } from "@/services/plugins";
import type { TPluginActorKind, TPluginAuditEvent } from "@/services/plugins";
// local imports
import { PLUGIN_AUDIT_LIST, PLUGIN_AUDIT_PAGE_LIMIT } from "./constants";
import { PluginAuditTimeline } from "./plugin-audit-timeline";
import { getMemberDisplayName, getPluginActorLabel } from "./helper";

type TRemovedPlugin = {
  key: string;
  version: string;
  uninstalledAt: string;
  actorId: string | null;
  actorKind: TPluginActorKind;
  events: TPluginAuditEvent[];
};

type Props = {
  workspaceSlug: string;
  /** Keys the list endpoint still returns; everything else in the trail was removed. */
  activeKeys: string[];
};

/**
 * Soft-deleted plugins are gone from the list endpoint, so the workspace audit —
 * which outlives them — is the only place they remain discoverable. Their trail
 * is rendered expanded, not hidden behind a toggle: "the record survives" is the
 * point of this section, and an affordance that hides it would undercut that.
 */
export function PluginRemovedSection({ workspaceSlug, activeKeys }: Props) {
  // hooks
  const { t } = useTranslation();
  const { getWorkspaceMemberDetails } = useMember().workspace;
  const { data, isLoading, error } = useSWR(PLUGIN_AUDIT_LIST(workspaceSlug), () =>
    pluginService.fetchPluginAudit(workspaceSlug, { limit: PLUGIN_AUDIT_PAGE_LIMIT })
  );

  const removedPlugins = useMemo<TRemovedPlugin[]>(() => {
    if (!data) return [];
    const active = new Set(activeKeys);
    const byKey = new Map<string, TPluginAuditEvent[]>();
    for (const event of data) {
      if (active.has(event.plugin_key)) continue;
      const events = byKey.get(event.plugin_key);
      if (events) events.push(event);
      else byKey.set(event.plugin_key, [event]);
    }

    const removed: TRemovedPlugin[] = [];
    // Newest first without sorting: the trail arrives in ascending id order, so
    // walking the keys backwards puts the most recent removal at the top.
    const keys = [...byKey.keys()];
    for (let index = keys.length - 1; index >= 0; index -= 1) {
      const key = keys[index];
      const events = byKey.get(key) ?? [];
      // The last uninstall row defines the removal. A key with events but no
      // uninstall row is not a removed plugin (a truncated or foreign trail), so
      // it is left out rather than guessed at.
      let uninstall: TPluginAuditEvent | undefined;
      for (const event of events) if (event.action === "uninstall") uninstall = event;
      if (!uninstall) continue;
      removed.push({
        key,
        version: uninstall.plugin_version,
        uninstalledAt: uninstall.at,
        actorId: uninstall.actor_id,
        actorKind: uninstall.actor_kind,
        events,
      });
    }
    return removed;
  }, [data, activeKeys]);

  if (error) {
    return <p className="text-11 text-tertiary">{t("workspace_settings.settings.plugins.removed.load_failed")}</p>;
  }

  if (isLoading || removedPlugins.length === 0) return null;

  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h4 className="text-h6-medium text-primary">{t("workspace_settings.settings.plugins.removed.heading")}</h4>
        <p className="text-body-xs-regular text-tertiary">
          {t("workspace_settings.settings.plugins.removed.description")}
        </p>
      </div>

      {removedPlugins.map((removed) => {
        const member = removed.actorId ? getWorkspaceMemberDetails(removed.actorId)?.member : null;
        return (
          <div key={removed.key} className="flex flex-col gap-3 rounded-lg border border-subtle bg-layer-2 px-4 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-body-sm-medium">{removed.key}</span>
              <span className="text-11 text-tertiary">v{removed.version}</span>
              <Badge variant="neutral" size="base">
                {t("workspace_settings.settings.plugins.audit.state_deleted")}
              </Badge>
              <span className="text-11 text-secondary">
                {t("workspace_settings.settings.plugins.removed.uninstalled_at", {
                  date: renderFormattedDate(removed.uninstalledAt, "MMM dd, yyyy HH:mm"),
                })}
              </span>
              <span className="text-11 text-secondary">
                {getPluginActorLabel(
                  { actor_id: removed.actorId, actor_kind: removed.actorKind },
                  t,
                  getMemberDisplayName(member)
                )}
              </span>
            </div>
            <PluginAuditTimeline events={removed.events} />
          </div>
        );
      })}
    </section>
  );
}
