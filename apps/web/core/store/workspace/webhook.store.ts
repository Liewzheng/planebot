/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// mobx
import { action, observable, makeObservable, computed, runInAction } from "mobx";
import { computedFn } from "mobx-utils";
// types
import type { IWebhook, TDispatchActionPermission, TStandardDispatchAction, TWorkspaceRoleCode } from "@plane/types";
// services
import { WebhookService } from "@/services/webhook.service";
// store
import type { CoreRootStore } from "../root.store";

/** M10 — the dispatch endpoint doesn't (yet) register a
 *  ``webhook`` resource type in
 *  ``plane.app.views.principal.base._DISPATCH_RESOURCE_TYPES``.
 *  Until it does, the only webhook permission we can read off
 *  the dispatch payload is the workspace-level ``member`` /
 *  ``user`` row the endpoint does emit; the webhook CRUD
 *  permission comes from the SP scope + grant chain (only
 *  visible to the SP caller) and from the role matrix (humans).
 *  The store exposes an effective-permission getter that
 *  components consult, returning ``allowed=false`` until the
 *  dispatch endpoint is updated to register ``webhook``. */
const EMPTY_WEBHOOK_PERMISSIONS: Record<TStandardDispatchAction, TDispatchActionPermission> = {
  read: { allowed: false, effective_role: null, reason: "unregistered" },
  list: { allowed: false, effective_role: null, reason: "unregistered" },
  create: { allowed: false, effective_role: null, reason: "unregistered" },
  update: { allowed: false, effective_role: null, reason: "unregistered" },
  delete: { allowed: false, effective_role: null, reason: "unregistered" },
};

export interface IWebhookStore {
  // observables
  webhooks: Record<string, IWebhook> | null;
  webhookSecretKey: string | null;
  // computed
  currentWebhook: IWebhook | null;
  // computed actions
  getWebhookById: (webhookId: string) => IWebhook | null;
  // M10 — effective permissions for webhook CRUD in the current
  // workspace, sourced from the principal-dispatch endpoint.
  // Returns the empty permission set until the dispatch endpoint
  // registers the ``webhook`` resource type (see the comment at
  // the top of this file).
  getEffectivePermissions: (workspaceSlug: string) => Record<TStandardDispatchAction, TDispatchActionPermission>;
  getEffectiveRole: (workspaceSlug: string) => TWorkspaceRoleCode | null;
  // fetch actions
  fetchWebhooks: (workspaceSlug: string) => Promise<IWebhook[]>;
  fetchWebhookById: (workspaceSlug: string, webhookId: string) => Promise<IWebhook>;
  // crud actions
  createWebhook: (
    workspaceSlug: string,
    data: Partial<IWebhook>
  ) => Promise<{ webHook: IWebhook; secretKey: string | null }>;
  updateWebhook: (workspaceSlug: string, webhookId: string, data: Partial<IWebhook>) => Promise<IWebhook>;
  removeWebhook: (workspaceSlug: string, webhookId: string) => Promise<void>;
  // secret key actions
  regenerateSecretKey: (
    workspaceSlug: string,
    webhookId: string
  ) => Promise<{ webHook: IWebhook; secretKey: string | null }>;
  clearSecretKey: () => void;
}

export class WebhookStore implements IWebhookStore {
  // observables
  webhooks: Record<string, IWebhook> | null = null;
  webhookSecretKey: string | null = null;
  // services
  webhookService;
  // root store
  rootStore;

  constructor(_rootStore: CoreRootStore) {
    makeObservable(this, {
      // observables
      webhooks: observable,
      webhookSecretKey: observable.ref,
      // computed
      currentWebhook: computed,
      // fetch actions
      fetchWebhooks: action,
      fetchWebhookById: action,
      // CRUD actions
      createWebhook: action,
      updateWebhook: action,
      removeWebhook: action,
      // secret key actions
      regenerateSecretKey: action,
      clearSecretKey: action,
    });

    // services
    this.webhookService = new WebhookService();
    // root store
    this.rootStore = _rootStore;
  }

  /**
   * computed value of current webhook based on webhook id saved in the query store
   */
  get currentWebhook() {
    const webhookId = this.rootStore.router.webhookId;
    if (!webhookId) return null;
    const currentWebhook = this.webhooks?.[webhookId] ?? null;
    return currentWebhook;
  }

  /**
   * get webhook info from the object of webhooks in the store using webhook id
   * @param webhookId
   */
  getWebhookById = computedFn((webhookId: string) => this.webhooks?.[webhookId] || null);

