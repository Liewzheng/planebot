/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import type { IncomingHttpHeaders } from "http";
import type { TUserDetails } from "@plane/editor";
import { logger } from "@plane/logger";
import { AppError } from "@/lib/errors";
// services
import { isServiceToken, PrincipalService } from "@/services/principal.service";
import { UserService } from "@/services/user.service";
// types
import type { HocusPocusServerContext, TDocumentTypes } from "@/types";

/** Service-token prefix shared with the app API.  Mirrors
 *  :data:`plane.service_principals.constants.SERVICE_TOKEN_PREFIX`. */
const SERVICE_TOKEN_PREFIX = "plane_svc_";

/**
 * Authenticate the user
 * @param requestHeaders - The request headers
 * @param context - The context
 * @param token - The token
 * @returns The authenticated user
 *
 * The token can be one of:
 *  - a JSON-encoded ``TUserDetails`` blob (legacy human path, the
 *    web client has been using this since day one); the cookie
 *    session is forwarded on every page call.
 *  - a raw ``plane_svc_*`` service-principal token (M10 SP path);
 *    the token is verified against the app API principal-dispatch
 *    endpoint and the same token is forwarded as ``X-Api-Key`` on
 *    every page call.  Grant revocation is therefore enforced
 *    server-side on each call — the live server never keeps a
 *    local copy of the grant, and the next denied write is what
 *    makes the connection drop (see design doc §12.3).
 */
export const onAuthenticate = async ({
  requestHeaders,
  requestParameters,
  context,
  token,
}: {
  requestHeaders: IncomingHttpHeaders;
  context: HocusPocusServerContext;
  requestParameters: URLSearchParams;
  token: string;
}) => {
  context.documentType = requestParameters.get("documentType")?.toString() as TDocumentTypes;
  context.projectId = requestParameters.get("projectId");
  context.workspaceSlug = requestParameters.get("workspaceSlug");

  // Service-principal path: the token is the credential, no cookie.
  if (isServiceToken(token)) {
    const slug = context.workspaceSlug;
    if (!slug) {
      const appError = new AppError("Service token connection is missing workspaceSlug", {
        code: "AUTH_MISSING_WORKSPACE",
      });
      logger.error("Service token auth missing workspace slug", appError);
      throw appError;
    }
    return await handleServicePrincipalAuthentication({
      workspaceSlug: slug,
      token,
      context,
    });
  }

  let cookie: string | undefined = undefined;
  let userId: string | undefined = undefined;

  // Extract cookie (fallback to request headers) and userId from token (for scenarios where
  // the cookies are not passed in the request headers)
  try {
    const parsedToken = JSON.parse(token) as TUserDetails;
    userId = parsedToken.id;
    cookie = parsedToken.cookie;
  } catch (error) {
    const appError = new AppError(error, {
      context: { operation: "onAuthenticate" },
    });
    logger.error("Token parsing failed, using request headers", appError);
  } finally {
    // If cookie is still not found, fallback to request headers
    if (!cookie) {
      cookie = requestHeaders.cookie?.toString();
    }
  }

  if (!cookie || !userId) {
    const appError = new AppError("Credentials not provided", { code: "AUTH_MISSING_CREDENTIALS" });
    logger.error("Credentials not provided", appError);
    throw appError;
  }

  // set cookie in context, so it can be used throughout the ws connection
  context.cookie = cookie ?? requestParameters.get("cookie") ?? "";
  context.userId = userId;

  return await handleAuthentication({
    cookie: context.cookie,
    userId: context.userId,
  });
};

export const handleAuthentication = async ({ cookie, userId }: { cookie: string; userId: string }) => {
  // fetch current user info
  try {
    const userService = new UserService();
    const user = await userService.currentUser(cookie);
    if (user.id !== userId) {
      throw new AppError("Authentication unsuccessful: User ID mismatch", { code: "AUTH_USER_MISMATCH" });
    }

    return {
      user: {
        id: user.id,
        name: user.display_name,
      },
    };
  } catch (error) {
    const appError = new AppError(error, {
      context: { operation: "handleAuthentication" },
    });
    logger.error("Authentication failed", appError);
    throw new AppError("Authentication unsuccessful", { code: appError.code });
  }
};

/** Verify a service-principal token against the app API's principal
 *  dispatch endpoint and stash the SP identity on the connection
 *  context so the page service can forward the same token on each
 *  call.  The live server does NOT cache any local grant state — the
 *  app API runs ``authorize()`` on every page read/write, which is
 *  how a revoked grant naturally drops the next collaborative update
 *  (see design doc §12.3). */
export const handleServicePrincipalAuthentication = async ({
  workspaceSlug,
  token,
  context,
}: {
  workspaceSlug: string;
  token: string;
  context: HocusPocusServerContext;
}) => {
  try {
    const principalService = new PrincipalService();
    const identity = await principalService.resolveServiceToken(workspaceSlug, token);

    // Cache the credential on the context so the page service can
    // attach X-Api-Key to every API call.  The token itself is held
    // in the service instance for the lifetime of the WS connection;
    // the next call that fails authz (4xx) propagates up through
    // fetch/store and the connection naturally drops.
    context.serviceToken = token;
    context.servicePrincipalId = identity.id;
    context.cookie = "";

    return {
      user: {
        id: identity.id,
        name: `svc:${identity.id}`,
      },
    };
  } catch (error) {
    if (error instanceof AppError) {
      throw error;
    }
    const appError = new AppError(error, {
      context: { operation: "handleServicePrincipalAuthentication", workspaceSlug },
    });
    logger.error("Service principal authentication failed", appError);
    throw new AppError("Authentication unsuccessful", { code: appError.code });
  }
};

export { isServiceToken, SERVICE_TOKEN_PREFIX };
