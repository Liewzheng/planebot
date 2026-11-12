/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import { useTranslation } from "@plane/i18n";
import { Badge } from "@plane/propel/badge";
import { Button } from "@plane/propel/button";
import { renderFormattedDate } from "@plane/utils";
import { Lock } from "lucide-react";
// hooks
import { useMember } from "@/hooks/store/use-member";
// local imports
import type { TPluginAuditEvent } from "@/services/plugins";
import {
  PLUGIN_AUDIT_ACTION_LABEL_KEY,
  PLUGIN_AUDIT_STATE_LABEL_KEY,
  getMemberDisplayName,
  getPluginActorLabel,
  shortenHash,
} from "./helper";

type Props = {
  events: TPluginAuditEvent[];
  isLoading?: boolean;
  error?: unknown;
  /** True when the server says older entries remain behind a cursor. */
  hasOlder?: boolean;
  isLoadingOlder?: boolean;
  olderError?: unknown;
  onLoadOlder?: () => void;
};

/**
 * The audit trail of one plugin, newest first, exactly as the server returns it.
 *
 * Deliberately immutable in the UI as well as on the server: there is no input,
 * no toggle, no menu and no destructive control anywhere in this tree, and the
 * heading states that entries cannot be edited — a reader must not be left
 * guessing whether this is a record or a control surface.
 *
 * Older entries are paged in on demand: the newest page is what loads, and the
 * button at the bottom follows the server's cursor, so a long trail is never
 * silently cut at a fixed number of rows.
 */
export function PluginAuditTimeline({
  events,
  isLoading = false,
  error = null,
  hasOlder = false,
  isLoadingOlder = false,
  olderError = null,
  onLoadOlder,
}: Props) {
  const { t } = useTranslation();
  const { getWorkspaceMemberDetails } = useMember().workspace;

  const getActorLabel = (event: TPluginAuditEvent) => {
    const member = event.actor_id ? getWorkspaceMemberDetails(event.actor_id)?.member : null;
    return getPluginActorLabel(event, t, getMemberDisplayName(member));
  };

  return (
    <div className="flex flex-col gap-3 rounded-md border border-subtle bg-surface-2 px-4 py-3">
      <div className="flex items-center justify-between gap-2">
        <h6 className="text-body-xs-medium text-secondary">{t("workspace_settings.settings.plugins.audit.heading")}</h6>
        <span className="flex items-center gap-1 text-11 text-tertiary">
          <Lock className="size-3" aria-hidden="true" />
          {t("workspace_settings.settings.plugins.audit.read_only")}
        </span>
      </div>

      {error ? (
        <p className="text-11 text-danger-primary">{t("workspace_settings.settings.plugins.audit.load_failed")}</p>
      ) : isLoading ? (
        <p className="text-11 text-tertiary">{t("workspace_settings.settings.plugins.audit.loading")}</p>
      ) : events.length === 0 ? (
        <p className="text-11 text-tertiary">{t("workspace_settings.settings.plugins.audit.empty")}</p>
      ) : (
        <ol className="flex flex-col gap-3 border-l border-subtle pl-4">
          {events.map((event) => (
            <li key={event.id} className="relative flex flex-col gap-0.5">
              <span
                className="bg-subtle-1 absolute top-1.5 -left-[1.3125rem] size-1.5 rounded-full"
                aria-hidden="true"
              />
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-13 font-medium text-primary">
                  {t(PLUGIN_AUDIT_ACTION_LABEL_KEY[event.action])}
                </span>
                <Badge variant="neutral" size="sm">
                  {`${t(PLUGIN_AUDIT_STATE_LABEL_KEY[event.from_state])} → ${t(
                    PLUGIN_AUDIT_STATE_LABEL_KEY[event.to_state]
                  )}`}
                </Badge>
                <span className="text-11 text-tertiary">{renderFormattedDate(event.at, "MMM dd, yyyy HH:mm")}</span>
                <span className="text-11 text-secondary">{getActorLabel(event)}</span>
              </div>
              {event.reason && (
                <p className="text-11 text-secondary">
                  {t("workspace_settings.settings.plugins.audit.reason", { reason: event.reason })}
                </p>
              )}
              {(event.request_id || event.manifest_hash) && (
                <p className="font-mono text-10 text-placeholder">
                  {[
                    event.request_id
                      ? t("workspace_settings.settings.plugins.audit.request", { id: event.request_id })
                      : null,
                    event.manifest_hash
                      ? t("workspace_settings.settings.plugins.audit.manifest", {
                          hash: shortenHash(event.manifest_hash),
                        })
                      : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              )}
            </li>
          ))}
        </ol>
      )}

      {hasOlder && !isLoading && (
        <div className="flex flex-col gap-1">
          <Button
            variant="ghost"
            size="sm"
            className="self-start"
            onClick={onLoadOlder}
            loading={isLoadingOlder}
            disabled={isLoadingOlder}
          >
            {t("common.load_more")}
          </Button>
          {olderError ? (
            <p className="text-11 text-danger-primary">{t("workspace_settings.settings.plugins.audit.load_failed")}</p>
          ) : null}
        </div>
      )}
    </div>
  );
}
