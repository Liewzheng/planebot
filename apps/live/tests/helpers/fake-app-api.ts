/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";

export type TProjectAccess = {
  /** user id -> project id -> the role the user holds in that project (20/15/5) */
  projectRoles?: Record<string, Record<string, number>>;
  /** user id -> workspace slug -> the role the user holds in that workspace */
  workspaceRoles?: Record<string, Record<string, number>>;
  /** project id -> `network` value of the project (0 = secret, 2 = public) */
  projectNetworks?: Record<string, number>;
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
  /** service-principal tokens that hold an active grant on the project */
  serviceTokens?: Record<string, string[]>;
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
 *    200 with the membership row, 404 when the user is not a member
 *  - `GET /api/workspaces/<slug>/workspace-members/me/` — 200 with the
 *    workspace membership (empty body when the user is not a workspace member)
 *  - `GET /api/v1/workspaces/<slug>/projects/<pid>/issues/` — 200 for a
 *    service principal holding an active grant on the project, 403 otherwise
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
    const method = req.method ?? "GET";

    const json = (status: number, body: unknown) => {
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(body));
    };

    if (method !== "GET") {
      json(405, { error: "method not allowed" });
      return;
    }

    // Session user
    if (url === "/api/users/me/") {
      if (!user || !cookie.includes(user.cookie)) {
        json(401, { error: "authentication required" });
        return;
      }
      json(200, { id: user.id, display_name: user.display_name });
      return;
    }

    // Service-principal work-item read (v1 API) — the grant decides.
    const v1Match = url.match(/^\/api\/v1\/workspaces\/([^/]+)\/projects\/([^/]+)\/issues\/?/);
    if (v1Match) {
      const projectId = v1Match[2];
      const granted = this.options.serviceTokens?.[apiKey] ?? [];
      if (!apiKey || !granted.includes(projectId)) {
        json(403, { error: "You don't have the required permissions." });
        return;
      }
      json(200, { results: [], count: 0 });
      return;
    }

    // Principal dispatch (service principal identity)
    const dispatchMatch = url.match(/^\/api\/workspaces\/([^/]+)\/principal\/permissions\/?/);
    if (dispatchMatch) {
      if (!apiKey || !(apiKey in (this.options.serviceTokens ?? {}))) {
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

    // Project membership of the session user
    const memberMatch = url.match(/^\/api\/workspaces\/([^/]+)\/projects\/([^/]+)\/project-members\/me\/?/);
    if (memberMatch) {
      const [, workspaceSlug, projectId] = memberMatch;
      const role = access.projectRoles?.[user?.id ?? ""]?.[projectId];
      if (!user || !cookie.includes(user.cookie)) {
        json(401, { error: "authentication required" });
        return;
      }
      if (role === undefined) {
        // Not a member of the project — the real endpoint raises DoesNotExist.
        json(404, { error: "The required object does not exist." });
        return;
      }
      json(200, { id: `${projectId}-membership`, role, workspace: workspaceSlug });
      return;
    }

    // Workspace membership of the session user
    const workspaceMatch = url.match(/^\/api\/workspaces\/([^/]+)\/workspace-members\/me\/?/);
    if (workspaceMatch) {
      const workspaceSlug = workspaceMatch[1];
      const role = access.workspaceRoles?.[user?.id ?? ""]?.[workspaceSlug];
      if (!user || !cookie.includes(user.cookie)) {
        json(401, { error: "authentication required" });
        return;
      }
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
