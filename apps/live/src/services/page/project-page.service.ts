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
  [key: string]: unknown;
}

export class ProjectPageService extends PageService {
  protected basePath: string;

  constructor(params: ProjectPageServiceParams) {
    super();
    const { workspaceSlug, projectId } = params;
    if (!workspaceSlug || !projectId) throw new AppError("Missing required fields.");
    // validate cookie
    if (!params.cookie) throw new AppError("Cookie is required.");
    // set cookie
    this.setHeader("Cookie", params.cookie);
    // Identify this client to the API: a page write coming from the live server
    // must not invalidate the in-memory document it just stored. The value is
    // shared with the API through the deployment env, so browsers cannot spoof
    // it and skip the invalidation.
    if (env.LIVE_INTERNAL_API_KEY) {
      this.setHeader("x-live-internal-key", env.LIVE_INTERNAL_API_KEY);
    }
    // set base path
    this.basePath = `/api/workspaces/${workspaceSlug}/projects/${projectId}`;
  }
}
