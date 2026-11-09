/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";

/** The role integers Plane uses (20 admin, 15 member, 5 guest). */
export const ROLE_ADMIN = 20;
export const ROLE_MEMBER = 15;
export const ROLE_GUEST = 5;

/** `Project.network` values (0 secret, 2 public). */
export const NETWORK_SECRET = 0;
export const NETWORK_PUBLIC = 2;

export type TProjectFixture = {
  /** the workspace the project lives in */
  workspaceSlug: string;
  /** `network` value of the project (0 = secret, 2 = public) */
  network?: number;
};

export type TServicePrincipalFixture = {
  /** project ids the service principal holds an active ProjectGrant on */
  grants: string[];
  /** scope rows as `resource_type:action` (e.g. `work_item:read`) */
  scopes: string[];
};

export type TProjectAccess = {
  /** user id -> workspace slug -> project id -> the role the user holds in that project */
  projectRoles?: Record<string, Record<string, Record<string, number>>>;
  /** user id -> workspace slug -> the role the user holds in that workspace */
  workspaceRoles?: Record<string, Record<string, number>>;
};

export type TFakeAppApiUser = {
  id: string;
  display_name: string;
  /** session cookie value that identifies this user */
  cookie: string;
};

export type TFakeAppApiOptions = {
  /** the user the session cookie belongs to */
  user?: TFakeAppApiUser;
  /** what the user may do in each project / workspace */
  access?: TProjectAccess;
  /** project id -> the project (its workspace and network value) */
  projects?: Record<string, TProjectFixture>;
  /** service token -> the service principal it resolves to */
  servicePrincipals?: Record<string, TServicePrincipalFixture>;
  /**
   * Forces the status of the `project-members/me` endpoint. The real
   * endpoint answers 404 for a non-member; a different status models an
   * app API that refuses the lookup for another reason.
   */
  membershipStatus?: number;
};

/**
 * Minimal stand-in for the Django app API / v1 API.
 *
 * It implements only the endpoints the live server talks to, with the same
 * status codes the real endpoints answer with, so the live server's
 * authorization decisions are exercised over real HTTP (real axios error
 * mapping included) instead of a stubbed service object.
 *
 * Routes:
 *  - `GET /api/users/me/` — the session user's profile (401 without a cookie)
 *  - `GET /api/workspaces/<slug>/projects/<pid>/project-members/me/` —
 *    200 with the membership row, 404 when the user is not a member of
 *    (workspace, project) — the real query filters on all three
 *  - `GET /api/workspaces/<slug>/workspace-members/me/` — 200 with the
 *    workspace membership (empty body when the user is not a workspace member)
 *  - `GET /api/workspaces/<slug>/projects/<pid>/` — the project itself:
 *    404 when the project does not live in that workspace, 403/409 for a
 *    workspace member who is neither a member of the project nor a
 *    workspace admin, 200 otherwise
 *  - `GET /api/v1/workspaces/<slug>/projects/<pid>/issues/` — 200 for a
 *    service principal that holds the `work_item:read` scope *and* an
 *    active grant on the project, 403 otherwise
 *  - `GET /api/workspaces/<slug>/principal/permissions/` — the service
 *    principal dispatch endpoint (401 for an unknown token)
 */
export class FakeAppApi {
  private server: Server | null = null;
  private baseURL = "";
  private options: TFakeAppApiOptions;
  /** every request the live server made, in order */
  public readonly requests: { url: string; headers: Record<string, unknown> }[] = [];

  constructor(options: TFakeAppApiOptions = {}) {
    this.options = options;
  }

  public setOptions(options: TFakeAppApiOptions) {
    this.options = options;
  }

  public get baseUrl() {
    return this.baseURL;
  }

  public async start(): Promise<string> {
    this.server = createServer((req, res) => this.handle(req, res));
    await new Promise<void>((resolve) => this.server?.listen(0, "127.0.0.1", resolve));
    const address = this.server.address() as AddressInfo;
    this.baseURL = `http://127.0.0.1:${address.port}`;
    return this.baseURL;
  }

  public async stop(): Promise<void> {
    if (!this.server) return;
    await new Promise<void>((resolve) => this.server?.close(() => resolve()));
    this.server = null;
  }

  private handle(req: IncomingMessage, res: ServerResponse) {
    const url = req.url ?? "";
    const headers = req.headers as Record<string, unknown>;
    this.requests.push({ url, headers });

    const cookie = String(headers.cookie ?? "");
    const apiKey = String(headers["x-api-key"] ?? "");
    const user = this.options.user;
    const access = this.options.access ?? {};
    const projects = this.options.projects ?? {};
    const method = req.method ?? "GET";

    const json = (status: number, body: unknown) => {
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(body));
    };

