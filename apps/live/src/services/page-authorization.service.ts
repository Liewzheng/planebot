/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { logger } from "@plane/logger";
// lib
import { AppError } from "@/lib/errors";
// services
import { APIService } from "./api.service";

/** A page id is a UUID; anything else is not a document we can authorize. */
const PAGE_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Extract the page id a collaborative document name refers to.
 *
 * The document name of a page **is** the page id: the web client opens the
 * collaborative document of a page under the page's own id
 * (``packages/editor/src/hooks/use-yjs-setup.ts`` builds the
 * ``HocuspocusProvider`` with ``name: docId``, and ``docId`` is the page id
 * — ``apps/web/core/components/pages/editor/editor-body.tsx`` passes
 * ``id={pageId}``).
 *
 * The id is therefore taken from the document name only. The
 * ``projectId`` / ``workspaceSlug`` query parameters are client supplied and
 * only ever scope the lookup — the app API binds page, project and
 * workspace together, so a forged pair can narrow the answer but never
 * widen it.
 *
 * @param documentName - The hocuspocus document name
 * @returns The page id, or ``null`` when the name is not a well formed page
 *          document
 */
export const parsePageId = (documentName: string): string | null => {
  if (!PAGE_ID_PATTERN.test(documentName)) return null;
  return documentName.toLowerCase();
};

export type TPageAuthorizationParams = {
  /** workspace slug of the request (client supplied, but only ever used to scope the lookup) */
  workspaceSlug: string | null;
  /** project id of the request (client supplied, but only ever used to scope the lookup) */
  projectId: string | null;
  /** page id parsed from the document name */
  pageId: string;
  /** session cookie of the human caller */
  cookie?: string | null;
  /** service principal token of a bot caller */
  serviceToken?: string | null;
};

/**
 * Decides whether a connection may open the collaborative document of a page.
 *
 * The decision is delegated to the app API — the live server keeps no
 * grant/permission state of its own:
 *
 * **Humans** — the app API's own rule for "may this user read this page",
 * i.e. the page endpoint of the app layer:
 *
 * 1. an active ``ProjectMember`` row for (user, project, workspace). The
 *    permission class resolves the role from that row and denies when there
 *    is none (``plane.app.permissions.page.ProjectPagePermission`` —
 *    ``_check_access_and_get_role``), so a workspace admin without a project
 *    membership is denied here, unlike the issue endpoints.
 * 2. the page must live in that (workspace, project): the queryset
 *    annotates the ``ProjectPage`` link of the project and filters on it
 *    (``plane.app.views.page.base.PageViewSet.get_queryset``), so a page of
 *    another project answers 404.
 * 3. a private page (``access=1``) is readable by its owner only; a public
 *    page (``access=0``) by every project member.
 * 4. a guest (role 5) may only read the pages they own unless the project
 *    sets ``guest_view_all_features`` (``PageViewSet.retrieve`` answers 400
 *    otherwise).
 *
 * The public-project network flag does not widen any of this: the page
 * endpoints ask for a project membership row, they never look at
 * ``Project.network``.
 *
 * **Service principals** — refused explicitly. The app layer's page views
 * declare no ``authz_resource_type``, so every SP request to them is
 * denied (``plane.app.permissions.page._sp_has_permission`` returns False
 * for a view without the attribute), and the v1 page endpoint does not
 * serve the collaborative binary the live server needs. See
 * ``PAGE_SERVICE_PRINCIPAL_UNSUPPORTED`` below.
 */
export class PageAuthorizationService extends APIService {
  /**
   * @throws {AppError} when the caller may not open the page document
   */
  async authorize({ workspaceSlug, projectId, pageId, cookie, serviceToken }: TPageAuthorizationParams): Promise<void> {
    // The service-principal path cannot work today, and a connection that
    // authenticates but never loads its document is a silent dead end — so
    // the refusal is explicit and readable instead of a failure buried in
    // the document load.
    //
    // Why the v1 page endpoint is not used instead: it is reachable for a
    // service principal (``resource_type = "page"`` in
    // ``plane.api.views.page.PageDetailAPIEndpoint``) and runs the
    // scope -> grant chain, but it serves the page JSON only — the
    // collaborative binary (``description_binary``) is not part of the v1
    // serializer (``plane.api.serializers.page.PageSerializer``), and
    // writing a page through the v1 endpoint regenerates the binary and
    // invalidates the live document
    // (``plane.utils.page_content.sync_page_description_formats``), which
    // would break the collaborative session on every save. The fix belongs
    // to the app layer (declare ``authz_resource_type`` on the page views),
    // which is out of this server's scope.
    if (serviceToken) {
      logger.warn("PAGE_AUTH: refusing a service principal on a page document", { pageId, workspaceSlug });
      throw new AppError("Service principals cannot open page documents", {
        code: "PAGE_SERVICE_PRINCIPAL_UNSUPPORTED",
        // The reason names the unsupported path, not the page — it says
        // nothing about the page the caller asked for.
        reason: "Service principals cannot open page documents",
      });
    }

    if (!workspaceSlug) {
      throw new AppError("Page document is missing the workspace slug", {
        code: "PAGE_MISSING_WORKSPACE",
      });
    }

    if (!projectId) {
      throw new AppError("Page document is missing the project id", {
        code: "PAGE_MISSING_PROJECT",
      });
    }

    if (!cookie) {
      throw new AppError("Page document is missing the session cookie", {
        code: "PAGE_MISSING_CREDENTIALS",
      });
    }

    try {
      // The page endpoint of the app layer — the same call the web client
      // makes when it opens a page. `track_visit=false` keeps the check out
      // of the "recently visited" list: an authorization probe is not a
      // visit.
      await this.get(
        `/api/workspaces/${encodeURIComponent(workspaceSlug)}/projects/${encodeURIComponent(projectId)}/pages/${pageId}/?track_visit=false`,
        { headers: { Cookie: cookie } }
      );
    } catch (error) {
      throw this.denied(pageId, workspaceSlug, projectId, error);
    }
  }

  private denied(pageId: string, workspaceSlug: string, projectId: string, cause?: unknown): AppError {
    logger.warn("PAGE_AUTH: denied page document", {
      pageId,
      workspaceSlug,
      projectId,
      statusCode: (cause as AppError | undefined)?.statusCode,
    });
    return new AppError("Not allowed to open this page", {
      code: "PAGE_FORBIDDEN",
    });
  }
}
