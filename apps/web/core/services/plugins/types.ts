/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

//: Plugin kinds the registry contract defines. `package` exists in the contract
//: but cannot be installed in v0 — there is no runtime to execute it.
export type TPluginKind = "builtin" | "package";

//: Registry states. `deleted` is deliberately absent: removal is `deleted_at`
//: on the row and only ever shows up in an audit event's `to_state`.
export type TPluginState = "installed" | "enabled" | "disabled";

export type TPluginAuditState = TPluginState | "deleted" | "";

export type TPluginAuditAction =
  | "install"
  | "enable"
  | "disable"
  | "uninstall"
  | "upgrade"
  | "import"
  | "contract_rematch";

export type TPluginActorKind = "human" | "service" | "system";

//: Manifest v0 as the kernel validates it. Everything is optional here: this
//: type describes what the UI may read out of a manifest it did not author, and
//: the server — not the client — decides whether a manifest is acceptable.
export type TPluginManifest = {
  manifest_version?: string;
  key?: string;
  name?: string;
  description?: string;
  version?: string;
  kind?: TPluginKind;
  kernel_contract?: string;
  publisher?: { name?: string };
  scopes?: string[];
  contributions?: Record<string, unknown[]>;
  config_schema?: Record<string, unknown>;
  limits?: Record<string, number>;
};

//: `PluginSerializer` output.
export type TPlugin = {
  id: string;
  key: string;
  name: string;
  description: string;
  version: string;
  kind: TPluginKind;
  state: TPluginState;
  manifest_hash: string;
  contract_hash: string;
  contract_drifted: boolean;
  installed_by: string | null;
  installed_at: string;
  created_at: string;
  updated_at: string;
};

//: `GET .../plugins/<key>/manifest/` — the stored manifest plus the hashes that
//: identify it, so an export can be checked against the kernel contract.
export type TPluginManifestExport = {
  manifest: TPluginManifest;
  manifest_hash: string;
  contract_hash: string;
  kernel_contract_hash: string;
  contract_drifted: boolean;
};

//: `GET .../plugins/audit/` row. Read-only by construction.
export type TPluginAuditEvent = {
  id: number;
  workspace_id: string;
  plugin_key: string;
  plugin_version: string;
  action: TPluginAuditAction;
  from_state: TPluginAuditState;
  to_state: TPluginAuditState;
  actor_id: string | null;
  actor_kind: TPluginActorKind;
  at: string;
  manifest_hash: string | null;
  contract_hash: string | null;
  reason: string;
  request_id: string;
};

export type TPluginAuditQuery = {
  key?: string;
  limit?: number;
  /** Exclusive cursor: only events with an id below this one are returned. */
  before_id?: number;
  action?: TPluginAuditAction;
  /** Collapse the (filtered) trail to one row per plugin key — its newest event. */
  distinct?: "key";
};

/**
 * One page of the read-only audit listing.
 *
 * The response body is still the plain list of events the endpoint has always
 * returned; the cursor comes from the `X-Audit-Next-Cursor` /
 * `X-Audit-Has-More` response headers. When a proxy strips those headers the
 * page is still walked correctly: a full page means "there may be more".
 */
export type TPluginAuditPage = {
  events: TPluginAuditEvent[];
  /** Send back as `before_id` to fetch the next (older) page; null when done. */
  nextCursor: number | null;
  hasMore: boolean;
};

export type TPluginUninstallPayload = {
  //: Must repeat the plugin key; the server refuses anything else.
  confirm: string;
  //: Mandatory, stored on the audit row.
  reason: string;
};

//: Registry refusals travel in this envelope (`{ error, code, details? }`),
//: so the UI shows the server's own sentence instead of restating a rule.
export type TPluginErrorBody = {
  error?: string;
  code?: string;
  details?: unknown;
};
