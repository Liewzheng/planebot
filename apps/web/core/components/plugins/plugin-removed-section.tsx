/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo } from "react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Badge } from "@plane/propel/badge";
import { Button } from "@plane/propel/button";
// hooks
import { useMember } from "@/hooks/store/use-member";
// local imports
import { PLUGIN_REMOVALS } from "./constants";
import { PluginAuditPanel } from "./plugin-audit-panel";
import { formatPluginTimestamp, getMemberDisplayName, getPluginActorLabel } from "./helper";
import { usePluginAuditPages } from "./use-plugin-audit-pages";

type Props = {
  workspaceSlug: string;
  /** Keys the list endpoint still returns; a removal of one of those is stale. */
  activeKeys: string[];
};

/**
 * Soft-deleted plugins are gone from the list endpoint, so the workspace audit —
 * which outlives them — is the only place they remain discoverable.
 *
 * The inventory is the server's own answer (`?action=uninstall&distinct=key`:
 * one row per key that was ever uninstalled, newest removal first) rather than a
 * client-side reconstruction from one page of the trail. The server computes it
 * with a `GROUP BY` over the whole workspace trail and pages it with the same
 * cursor as the trail, so it cannot silently stop at a fixed number of events.
 *
 * Each removed plugin's trail is rendered expanded, not hidden behind a toggle:
 * "the record survives" is the point of this section, and an affordance that
 * hides it would undercut that. The trail itself is newest-first and paged on
 * demand like every other audit panel.
 */
export function PluginRemovedSection({ workspaceSlug, activeKeys }: Props) {
  // hooks
  const { t, currentLocale } = useTranslation();
  const { getWorkspaceMemberDetails } = useMember().workspace;
  const { events, isLoading, error, hasOlder, isLoadingOlder, olderError, loadOlder } = usePluginAuditPages({
    workspaceSlug,
    cacheKey: PLUGIN_REMOVALS(workspaceSlug),
    query: { action: "uninstall", distinct: "key" },
  });

  // derived values
  const active = useMemo(() => new Set(activeKeys), [activeKeys]);
  const removals = useMemo(() => events.filter((event) => !active.has(event.plugin_key)), [events, active]);

  if (error) {
    return <p className="text-11 text-tertiary">{t("workspace_settings.settings.plugins.removed.load_failed")}</p>;
  }

  if (isLoading || removals.length === 0) return null;

  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h4 className="text-h6-medium text-primary">{t("workspace_settings.settings.plugins.removed.heading")}</h4>
        <p className="text-body-xs-regular text-tertiary">
          {t("workspace_settings.settings.plugins.removed.description")}
        </p>
      </div>

      {removals.map((removal) => {
        const member = removal.actor_id ? getWorkspaceMemberDetails(removal.actor_id)?.member : null;
        return (
          <div
            key={removal.plugin_key}
            className="flex flex-col gap-3 rounded-lg border border-subtle bg-layer-2 px-4 py-3"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-body-sm-medium">{removal.plugin_key}</span>
              <span className="text-11 text-tertiary">v{removal.plugin_version}</span>
              <Badge variant="neutral" size="base">
                {t("workspace_settings.settings.plugins.audit.state_deleted")}
              </Badge>
              <span className="text-11 text-secondary">
                {t("workspace_settings.settings.plugins.removed.uninstalled_at", {
                  date: formatPluginTimestamp(removal.at, currentLocale),
                })}
              </span>
              <span className="text-11 text-secondary">
                {getPluginActorLabel(
                  { actor_id: removal.actor_id, actor_kind: removal.actor_kind },
                  t,
                  getMemberDisplayName(member)
                )}
              </span>
            </div>
            <PluginAuditPanel pluginKey={removal.plugin_key} workspaceSlug={workspaceSlug} />
          </div>
        );
      })}

      {hasOlder && (
        <div className="flex flex-col gap-1">
          <Button
            variant="ghost"
            size="sm"
            className="self-start"
            onClick={() => void loadOlder()}
            loading={isLoadingOlder}
            disabled={isLoadingOlder}
          >
            {t("common.load_more")}
          </Button>
          {olderError ? (
            <p className="text-11 text-danger-primary">
              {t("workspace_settings.settings.plugins.removed.load_failed")}
            </p>
          ) : null}
        </div>
      )}
    </section>
  );
}
