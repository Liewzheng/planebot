/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeAppApi } from "../../helpers/fake-app-api";

const apiState = vi.hoisted(() => ({ baseURL: "http://127.0.0.1:1" }));

vi.mock("@/env", () => ({
  env: {
    get API_BASE_URL() {
      return apiState.baseURL;
    },
    APP_VERSION: "1.0.0",
    HOSTNAME: "test",
    PORT: "3000",
    CORS_ALLOWED_ORIGINS: "",
    LIVE_BASE_PATH: "/live",
    COMPRESSION_LEVEL: 6,
    COMPRESSION_THRESHOLD: 5000,
    LIVE_SERVER_SECRET_KEY: "test-secret",
    LIVE_INTERNAL_API_KEY: "",
    REDIS_HOST: undefined,
    REDIS_PORT: 6379,
    REDIS_URL: undefined,
  },
}));

const loggerMock = vi.hoisted(() => ({
  warn: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  debug: vi.fn(),
}));

vi.mock("@plane/logger", () => ({ logger: loggerMock }));

const { IssueEventsAuthorizationService, parseIssueEventsProjectId } = await import("@/services/issue-events.service");
const { assertIssueEventsChannelAccess } = await import("@/lib/issue-events-auth");

const WORKSPACE_SLUG = "acme";
const PROJECT_ID = "11111111-1111-4111-8111-111111111111";
const SECRET_PROJECT_ID = "22222222-2222-4222-8222-222222222222";
const USER = { id: "user-1", display_name: "Ada Lovelace", cookie: "session=user-1" };
const SERVICE_TOKEN = "plane_svc_0123456789abcdef";

const appApi = new FakeAppApi();

const context = (overrides: Record<string, unknown> = {}) => ({
  projectId: null as string | null,
  cookie: USER.cookie,
  documentType: "project_page" as const,
  workspaceSlug: WORKSPACE_SLUG,
  userId: USER.id,
  serviceToken: null as string | null,
  servicePrincipalId: null as string | null,
  ...overrides,
});

const service = () => new IssueEventsAuthorizationService();

beforeAll(async () => {
  apiState.baseURL = await appApi.start();
});

afterAll(async () => {
  await appApi.stop();
});

beforeEach(() => {
  appApi.requests.length = 0;
  loggerMock.warn.mockClear();
  loggerMock.error.mockClear();
});

describe("parseIssueEventsProjectId", () => {
  it("takes the project id from the document name", () => {
    expect(parseIssueEventsProjectId(`issue-events:${PROJECT_ID}`)).toBe(PROJECT_ID);
  });

  it("rejects names that do not carry a project id", () => {
    expect(parseIssueEventsProjectId("issue-events:")).toBeNull();
    expect(parseIssueEventsProjectId("issue-events:not-a-uuid")).toBeNull();
    expect(parseIssueEventsProjectId(`issue-events:${PROJECT_ID}/extra`)).toBeNull();
    expect(parseIssueEventsProjectId(`page:${PROJECT_ID}`)).toBeNull();
  });
});

