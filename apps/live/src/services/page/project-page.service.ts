/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { env } from "@/env";
import { AppError } from "@/lib/errors";
import { PageService } from "./extended.service";

interface ProjectPageServiceParams {
  workspaceSlug: string | null;
  projectId: string | null;
  cookie: string | null;
  // M10 — service-principal connection.  When set, the SP token is
  // forwarded as X-Api-Key on every page-service call so the app API
  // routes the request through `authorize()`.  When null, the legacy
  // cookie path is used.
  serviceToken?: string | null;
  servicePrincipalId?: string | null;
  [key: string]: unknown;
}

export class ProjectPageService extends PageService {
  protected basePath: string;

  constructor(params: ProjectPageServiceParams) {
    super();
    const { workspaceSlug, projectId } = params;
    if (!workspaceSlug || !projectId) throw new AppError("Missing required fields.");
    // Identify this client to the API: a page write coming from the live server
    // must not invalidate the in-memory document it just stored. The value is
    // shared with the API through the deployment env, so browsers cannot spoof
    // it and skip the invalidation.
    if (env.LIVE_INTERNAL_API_KEY) {
      this.setHeader("x-live-internal-key", env.LIVE_INTERNAL_API_KEY);
    }
    // SP path: forward the service token as X-Api-Key.  The app API's
    // APIKeyAuthentication reads this header and routes the request
    // through `authorize()` against the linked ServicePrincipal.  When
    // a grant is revoked, the next page read/write returns 4xx and the
    // Database extension surfaces it through broadcastError / the
    // balloon guard — the live server never proactively disconnects.
    if (params.serviceToken) {
      this.setHeader("X-Api-Key", params.serviceToken);
    } else if (params.cookie) {
      // Legacy human path.  The SP path intentionally does NOT set
      // a Cookie header — the two credentials are mutually exclusive
      // on the app API's principal resolution.
      this.setHeader("Cookie", params.cookie);
    } else {
      throw new AppError("Either cookie or service token is required.");
    }
    // set base path
    this.basePath = `/api/workspaces/${workspaceSlug}/projects/${projectId}`;
  }
}
