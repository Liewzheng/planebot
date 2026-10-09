/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { action, computed, makeObservable, observable, runInAction } from "mobx";
// plane imports
import type {
  TDispatchActionPermission,
  TDispatchMemberRow,
  TDispatchPermissionsMatrix,
  TPrincipalDispatchPayload,
  TPrincipalKind,
  TPrincipalSummary,
  TStandardDispatchAction,
  TWorkspaceRoleCode,
} from "@plane/types";
// services
import { servicePrincipalService } from "@/services/ai-account.service";

const EMPTY_PERMISSIONS: TDispatchPermissionsMatrix = {} as TDispatchPermissionsMatrix;

const DEFAULT_PRINCIPAL: TPrincipalSummary = {
  kind: "anonymous",
  id: "",
  workspace_role: null,
};

export interface IPrincipalStore {
  // observables
  dispatchMap: Record<string, TPrincipalDispatchPayload | null>;
  fetchingSlugs: Set<string>;
  // computed
  getDispatch: (workspaceSlug: string) => TPrincipalDispatchPayload | null;
  getVisibleMemberRows: (workspaceSlug: string) => TDispatchMemberRow[];
  getSpAssignable: (workspaceSlug: string) => boolean;
  getPermissions: (workspaceSlug: string) => TDispatchPermissionsMatrix;
  getPrincipal: (workspaceSlug: string) => TPrincipalSummary;
  // fetch
  fetchPrincipalDispatch: (
    workspaceSlug: string,
    options?: { force?: boolean }
  ) => Promise<TPrincipalDispatchPayload | null>;
  // mutations
  resetDispatch: (workspaceSlug?: string) => void;
}

/** Holds the unified principal-dispatch payload the M9 app API
 *  endpoint hands the web client.  The previous front end recomputed
 *  the visibility predicate per-view (`is_bot` / `bot_type`), which
 *  meant the assignee picker, mention search, and member counter
 *  drifted apart.  This store makes the dispatch endpoint the only
 *  source of truth and lets every consumer (member store, webhook
 *  store, anything that asks "what can I do on work_items?")
 *  read the same answer. */
export class PrincipalStore implements IPrincipalStore {
  // observables
  dispatchMap: Record<string, TPrincipalDispatchPayload | null> = {};
  fetchingSlugs: Set<string> = new Set();

  constructor() {
    makeObservable(this, {
      // observable
      dispatchMap: observable.ref,
      fetchingSlugs: observable,
      // computed
      getDispatch: computed,
      getVisibleMemberRows: computed,
      getSpAssignable: computed,
      getPermissions: computed,
      getPrincipal: computed,
      // actions
      fetchPrincipalDispatch: action,
      resetDispatch: action,
    });
  }

  /** Raw dispatch payload, or null if the slug hasn't been fetched
   *  yet (or the most recent fetch failed).  The member store
   *  distinguishes "not loaded" from "loaded but empty" by checking
   *  the slug is in the map; a `null` value means the request
   *  errored and the consumer should fall back to the legacy
   *  member endpoint if it has to render something. */
  get getDispatch() {
    return (workspaceSlug: string): TPrincipalDispatchPayload | null => this.dispatchMap[workspaceSlug] ?? null;
  }

  /** Server-filtered visible member rows.  The server already
   *  applied `VISIBLE_MEMBER_Q` (humans only, `is_bot=False`) and
   *  the `sp_assignable` toggle.  Components and stores that need
   *  the human roster use this directly; the legacy
   *  `isVisibleMember` predicate in `workspace-member.store.ts` is
   *  gone. */
  get getVisibleMemberRows() {
    return (workspaceSlug: string): TDispatchMemberRow[] => this.dispatchMap[workspaceSlug]?.members ?? [];
  }

  /** Whether SPs are surfaced in the workspace.  Default false when
   *  the dispatch hasn't been fetched yet — matches the server-side
   *  default in `is_sp_assignable` and is the safe (closed) answer
   *  for any UI that hides the SP picker until the toggle is on. */
  get getSpAssignable() {
    return (workspaceSlug: string): boolean => this.dispatchMap[workspaceSlug]?.sp_assignable ?? false;
  }

  /** Per-(resource_type, action) effective permissions.  Returns
   *  `{}` until the dispatch has loaded; the webhook store and
   *  other consumers should treat that as "I don't know yet" and
   *  avoid rendering affordances. */
  get getPermissions() {
    return (workspaceSlug: string): TDispatchPermissionsMatrix =>
      this.dispatchMap[workspaceSlug]?.permissions ?? EMPTY_PERMISSIONS;
  }

  /** The calling principal (the row that was generated for the
   *  viewer, not the roster).  Default is the anonymous stub when
   *  the dispatch hasn't been fetched. */
  get getPrincipal() {
    return (workspaceSlug: string): TPrincipalSummary =>
      this.dispatchMap[workspaceSlug]?.principal ?? DEFAULT_PRINCIPAL;
  }

  /** Fetch (or refetch) the dispatch payload for a workspace.  One
   *  in-flight call per slug — concurrent calls return the same
   *  shared promise.  Pass `force: true` to bypass the in-flight
   *  check (e.g. after the `sp_assignable` toggle changes, the
   *  member visibility flips and we need a fresh payload). */
  fetchPrincipalDispatch = async (
    workspaceSlug: string,
    options?: { force?: boolean }
  ): Promise<TPrincipalDispatchPayload | null> => {
    if (!workspaceSlug) return null;
    if (!options?.force && this.fetchingSlugs.has(workspaceSlug)) {
      return this.dispatchMap[workspaceSlug] ?? null;
    }
    this.fetchingSlugs.add(workspaceSlug);
    try {
      const payload = await servicePrincipalService.fetchPrincipalDispatch(workspaceSlug);
      runInAction(() => {
        this.dispatchMap[workspaceSlug] = payload;
        this.fetchingSlugs.delete(workspaceSlug);
      });
      return payload;
    } catch (error) {
      runInAction(() => {
        // Record the failure so consumers can react.  Storing `null`
        // (vs. omitting the key) signals "we tried, the server
        // errored" and lets the member store fall back to its
        // legacy endpoint rather than block on a never-arriving
        // payload.
        this.dispatchMap[workspaceSlug] = null;
        this.fetchingSlugs.delete(workspaceSlug);
      });
      throw error;
    }
  };

  /** Drop the cached payload for one workspace (or all).  Called on
   *  sign-out and when the workspace switches — the next read
   *  refetches. */
  resetDispatch = (workspaceSlug?: string) => {
    runInAction(() => {
      if (workspaceSlug) {
        delete this.dispatchMap[workspaceSlug];
        this.fetchingSlugs.delete(workspaceSlug);
      } else {
        this.dispatchMap = {};
        this.fetchingSlugs.clear();
      }
    });
  };
}

// Re-export the type tuples for downstream consumers that want to
// parameterize on a specific action / role / kind without importing
// the entire types module.
export type {
  TDispatchActionPermission,
  TDispatchMemberRow,
  TDispatchPermissionsMatrix,
  TPrincipalDispatchPayload,
  TPrincipalKind,
  TPrincipalSummary,
  TStandardDispatchAction,
  TWorkspaceRoleCode,
};
