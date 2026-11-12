/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { IUserLite } from "@plane/types";
import { pluginService } from "@/services/plugins";
import type {
  TPlugin,
  TPluginActorKind,
  TPluginAuditAction,
  TPluginAuditEvent,
  TPluginAuditState,
  TPluginErrorBody,
  TPluginState,
} from "@/services/plugins";

/**
 * The registry answers refusals as `{ error, code, details? }` — the server's own
 * sentence. Showing it verbatim keeps one source of truth for the rules; the UI
 * never restates a server rule as its own validation.
 */
export const getPluginErrorSentence = (error: unknown): string | null => {
  if (!error || typeof error !== "object") return null;
  const { error: message, code } = error as TPluginErrorBody;
  if (typeof message === "string" && message.trim()) return message;
  if (typeof code === "string" && code.trim()) return code;
  return null;
};

export const PLUGIN_STATE_LABEL_KEY: Record<TPluginState, string> = {
  installed: "workspace_settings.settings.plugins.list.state_installed",
  enabled: "workspace_settings.settings.plugins.list.state_enabled",
  disabled: "workspace_settings.settings.plugins.list.state_disabled",
};

export const PLUGIN_AUDIT_STATE_LABEL_KEY: Record<TPluginAuditState, string> = {
  "": "workspace_settings.settings.plugins.audit.state_none",
  installed: "workspace_settings.settings.plugins.list.state_installed",
  enabled: "workspace_settings.settings.plugins.list.state_enabled",
  disabled: "workspace_settings.settings.plugins.list.state_disabled",
  deleted: "workspace_settings.settings.plugins.audit.state_deleted",
};

export const PLUGIN_AUDIT_ACTION_LABEL_KEY: Record<TPluginAuditAction, string> = {
  install: "workspace_settings.settings.plugins.audit.actions.install",
  enable: "workspace_settings.settings.plugins.audit.actions.enable",
  disable: "workspace_settings.settings.plugins.audit.actions.disable",
  uninstall: "workspace_settings.settings.plugins.audit.actions.uninstall",
  upgrade: "workspace_settings.settings.plugins.audit.actions.upgrade",
  import: "workspace_settings.settings.plugins.audit.actions.import",
  contract_rematch: "workspace_settings.settings.plugins.audit.actions.contract_rematch",
};

export const PLUGIN_ACTOR_KIND_LABEL_KEY: Record<TPluginActorKind, string> = {
  human: "workspace_settings.settings.plugins.audit.actor_human",
  service: "workspace_settings.settings.plugins.audit.actor_service",
  system: "workspace_settings.settings.plugins.audit.actor_system",
};

/** Shorten a sha256 for inline display; the full value stays available in the manifest dialog. */
export const shortenHash = (hash: string | null | undefined, length = 12): string =>
  hash ? `${hash.slice(0, length)}…` : "";

export const getMemberDisplayName = (member: IUserLite | null | undefined): string | null => {
  if (!member) return null;
  const fullName = [member.first_name, member.last_name].filter(Boolean).join(" ").trim();
  return member.display_name || fullName || member.email || null;
};

/**
 * Who performed a transition, in one place so the trail and the removed-plugin
 * list cannot label the same actor differently. A human whose membership is gone
 * keeps their raw id: the record is evidence, and an id is still evidence.
 */
export const getPluginActorLabel = (
  event: Pick<TPluginAuditEvent, "actor_id" | "actor_kind">,
  t: (key: string, params?: Record<string, unknown>) => string,
  memberName?: string | null
): string => {
  if (event.actor_kind === "system") return t(PLUGIN_ACTOR_KIND_LABEL_KEY.system);
  if (memberName) return memberName;
  if (event.actor_kind === "service") return t(PLUGIN_ACTOR_KIND_LABEL_KEY.service);
  return event.actor_id ?? t("workspace_settings.settings.plugins.audit.actor_unknown");
};

/**
 * Download the stored manifest as a file. The endpoint is a read, so an export
 * cannot change anything — it yields the same document that was validated at
 * install time, which is what makes it usable as evidence (or as a re-install).
 */
export const exportPluginManifest = async (workspaceSlug: string, plugin: Pick<TPlugin, "key" | "version">) => {
  const data = await pluginService.fetchPluginManifest(workspaceSlug, plugin.key);
  const blob = new Blob([JSON.stringify(data.manifest, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${plugin.key}-${plugin.version}.manifest.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
};