    if (method !== "GET") {
      json(405, { error: "method not allowed" });
      return;
    }

    const isSessionUser = () => Boolean(user) && cookie.includes(user?.cookie ?? "");

    // Session user
    if (url === "/api/users/me/") {
      if (!isSessionUser()) {
        json(401, { error: "authentication required" });
        return;
      }
      json(200, { id: user?.id, display_name: user?.display_name });
      return;
    }

    // Service-principal work-item read (v1 API) — scope AND grant decide.
    const v1Match = url.match(/^\/api\/v1\/workspaces\/([^/]+)\/projects\/([^/]+)\/issues\/?/);
    if (v1Match) {
      const projectId = v1Match[2];
      const principal = this.options.servicePrincipals?.[apiKey];
      const hasScope = Boolean(principal?.scopes.includes("work_item:read"));
      const hasGrant = Boolean(principal?.grants.includes(projectId));
      if (!principal || !hasScope || !hasGrant) {
        json(403, { error: "You don't have the required permissions." });
        return;
      }
      json(200, { results: [], count: 0 });
      return;
    }

    // Principal dispatch (service principal identity)
    const dispatchMatch = url.match(/^\/api\/workspaces\/([^/]+)\/principal\/permissions\/?/);
    if (dispatchMatch) {
      if (!apiKey || !(apiKey in (this.options.servicePrincipals ?? {}))) {
        json(401, { error: "Authentication required" });
        return;
      }
      json(200, {
        principal: { kind: "service", id: "sp-1", workspace_role: null },
        members: [],
        sp_assignable: false,
        permissions: {},
      });
      return;
    }

    // Project membership of the session user — the real query filters on
    // project id, workspace slug, member and is_active together.
    const memberMatch = url.match(/^\/api\/workspaces\/([^/]+)\/projects\/([^/]+)\/project-members\/me\/?/);
    if (memberMatch) {
      const [, workspaceSlug, projectId] = memberMatch;
      if (!isSessionUser()) {
        json(401, { error: "authentication required" });
        return;
      }
      if (this.options.membershipStatus !== undefined) {
        json(this.options.membershipStatus, { error: "forced status" });
        return;
      }
      const role = access.projectRoles?.[user?.id ?? ""]?.[workspaceSlug]?.[projectId];
      if (role === undefined) {
        // Not a member of (workspace, project) — the real endpoint raises
        // DoesNotExist and the app API answers 404.
        json(404, { error: "The required object does not exist." });
        return;
      }
      json(200, { id: `${projectId}-membership`, role, workspace: workspaceSlug });
      return;
    }

    // The project itself — the queryset filters `workspace__slug`, so a
    // project outside the workspace is a 404.
    const projectMatch = url.match(/^\/api\/workspaces\/([^/]+)\/projects\/([^/]+)\/?$/);
    if (projectMatch) {
      const [, workspaceSlug, projectId] = projectMatch;
      if (!isSessionUser()) {
        json(401, { error: "authentication required" });
        return;
      }
      const project = projects[projectId];
      if (!project || project.workspaceSlug !== workspaceSlug) {
        json(404, { error: "Project does not exist" });
        return;
      }
      const workspaceRole = access.workspaceRoles?.[user?.id ?? ""]?.[workspaceSlug];
      if (workspaceRole === undefined) {
        // not a workspace member at all
        json(403, { error: "You don't have the required permissions." });
        return;
      }
      const isProjectMember = access.projectRoles?.[user?.id ?? ""]?.[workspaceSlug]?.[projectId] !== undefined;
      const isWorkspaceAdmin = workspaceRole === ROLE_ADMIN;
      if (!isProjectMember && !isWorkspaceAdmin) {
        if ((project.network ?? NETWORK_PUBLIC) === NETWORK_SECRET) {
          json(403, { error: "You do not have permission" });
        } else {
          json(409, { error: "You are not a member of this project" });
        }
        return;
      }
      json(200, { id: projectId, name: `project-${projectId}`, network: project.network ?? NETWORK_PUBLIC });
      return;
    }

    // Workspace membership of the session user
    const workspaceMatch = url.match(/^\/api\/workspaces\/([^/]+)\/workspace-members\/me\/?/);
    if (workspaceMatch) {
      const workspaceSlug = workspaceMatch[1];
      if (!isSessionUser()) {
        json(401, { error: "authentication required" });
        return;
      }
      const role = access.workspaceRoles?.[user?.id ?? ""]?.[workspaceSlug];
      if (role === undefined) {
        // Not a workspace member — the real endpoint serializes None.
        json(200, {});
        return;
      }
      json(200, { role });
      return;
    }

    json(404, { error: "not found" });
  }
}
