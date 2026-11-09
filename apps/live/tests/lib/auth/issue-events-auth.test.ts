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

const { onAuthenticate } = await import("@/lib/auth");

const WORKSPACE_SLUG = "acme";
const PROJECT_ID = "11111111-1111-4111-8111-111111111111";
const SECRET_PROJECT_ID = "22222222-2222-4222-8222-222222222222";
const USER = { id: "user-1", display_name: "Ada Lovelace", cookie: "session=user-1" };
const SERVICE_TOKEN = "plane_svc_0123456789abcdef";

const appApi = new FakeAppApi();

const emptyContext = () => ({
  projectId: null as string | null,
  cookie: "",
  documentType: "project_page" as const,
  workspaceSlug: null as string | null,
  userId: "",
  serviceToken: null as string | null,
  servicePrincipalId: null as string | null,
});

const parameters = (overrides: Record<string, string> = {}) => {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries({
    documentType: "project_page",
    workspaceSlug: WORKSPACE_SLUG,
    ...overrides,
  })) {
    params.set(key, value);
  }
  return params;
};

const humanToken = JSON.stringify({ id: USER.id, cookie: USER.cookie });

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

describe("onAuthenticate — issue-events channel", () => {
  it("refuses a connection to the issue-events channel of a project the user is not a member of", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [WORKSPACE_SLUG]: {} } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: 15 } },
      },
      projects: {
        [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG, network: 2 },
        [SECRET_PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG, network: 0 },
      },
    });

    await expect(
      onAuthenticate({
        requestHeaders: {},
        requestParameters: parameters({ projectId: PROJECT_ID }),
        context: emptyContext(),
        token: humanToken,
        documentName: `issue-events:${PROJECT_ID}`,
      })
    ).rejects.toMatchObject({ code: "ISSUE_EVENTS_FORBIDDEN" });
  });

  it("accepts a connection to the issue-events channel of a project the user is a member of", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [WORKSPACE_SLUG]: { [PROJECT_ID]: 15 } } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: 15 } },
      },
      projects: {
        [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG, network: 2 },
        [SECRET_PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG, network: 0 },
      },
    });

    const context = emptyContext();
    const result = await onAuthenticate({
      requestHeaders: {},
      requestParameters: parameters({ projectId: PROJECT_ID }),
      context,
      token: humanToken,
      documentName: `issue-events:${PROJECT_ID}`,
    });

    expect(result.user).toEqual({ id: USER.id, name: USER.display_name });
  });

  it("ignores the client supplied projectId query parameter", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        // the user is a member of PROJECT_ID only
        projectRoles: { [USER.id]: { [WORKSPACE_SLUG]: { [PROJECT_ID]: 15 } } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: 15 } },
      },
      projects: {
        [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG, network: 2 },
        [SECRET_PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG, network: 0 },
      },
    });

    // the document name asks for the secret project, the query parameter
    // claims the project the user is a member of
    await expect(
      onAuthenticate({
        requestHeaders: {},
        requestParameters: parameters({ projectId: PROJECT_ID }),
        context: emptyContext(),
        token: humanToken,
        documentName: `issue-events:${SECRET_PROJECT_ID}`,
      })
    ).rejects.toMatchObject({ code: "ISSUE_EVENTS_FORBIDDEN" });

    expect(appApi.requests.map((request) => request.url)).toContain(
      `/api/workspaces/${WORKSPACE_SLUG}/projects/${SECRET_PROJECT_ID}/project-members/me/`
    );
  });

  it("leaves the page documents alone", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [WORKSPACE_SLUG]: {} } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: 15 } },
      },
      projects: {
        [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG, network: 2 },
        [SECRET_PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG, network: 0 },
      },
    });

    const context = emptyContext();
    const result = await onAuthenticate({
      requestHeaders: {},
      requestParameters: parameters({ projectId: PROJECT_ID }),
      context,
      token: humanToken,
      documentName: `page:${PROJECT_ID}`,
    });

    expect(result.user).toEqual({ id: USER.id, name: USER.display_name });
    // no issue-events lookup happened
    expect(appApi.requests.map((request) => request.url)).toEqual(["/api/users/me/"]);
  });

  it("refuses a service principal without a grant on the project", async () => {
    appApi.setOptions({
      user: USER,
      access: { projectRoles: {}, workspaceRoles: {} },
      servicePrincipals: { [SERVICE_TOKEN]: { grants: [SECRET_PROJECT_ID], scopes: ["work_item:read"] } },
    });

    await expect(
      onAuthenticate({
        requestHeaders: {},
        requestParameters: parameters({ projectId: PROJECT_ID }),
        context: emptyContext(),
        token: SERVICE_TOKEN,
        documentName: `issue-events:${PROJECT_ID}`,
      })
    ).rejects.toMatchObject({ code: "ISSUE_EVENTS_FORBIDDEN" });
  });

  it("accepts a service principal with a grant on the project", async () => {
    appApi.setOptions({
      user: USER,
      access: { projectRoles: {}, workspaceRoles: {} },
      servicePrincipals: { [SERVICE_TOKEN]: { grants: [PROJECT_ID], scopes: ["work_item:read"] } },
    });

    const context = emptyContext();
    const result = await onAuthenticate({
      requestHeaders: {},
      requestParameters: parameters({ projectId: PROJECT_ID }),
      context,
      token: SERVICE_TOKEN,
      documentName: `issue-events:${PROJECT_ID}`,
    });

    expect(result.user).toEqual({ id: "sp-1", name: "svc:sp-1" });
    expect(appApi.requests.map((request) => request.url)).toContain(
      `/api/v1/workspaces/${WORKSPACE_SLUG}/projects/${PROJECT_ID}/issues/?per_page=1`
    );
  });
});