  /** M10 — effective webhook permissions for the calling
   *  principal.  Reads the dispatch endpoint's permissions
   *  matrix via the principal store.  The dispatch endpoint
   *  doesn't yet register ``webhook`` as a resource type, so
   *  until that lands the getter returns the empty permission
   *  set (components should treat that as "unknown" rather than
   *  "deny").  The fallback uses the workspace role on the
   *  principal block as the `effective_role` so the UI can at
   *  least render the caller's tier. */
  getEffectivePermissions = computedFn(
    (workspaceSlug: string): Record<TStandardDispatchAction, TDispatchActionPermission> => {
      const dispatch = this.rootStore.memberRoot.principalStore.getPermissions(workspaceSlug);
      const webhookRow = (
        dispatch as Record<string, Record<TStandardDispatchAction, TDispatchActionPermission>> | undefined
      )?.webhook;
      if (webhookRow) {
        // The dispatch has registered a webhook row — forward
        // it verbatim.  Components can read `read.allowed`,
        // `create.allowed`, etc.
        return webhookRow;
      }
      // Dispatch endpoint doesn't yet emit `webhook`.  Build a
      // best-effort permission set from the principal's
      // workspace role so the UI doesn't render a permanently
      // disabled state for admins.  Webhook CRUD currently
      // requires ADMIN on the legacy role-based path, but we
      // don't want to bake the matrix into the store — the
      // dispatch endpoint is the source of truth.
      const principal = this.rootStore.memberRoot.principalStore.getPrincipal(workspaceSlug);
      if (principal.kind === "service") {
        // SP path: only the SP can read its own permissions,
        // and the engine will produce the right answer on the
        // next call.  Until then we report deny so the UI
        // doesn't render stale affordances.
        return EMPTY_WEBHOOK_PERMISSIONS;
      }
      const role = principal.workspace_role;
      if (role === null || role === undefined) {
        return EMPTY_WEBHOOK_PERMISSIONS;
      }
      const ADMIN: TWorkspaceRoleCode = 20;
      const allowed = role >= ADMIN;
      return {
        read: { allowed, effective_role: role, reason: allowed ? "allowed" : "role_cap" },
        list: { allowed, effective_role: role, reason: allowed ? "allowed" : "role_cap" },
        create: { allowed, effective_role: role, reason: allowed ? "allowed" : "role_cap" },
        update: { allowed, effective_role: role, reason: allowed ? "allowed" : "role_cap" },
        delete: { allowed, effective_role: role, reason: allowed ? "allowed" : "role_cap" },
      };
    }
  );

  /** M10 — workspace role resolved by the dispatch endpoint for
   *  the calling principal.  Convenience getter so the webhook
   *  UI can render the role badge without re-deriving from the
   *  matrix. */
  getEffectiveRole = computedFn((workspaceSlug: string): TWorkspaceRoleCode | null => {
    return this.rootStore.memberRoot.principalStore.getPrincipal(workspaceSlug).workspace_role;
  });

  /**
   * fetch all the webhooks for a workspace
   * @param workspaceSlug
   */
  fetchWebhooks = async (workspaceSlug: string) =>
    await this.webhookService.fetchWebhooksList(workspaceSlug).then((response) => {
      const webHookObject: { [webhookId: string]: IWebhook } = response.reduce((accumulator, currentWebhook) => {
        if (currentWebhook && currentWebhook.id) {
          return { ...accumulator, [currentWebhook.id]: currentWebhook };
        }
        return accumulator;
      }, {});
      runInAction(() => {
        this.webhooks = webHookObject;
      });
      return response;
    });

  /**
   * fetch webhook info from API using webhook id
   * @param workspaceSlug
   * @param webhookId
   */
  fetchWebhookById = async (workspaceSlug: string, webhookId: string) =>
    await this.webhookService.fetchWebhookDetails(workspaceSlug, webhookId).then((response) => {
      runInAction(() => {
        this.webhooks = {
          ...this.webhooks,
          [response.id]: response,
        };
      });
      return response;
    });

  /**
   * create a new webhook for a workspace using the data
   * @param workspaceSlug
   * @param data
   */
  createWebhook = async (workspaceSlug: string, data: Partial<IWebhook>) =>
    await this.webhookService.createWebhook(workspaceSlug, data).then((response) => {
      const _secretKey = response?.secret_key ?? null;
      delete response?.secret_key;
      const _webhooks = this.webhooks;
      if (response && response.id && _webhooks) _webhooks[response.id] = response;
      runInAction(() => {
        this.webhookSecretKey = _secretKey || null;
        this.webhooks = _webhooks;
      });
      return { webHook: response, secretKey: _secretKey };
    });

  /**
   * update a webhook using the data
   * @param workspaceSlug
   * @param webhookId
   * @param data
   */
  updateWebhook = async (workspaceSlug: string, webhookId: string, data: Partial<IWebhook>) =>
    await this.webhookService.updateWebhook(workspaceSlug, webhookId, data).then((response) => {
      let _webhooks = this.webhooks;
      if (webhookId && _webhooks && this.webhooks)
        _webhooks = { ..._webhooks, [webhookId]: { ...this.webhooks[webhookId], ...data } };
      runInAction(() => {
        this.webhooks = _webhooks;
      });
      return response;
    });

  /**
   * delete a webhook using webhook id
   * @param workspaceSlug
   * @param webhookId
   */
  removeWebhook = async (workspaceSlug: string, webhookId: string) =>
    await this.webhookService.deleteWebhook(workspaceSlug, webhookId).then(() => {
      const _webhooks = this.webhooks ?? {};
      delete _webhooks[webhookId];
      runInAction(() => {
        this.webhooks = _webhooks;
      });
    });

  /**
   * regenerate secret key for a webhook using webhook id
   * @param workspaceSlug
   * @param webhookId
   */
  regenerateSecretKey = async (workspaceSlug: string, webhookId: string) =>
    await this.webhookService.regenerateSecretKey(workspaceSlug, webhookId).then((response) => {
      const _secretKey = response?.secret_key ?? null;
      delete response?.secret_key;
      const _webhooks = this.webhooks;
      if (_webhooks && response && response.id) {
        _webhooks[response.id] = response;
      }
      runInAction(() => {
        this.webhookSecretKey = _secretKey || null;
        this.webhooks = _webhooks;
      });
      return { webHook: response, secretKey: _secretKey };
    });

  /**
   * clear secret key from the store
   */
  clearSecretKey = () => {
    this.webhookSecretKey = null;
  };
}
