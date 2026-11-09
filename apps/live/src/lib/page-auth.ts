/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { logger } from "@plane/logger";
// lib
import { AppError } from "@/lib/errors";
// services
import { ISSUE_EVENTS_DOCUMENT_PREFIX } from "@/services/issue-events.service";
import { PageAuthorizationService, parsePageId } from "@/services/page-authorization.service";
// types
import type { HocusPocusServerContext } from "@/types";

/**
 * Gate the page channel on the app API.
 *
 * A page document holds the **body** of a page and is shared: once a member
 * has opened it, the document stays in memory (hocuspocus loads a document
 * once), and every later connection joins it without a check of its own. The
 * authorization therefore runs on every connection — the hocuspocus
 * ``onAuthenticate`` hook — and not only when the document is loaded.
 *
 * The page id is taken from the **document name** (the page document name is
 * the page id). The client supplied ``projectId`` / ``workspaceSlug`` query
 * parameters only scope the lookup; the app API binds page, project and
 * workspace together, so a forged pair narrows the answer but never widens
 * it.
 *
 * @throws {AppError} when the caller may not open the page document
 */
export const assertPageDocumentAccess = async ({
  context,
  documentName,
}: {
  context: HocusPocusServerContext;
  documentName?: string;
}): Promise<void> => {
  // The issue-events channel is a broadcast bus, not a page document — it
  // has its own gate (see assertIssueEventsChannelAccess).
  if (documentName?.startsWith(ISSUE_EVENTS_DOCUMENT_PREFIX)) return;

  // Every other document name must be a page id. A missing or blank name is
  // not "outside the page namespace" — it is an invalid document, and the
  // gate fails closed on it. (Hocuspocus always sends a name, but an empty
  // one would otherwise reach the content fetch as `pages//description/`.)
  const pageId = documentName ? parsePageId(documentName) : null;
  if (!pageId) {
    logger.warn("PAGE_AUTH: rejecting malformed page document name", { documentName });
    throw new AppError("Invalid page document", { code: "PAGE_INVALID_DOCUMENT" });
  }

  const service = new PageAuthorizationService();
  await service.authorize({
    workspaceSlug: context.workspaceSlug,
    projectId: context.projectId,
    pageId,
    cookie: context.cookie,
    serviceToken: context.serviceToken,
  });
};
