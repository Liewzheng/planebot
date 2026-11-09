/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeAppApi, PAGE_ACCESS_PUBLIC, ROLE_MEMBER } from "../../helpers/fake-app-api";

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
const PAGE_ID = "33333333-3333-4333-8333-333333333333";
const USER = { id: "user-1", display_name: "Ada Lovelace", cookie: "session=user-1" };
const OTHER_USER = { id: "user-2", display_name: "Grace Hopper", cookie: "session=user-2" };

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
    projectId: PROJECT_ID,
    ...overrides,
  })) {
    params.set(key, value);
  }
  return params;
};

const humanToken = JSON.stringify({ id: USER.id, cookie: USER.cookie });

const pageFixture = () => ({
  [PAGE_ID]: {
    workspaceSlug: WORKSPACE_SLUG,
    projectId: PROJECT_ID,
    access: PAGE_ACCESS_PUBLIC,
    ownedBy: USER.id,
  },
});

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

describe("onAuthenticate — page documents", () => {
  it("lets a member of the project open the page", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [WORKSPACE_SLUG]: { [PROJECT_ID]: ROLE_MEMBER } } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: ROLE_MEMBER } },
      },
      projects: { [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG } },
      pages: pageFixture(),
    });

    const result = await onAuthenticate({
      requestHeaders: {},
      requestParameters: parameters(),
      context: emptyContext(),
      token: humanToken,
      documentName: PAGE_ID,
    });

    expect(result.user).toEqual({ id: USER.id, name: USER.display_name });
    expect(appApi.requests.map((request) => request.url)).toEqual([
      "/api/users/me/",
      `/api/workspaces/${WORKSPACE_SLUG}/projects/${PROJECT_ID}/pages/${PAGE_ID}/?track_visit=false`,
    ]);
  });

  it("refuses a user who is not a member of the project", async () => {
    appApi.setOptions({
      user: OTHER_USER,
      access: {
        projectRoles: { [OTHER_USER.id]: { [WORKSPACE_SLUG]: {} } },
        workspaceRoles: { [OTHER_USER.id]: { [WORKSPACE_SLUG]: ROLE_MEMBER } },
      },
      projects: { [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG } },
      pages: pageFixture(),
    });

    await expect(
      onAuthenticate({
        requestHeaders: {},
        requestParameters: parameters(),
        context: emptyContext(),
        token: JSON.stringify({ id: OTHER_USER.id, cookie: OTHER_USER.cookie }),
        documentName: PAGE_ID,
      })
    ).rejects.toMatchObject({ code: "PAGE_FORBIDDEN" });
  });

  it("refuses a blank document name without asking the app API for a page", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [WORKSPACE_SLUG]: { [PROJECT_ID]: ROLE_MEMBER } } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: ROLE_MEMBER } },
      },
      projects: { [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG } },
      pages: pageFixture(),
    });

    await expect(
      onAuthenticate({
        requestHeaders: {},
        requestParameters: parameters(),
        context: emptyContext(),
        token: humanToken,
        documentName: "",
      })
    ).rejects.toMatchObject({ code: "PAGE_INVALID_DOCUMENT" });

    // the session lookup is the only app API call — the gate must not fall
    // through to a page lookup with an empty page id
    expect(appApi.requests.map((request) => request.url)).toEqual(["/api/users/me/"]);
  });

  it("refuses a missing document name without asking the app API for a page", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [WORKSPACE_SLUG]: { [PROJECT_ID]: ROLE_MEMBER } } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: ROLE_MEMBER } },
      },
      projects: { [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG } },
      pages: pageFixture(),
    });

    await expect(
      onAuthenticate({
        requestHeaders: {},
        requestParameters: parameters(),
        context: emptyContext(),
        token: humanToken,
      })
    ).rejects.toMatchObject({ code: "PAGE_INVALID_DOCUMENT" });

    expect(appApi.requests.map((request) => request.url)).toEqual(["/api/users/me/"]);
  });

  it("refuses a malformed document name without asking the app API for a page", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [WORKSPACE_SLUG]: { [PROJECT_ID]: ROLE_MEMBER } } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: ROLE_MEMBER } },
      },
      projects: { [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG } },
      pages: pageFixture(),
    });

    await expect(
      onAuthenticate({
        requestHeaders: {},
        requestParameters: parameters(),
        context: emptyContext(),
        token: humanToken,
        documentName: "not-a-page-id",
      })
    ).rejects.toMatchObject({ code: "PAGE_INVALID_DOCUMENT" });

    expect(appApi.requests.map((request) => request.url)).toEqual(["/api/users/me/"]);
  });
});