describe("IssueEventsAuthorizationService — human path", () => {
  it("allows a member of the project", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [PROJECT_ID]: 15 } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: 15 } },
      },
    });

    await expect(
      service().authorize({ workspaceSlug: WORKSPACE_SLUG, projectId: PROJECT_ID, cookie: USER.cookie })
    ).resolves.toBeUndefined();

    // the membership lookup carries user + project + workspace in one query
    expect(appApi.requests.map((request) => request.url)).toEqual([
      `/api/workspaces/${WORKSPACE_SLUG}/projects/${PROJECT_ID}/project-members/me/`,
    ]);
    expect(appApi.requests[0].headers.cookie).toBe(USER.cookie);
  });

  it("allows a project guest (role 5)", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [PROJECT_ID]: 5 } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: 15 } },
      },
    });

    await expect(
      service().authorize({ workspaceSlug: WORKSPACE_SLUG, projectId: PROJECT_ID, cookie: USER.cookie })
    ).resolves.toBeUndefined();
  });

  it("denies a user who is not a member of the project", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: {} },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: 15 } },
      },
    });

    await expect(
      service().authorize({ workspaceSlug: WORKSPACE_SLUG, projectId: PROJECT_ID, cookie: USER.cookie })
    ).rejects.toMatchObject({ code: "ISSUE_EVENTS_FORBIDDEN" });
  });

  it("denies a workspace member of a public project who is not a member of it", async () => {
    // network=2 widens the project *listing*, not the issue read: the app API
    // still asks for a project membership row (allow_permission, level PROJECT)
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: {} },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: 15 } },
        projectNetworks: { [PROJECT_ID]: 2 },
      },
    });

    await expect(
      service().authorize({ workspaceSlug: WORKSPACE_SLUG, projectId: PROJECT_ID, cookie: USER.cookie })
    ).rejects.toMatchObject({ code: "ISSUE_EVENTS_FORBIDDEN" });
  });

  it("allows a workspace admin who is not a member of the project", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: {} },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: 20 } },
      },
    });

    await expect(
      service().authorize({ workspaceSlug: WORKSPACE_SLUG, projectId: SECRET_PROJECT_ID, cookie: USER.cookie })
    ).resolves.toBeUndefined();

    expect(appApi.requests.map((request) => request.url)).toEqual([
      `/api/workspaces/${WORKSPACE_SLUG}/projects/${SECRET_PROJECT_ID}/project-members/me/`,
      `/api/workspaces/${WORKSPACE_SLUG}/workspace-members/me/`,
    ]);
  });

  it("denies when the workspace slug is missing", async () => {
    await expect(
      service().authorize({ workspaceSlug: null, projectId: PROJECT_ID, cookie: USER.cookie })
    ).rejects.toMatchObject({ code: "ISSUE_EVENTS_MISSING_WORKSPACE" });
  });

  it("denies when no credential is available", async () => {
    await expect(
      service().authorize({ workspaceSlug: WORKSPACE_SLUG, projectId: PROJECT_ID, cookie: null })
    ).rejects.toMatchObject({ code: "ISSUE_EVENTS_MISSING_CREDENTIALS" });
  });
});

describe("IssueEventsAuthorizationService — service principal path", () => {
  it("allows a service principal with an active grant on the project", async () => {
    appApi.setOptions({
      user: USER,
      access: { projectRoles: {}, workspaceRoles: {} },
      serviceTokens: { [SERVICE_TOKEN]: [PROJECT_ID] },
    });

    await expect(
      service().authorize({
        workspaceSlug: WORKSPACE_SLUG,
        projectId: PROJECT_ID,
        serviceToken: SERVICE_TOKEN,
      })
    ).resolves.toBeUndefined();

    expect(appApi.requests.map((request) => request.url)).toEqual([
      `/api/v1/workspaces/${WORKSPACE_SLUG}/projects/${PROJECT_ID}/issues/?per_page=1`,
    ]);
    expect(appApi.requests[0].headers["x-api-key"]).toBe(SERVICE_TOKEN);
  });

  it("denies a service principal without a grant on the project", async () => {
    appApi.setOptions({
      user: USER,
      access: { projectRoles: {}, workspaceRoles: {} },
      serviceTokens: { [SERVICE_TOKEN]: [SECRET_PROJECT_ID] },
    });

    await expect(
      service().authorize({
        workspaceSlug: WORKSPACE_SLUG,
        projectId: PROJECT_ID,
        serviceToken: SERVICE_TOKEN,
      })
    ).rejects.toMatchObject({ code: "ISSUE_EVENTS_FORBIDDEN" });
  });
});

describe("assertIssueEventsChannelAccess", () => {
  it("does nothing for documents outside the issue-events namespace", async () => {
    appApi.setOptions({ user: USER, access: { projectRoles: {}, workspaceRoles: {} } });

    await expect(
      assertIssueEventsChannelAccess({ context: context(), documentName: `page:${PROJECT_ID}` })
    ).resolves.toBeUndefined();

    expect(appApi.requests).toHaveLength(0);
  });

  it("rejects a malformed issue-events document name", async () => {
    await expect(
      assertIssueEventsChannelAccess({ context: context(), documentName: "issue-events:not-a-uuid" })
    ).rejects.toMatchObject({ code: "ISSUE_EVENTS_INVALID_DOCUMENT" });

    expect(appApi.requests).toHaveLength(0);
  });

  it("uses the project id of the document name, not the one of the context", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [PROJECT_ID]: 15 } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: 15 } },
      },
    });

    // the context carries another project id (the client supplied query
    // parameter) — the document name wins
    await expect(
      assertIssueEventsChannelAccess({
        context: context({ projectId: SECRET_PROJECT_ID }),
        documentName: `issue-events:${PROJECT_ID}`,
      })
    ).resolves.toBeUndefined();

    expect(appApi.requests.map((request) => request.url)).toEqual([
      `/api/workspaces/${WORKSPACE_SLUG}/projects/${PROJECT_ID}/project-members/me/`,
    ]);
  });
});
