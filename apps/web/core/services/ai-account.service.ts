/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import { API_BASE_URL } from "@plane/constants";
import type {
  TPrincipalDispatchPayload,
  TProjectGrant,
  TProjectGrantInput,
  TServicePrincipal,
  TServicePrincipalCreatePayload,
  TServicePrincipalUpdatePayload,
  TServiceScope,
  TServiceScopeInput,
  TWorkspaceSPSettings,
} from "@plane/types";
import { APIService } from "@/services/api.service";

export class ServicePrincipalService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  async fetchServicePrincipalsList(workspaceSlug: string): Promise<TServicePrincipal[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-principals/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async createServicePrincipal(
    workspaceSlug: string,
    data: TServicePrincipalCreatePayload
  ): Promise<TServicePrincipal & { token: string }> {
    return this.post(`/api/workspaces/${workspaceSlug}/service-principals/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async updateServicePrincipal(
    workspaceSlug: string,
    principalId: string,
    data: TServicePrincipalUpdatePayload
  ): Promise<TServicePrincipal> {
    return this.patch(`/api/workspaces/${workspaceSlug}/service-principals/${principalId}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async deleteServicePrincipal(workspaceSlug: string, principalId: string, totpCode?: string): Promise<void> {
    return this.delete(
      `/api/workspaces/${workspaceSlug}/service-principals/${principalId}/`,
      totpCode ? { totp_code: totpCode } : undefined
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async rotateServicePrincipalToken(
    workspaceSlug: string,
    principalId: string,
    totpCode?: string
  ): Promise<TServicePrincipal & { token: string }> {
    return this.post(
      `/api/workspaces/${workspaceSlug}/service-principals/${principalId}/rotate-token/`,
      totpCode ? { totp_code: totpCode } : {}
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async fetchServicePrincipalScopes(workspaceSlug: string, principalId: string): Promise<TServiceScope[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-principals/${principalId}/scopes/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async updateServicePrincipalScopes(
    workspaceSlug: string,
    principalId: string,
    scopes: TServiceScopeInput[]
  ): Promise<TServiceScope[]> {
    return this.put(`/api/workspaces/${workspaceSlug}/service-principals/${principalId}/scopes/`, { scopes })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async fetchServicePrincipalGrants(workspaceSlug: string, principalId: string): Promise<TProjectGrant[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-principals/${principalId}/grants/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async updateServicePrincipalGrants(
    workspaceSlug: string,
    principalId: string,
    grants: TProjectGrantInput[]
  ): Promise<TProjectGrant[]> {
    return this.put(`/api/workspaces/${workspaceSlug}/service-principals/${principalId}/grants/`, { grants })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  // Read the per-workspace SP settings (sp_assignable toggle).
  // Backend endpoint /api/workspaces/<slug>/sp-settings/ is not yet wired
  // by M6; the GET returns 404 today and the UI treats 404 as the
  // default state (sp_assignable=false).
  async fetchSPSettings(workspaceSlug: string): Promise<TWorkspaceSPSettings> {
    return this.get(`/api/workspaces/${workspaceSlug}/sp-settings/`)
      .then((response) => response?.data)
      .catch((error) => {
        // Rethrow with the HTTP status attached so the UI's
        // isHttpNotFound check can detect "endpoint not wired yet".
        // Default to {} when the response body is missing (network error)
        // to keep the spread safe.
        throw {
          status_code: error?.response?.status,
          ...error?.response?.data,
        };
      });
  }

  // Update the per-workspace SP settings. Throws if the backend
  // has not been wired yet (e.g. 404); callers should treat that
  // as a UI affordance that is one backend PR away from working.
  async updateSPSettings(workspaceSlug: string, data: Partial<TWorkspaceSPSettings>): Promise<TWorkspaceSPSettings> {
    return this.patch(`/api/workspaces/${workspaceSlug}/sp-settings/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        // See fetchSPSettings — propagate status_code so the UI's
        // rollback path can distinguish "endpoint missing" (no real
        // failure) from a genuine validation error.
        throw {
          status_code: error?.response?.status,
          ...error?.response?.data,
        };
      });
  }

  /** M10 — fetch the unified principal-dispatch payload.
   *  Backed by `GET /api/workspaces/<slug>/principal/permissions/`,
   *  this is the single source of truth the web consumes for member
   *  visibility (the legacy `is_bot` / `bot_type` predicate was
   *  removed from `workspace-member.store.ts` in this branch) and
   *  effective permissions.  See
   *  `apps/api/plane/app/views/principal/base.py`. */
  async fetchPrincipalDispatch(workspaceSlug: string): Promise<TPrincipalDispatchPayload> {
    return this.get(`/api/workspaces/${workspaceSlug}/principal/permissions/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data ?? error;
      });
  }
}

export const servicePrincipalService = new ServicePrincipalService();
