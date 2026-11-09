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

/**
 * Document name prefix of the ephemeral issue-event channel.
 *
 * The channel is a **broadcast bus**: every connection on
 * ``issue-events:<project_id>`` receives the project's issue events
 * (``project_id`` / ``issue_id`` / ``type`` / ``actor_id``).  The
 * subscription is therefore the authorization boundary — there is no
 * per-message filter behind it.
 */
export const ISSUE_EVENTS_DOCUMENT_PREFIX = "issue-events:";

/** A project id is a UUID; anything else is not a document we can authorize. */
const PROJECT_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Plane workspace role integer of a workspace admin (20). */
const WORKSPACE_ADMIN_ROLE = 20;

/**
 * Extract the project id an issue-events document name refers to.
 *
 * The project id is taken from the **document name only** — the
 * ``projectId`` query parameter is client supplied and must never be
 * trusted (see :func:`assertIssueEventsChannelAccess`).
 *
 * @param documentName - The hocuspocus document name
 * @returns The project id, or ``null`` when the name is not a well formed
 *          issue-events document
 */
export const parseIssueEventsProjectId = (documentName: string): string | null => {
  if (!documentName.startsWith(ISSUE_EVENTS_DOCUMENT_PREFIX)) return null;
  const projectId = documentName.slice(ISSUE_EVENTS_DOCUMENT_PREFIX.length);
  if (!PROJECT_ID_PATTERN.test(projectId)) return null;
  return projectId.toLowerCase();
};

export type TIssueEventsAuthorizationParams = {
  /** workspace slug of the request (client supplied, but only ever used to scope the lookup) */
  workspaceSlug: string | null;
  /** project id parsed from the document name */
  projectId: string;
  /** session cookie of the human caller */
  cookie?: string | null;
  /** service principal token of a bot caller */
  serviceToken?: string | null;
};

/**
 * Decides whether a connection may subscribe to an issue-events channel.
 *
 * The decision is delegated to the app API in both cases — the live server
 * keeps no grant/permission state of its own:
 *
 * **Humans** — the app API's own rule for "may this user read this
 * project's issues":
 *
 * 1. an active ``ProjectMember`` row for (user, project, workspace).  The
 *    endpoint filters on all three at once, so a forged workspace slug
 *    cannot widen the answer
 *    (``plane.app.views.project.member.ProjectMemberUserEndpoint``).
 * 2. workspace admins own the workspace and may read every project under
 *    it, including the ones they are not a member of
 *    (``plane.app.permissions.base.allow_permission`` — the SAFE_METHODS
 *    workspace-admin bypass).  The project still has to live in that
 *    workspace: the app API binds the two (``IssueViewSet.list`` does
 *    ``Project.objects.get(pk=project_id, workspace__slug=slug)``), so the
 *    admin branch additionally asks for the project itself.
 *
 * **Service principals** — the v1 API runs the authz engine for the
 * work-item collection of that project, so the service principal needs the
 * matching scope *and* an active ``ProjectGrant`` for the project
 * (``plane.core.authz.engine.authorize`` — scope → grant → role cap →
 * owner intersection).
 */
export class IssueEventsAuthorizationService extends APIService {
  /**
   * @throws {AppError} when the caller may not subscribe to the channel
   */
  async authorize({ workspaceSlug, projectId, cookie, serviceToken }: TIssueEventsAuthorizationParams): Promise<void> {
    if (!workspaceSlug) {
      throw new AppError("Issue events channel is missing the workspace slug", {
        code: "ISSUE_EVENTS_MISSING_WORKSPACE",
      });
    }

    if (serviceToken) {
      await this.authorizeServicePrincipal({ workspaceSlug, projectId, serviceToken });
      return;
    }

    if (!cookie) {
      throw new AppError("Issue events channel is missing the session cookie", {
        code: "ISSUE_EVENTS_MISSING_CREDENTIALS",
      });
    }

    await this.authorizeHuman({ workspaceSlug, projectId, cookie });
  }

  /**
   * Human path: the app API decides whether the caller may read the
   * project's issues.
   */
  private async authorizeHuman({
    workspaceSlug,
    projectId,
    cookie,
  }: {
    workspaceSlug: string;
    projectId: string;
    cookie: string;
  }): Promise<void> {
    const headers = { Cookie: cookie };

    // 1. active project membership — proves user + project + workspace at once
    try {
      await this.get(`/api/workspaces/${encodeURIComponent(workspaceSlug)}/projects/${projectId}/project-members/me/`, {
        headers,
      });
      return;
    } catch (error) {
      // The app API answers **404** when the caller is not a member of the
      // project (`ProjectMember.DoesNotExist` → `BaseAPIView.handle_exception`
      // → 404, `plane.app.views.project.member.ProjectMemberUserEndpoint`).
      // Every other answer (401/403/5xx/network) is a denial and must not
      // fall through to the workspace-admin branch below.
      if ((error as AppError).statusCode !== 404) {
        throw this.denied(projectId, workspaceSlug, error);
      }
    }

    // 2. workspace admins may read every project of their workspace — but the
    //    project has to live in that workspace. The app API binds the two
    //    (`IssueViewSet.list` does `Project.objects.get(pk=project_id,
    //    workspace__slug=slug)`, so a foreign project is a 404 there, even
    //    for an admin of the workspace the client claims). A caller that is
    //    neither a member nor an admin stops here.
    let isWorkspaceAdmin = false;
    try {
      const response = await this.get(`/api/workspaces/${encodeURIComponent(workspaceSlug)}/workspace-members/me/`, {
        headers,
      });
      isWorkspaceAdmin = (response?.data as { role?: number } | undefined)?.role === WORKSPACE_ADMIN_ROLE;
    } catch (error) {
      throw this.denied(projectId, workspaceSlug, error);
    }
    if (!isWorkspaceAdmin) {
      throw this.denied(projectId, workspaceSlug);
    }

    // 3. admin branch: bind the project to the workspace. The project
    //    endpoint's queryset filters `workspace__slug`, so a project outside
    //    the workspace answers 404 and the subscription is refused. (It also
    //    records the admin in the "recently visited" list — the same entry
    //    the web client writes when it opens the project.)
    try {
      await this.get(`/api/workspaces/${encodeURIComponent(workspaceSlug)}/projects/${projectId}/`, { headers });
    } catch (error) {
      throw this.denied(projectId, workspaceSlug, error);
    }
  }

  /**
   * Service principal path: the v1 API runs ``authorize()`` for the
   * work-item collection of that project, which requires an active
   * ``ProjectGrant`` for the project.
   */
  private async authorizeServicePrincipal({
    workspaceSlug,
    projectId,
    serviceToken,
  }: {
    workspaceSlug: string;
    projectId: string;
    serviceToken: string;
  }): Promise<void> {
    try {
      await this.get(
        `/api/v1/workspaces/${encodeURIComponent(workspaceSlug)}/projects/${projectId}/issues/?per_page=1`,
        { headers: { "X-Api-Key": serviceToken } }
      );
    } catch (error) {
      throw this.denied(projectId, workspaceSlug, error);
    }
  }

  private denied(projectId: string, workspaceSlug: string, cause?: unknown): AppError {
    logger.warn("ISSUE_EVENTS_AUTH: denied issue events subscription", {
      projectId,
      workspaceSlug,
      statusCode: (cause as AppError | undefined)?.statusCode,
    });
    return new AppError("Not allowed to subscribe to the issue events channel", {
      code: "ISSUE_EVENTS_FORBIDDEN",
    });
  }
}
