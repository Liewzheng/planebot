/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { logger } from "@plane/logger";
import type { IUser } from "@plane/types";
// services
import { AppError } from "@/lib/errors";
import { APIService } from "./api.service";

/** Wire shape returned by the app API principal-dispatch endpoint
 *  (``GET /api/workspaces/<slug>/principal/permissions/``).
 *  Mirrors :class:`plane.app.views.principal.base.PrincipalDispatchEndpoint`. */
export interface TPrincipalDispatchPrincipal {
  kind: "user" | "service" | "anonymous";
  id: string;
  workspace_role: 20 | 15 | 5 | null;
}

export interface TPrincipalDispatchPayload {
  principal: TPrincipalDispatchPrincipal;
  members: unknown[];
  sp_assignable: boolean;
  permissions: Record<string, Record<string, unknown>>;
}

/** Service-principal identity resolved from a ``plane_svc_`` token.
 *  Mirrors the dispatch endpoint's ``principal.kind === "service"`` block. */
export interface TServicePrincipalIdentity {
  kind: "service";
  id: string;
  workspace_role: null;
}

/** Resolved principal — either a service principal (token-authenticated)
 *  or a human user (cookie-authenticated).  The fields the live server
 *  needs to render the connection's display name and forward authz
 *  decisions to the app API on every page call. */
export type TResolvedPrincipal =
  | { kind: "user"; id: string; name: string }
  | { kind: "service"; id: string; name: string };

/** SP service tokens are visually and lexically distinct from user
 *  tokens (see :data:`plane.service_principals.constants.SERVICE_TOKEN_PREFIX`).
 *  Detecting the prefix at the credential layer is the cheapest way to
 *  route the connection through the SP path before principal resolution. */
const SERVICE_TOKEN_PREFIX = "plane_svc_";

export const isServiceToken = (token: string | null | undefined): boolean => {
  return typeof token === "string" && token.startsWith(SERVICE_TOKEN_PREFIX);
};

/** Thin wrapper that hits the app API's principal-dispatch endpoint
 *  with the SP service token as the credential.  The endpoint is
 *  ``allow_service_principal = True`` on the server side and the
 *  ``APIKeyAuthentication`` middleware accepts ``X-Api-Key`` for
 *  ``principal_type=SERVICE`` tokens. */
export class PrincipalService extends APIService {
  /** Verify a service token against the dispatch endpoint and return
   *  the SP identity.  Throws ``AppError`` on any non-2xx response —
   *  the live server lets the connection close naturally instead of
   *  exposing a soft "session-expired" affordance, which is the
   *  grant-revocation semantics the design doc calls for. */
  async resolveServiceToken(workspaceSlug: string, token: string): Promise<TServicePrincipalIdentity> {
    try {
      const response = await this.get(`/api/workspaces/${encodeURIComponent(workspaceSlug)}/principal/permissions/`, {
        headers: {
          "X-Api-Key": token,
        },
      });
      const payload = response?.data as TPrincipalDispatchPayload | undefined;
      if (!payload || payload.principal?.kind !== "service" || !payload.principal.id) {
        throw new AppError("Service principal identity not found in dispatch response", {
          code: "AUTH_SP_RESOLVE_FAILED",
        });
      }
      return {
        kind: "service",
        id: payload.principal.id,
        workspace_role: null,
      };
    } catch (error) {
      if (error instanceof AppError) {
        throw error;
      }
      const appError = new AppError(error, {
        context: { operation: "resolveServiceToken", workspaceSlug },
      });
      logger.error("Failed to resolve service principal token", appError);
      throw new AppError("Authentication unsuccessful", { code: appError.code });
    }
  }
}

export interface TLegacyUserVerification {
  user: IUser;
  cookie: string;
}
