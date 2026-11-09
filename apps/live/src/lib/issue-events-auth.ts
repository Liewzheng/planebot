/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { logger } from "@plane/logger";
// lib
import { AppError } from "@/lib/errors";
// services
import {
  ISSUE_EVENTS_DOCUMENT_PREFIX,
  IssueEventsAuthorizationService,
  parseIssueEventsProjectId,
} from "@/services/issue-events.service";
// types
import type { HocusPocusServerContext } from "@/types";

/**
 * Gate the ephemeral issue-events channel on the app API.
 *
 * The channel is a broadcast bus: every connection on it receives the
 * project's issue events, so the subscription itself has to be authorized.
 * The project id is taken from the **document name** — the ``projectId``
 * query parameter is client supplied and is never used here.
 *
 * The check runs on every connection (the hocuspocus ``onAuthenticate``
 * hook), because the document itself is shared: once a member has opened
 * ``issue-events:<project_id>`` the document stays in memory, and a second
 * connection would otherwise join it without any check of its own.
 *
 * @throws {AppError} when the caller may not subscribe to the channel
 */
export const assertIssueEventsChannelAccess = async ({
  context,
  documentName,
}: {
  context: HocusPocusServerContext;
  documentName?: string;
}): Promise<void> => {
  if (!documentName?.startsWith(ISSUE_EVENTS_DOCUMENT_PREFIX)) return;

  const projectId = parseIssueEventsProjectId(documentName);
  if (!projectId) {
    logger.warn("ISSUE_EVENTS_AUTH: rejecting malformed issue events document name", { documentName });
    throw new AppError("Invalid issue events document", { code: "ISSUE_EVENTS_INVALID_DOCUMENT" });
  }

  const service = new IssueEventsAuthorizationService();
  await service.authorize({
    workspaceSlug: context.workspaceSlug,
    projectId,
    cookie: context.cookie,
    serviceToken: context.serviceToken,
  });
};
