/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import {
  FakeAppApi,
  NETWORK_PUBLIC,
  PAGE_ACCESS_PRIVATE,
  PAGE_ACCESS_PUBLIC,
  ROLE_ADMIN,
  ROLE_GUEST,
  ROLE_MEMBER,
} from "../../helpers/fake-app-api";

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

const { PageAuthorizationService, parsePageId } = await import("@/services/page-authorization.service");
const { assertPageDocumentAccess } = await import("@/lib/page-auth");

const WORKSPACE_SLUG = "acme";
const OTHER_WORKSPACE_SLUG = "other";
const PROJECT_ID = "11111111-1111-4111-8111-111111111111";
const OTHER_PROJECT_ID = "22222222-2222-4222-8222-222222222222";
const PAGE_ID = "33333333-3333-4333-8333-333333333333";
const USER = { id: "user-1", display_name: "Ada Lovelace", cookie: "session=user-1" };
const OTHER_USER = { id: "user-2", display_name: "Grace Hopper", cookie: "session=user-2" };
const SERVICE_TOKEN = "plane_svc_0123456789abcdef";

const appApi = new FakeAppApi();

const context = (overrides: Record<string, unknown> = {}) => ({
  projectId: PROJECT_ID as string | null,
  cookie: USER.cookie,
  documentType: "project_page" as const,
  workspaceSlug: WORKSPACE_SLUG as string | null,
  userId: USER.id,
  serviceToken: null as string | null,
  servicePrincipalId: null as string | null,
  ...overrides,
});

const service = () => new PageAuthorizationService();

/** A page the caller may read: public, owned by somebody else. */
const publicPage = (ownedBy = USER.id) => ({
  [PAGE_ID]: {
    workspaceSlug: WORKSPACE_SLUG,
    projectId: PROJECT_ID,
    access: PAGE_ACCESS_PUBLIC,
    ownedBy,
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

describe("parsePageId", () => {
  it("takes the page id from the document name", () => {
    expect(parsePageId(PAGE_ID)).toBe(PAGE_ID);
  });

  it("rejects names that do not carry a page id", () => {
    expect(parsePageId("")).toBeNull();
    expect(parsePageId("not-a-uuid")).toBeNull();
    expect(parsePageId(`${PAGE_ID}/extra`)).toBeNull();
    expect(parsePageId(`page:${PAGE_ID}`)).toBeNull();
    expect(parsePageId(`issue-events:${PROJECT_ID}`)).toBeNull();
  });
});

describe("PageAuthorizationService — human path", () => {
  it("allows a member of the project", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [WORKSPACE_SLUG]: { [PROJECT_ID]: ROLE_MEMBER } } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: ROLE_MEMBER } },
      },
      projects: { [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG, network: NETWORK_PUBLIC } },
      pages: publicPage(),
    });

    await expect(
      service().authorize({
        workspaceSlug: WORKSPACE_SLUG,
        projectId: PROJECT_ID,
        pageId: PAGE_ID,
        cookie: USER.cookie,
      })
    ).resolves.toBeUndefined();

    // the page endpoint of the app layer — the rule the live server mirrors
    expect(appApi.requests.map((request) => request.url)).toEqual([
      `/api/workspaces/${WORKSPACE_SLUG}/projects/${PROJECT_ID}/pages/${PAGE_ID}/?track_visit=false`,
    ]);
    expect(appApi.requests[0].headers.cookie).toBe(USER.cookie);
  });

  it("allows the owner of a private page", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [WORKSPACE_SLUG]: { [PROJECT_ID]: ROLE_MEMBER } } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: ROLE_MEMBER } },
      },
      projects: { [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG } },
      pages: {
        [PAGE_ID]: {
          workspaceSlug: WORKSPACE_SLUG,
          projectId: PROJECT_ID,
          access: PAGE_ACCESS_PRIVATE,
          ownedBy: USER.id,
        },
      },
    });

    await expect(
      service().authorize({
        workspaceSlug: WORKSPACE_SLUG,
        projectId: PROJECT_ID,
        pageId: PAGE_ID,
        cookie: USER.cookie,
      })
    ).resolves.toBeUndefined();
  });

  it("denies a member of the project for a private page they do not own", async () => {
    appApi.setOptions({
      user: OTHER_USER,
      access: {
        projectRoles: { [OTHER_USER.id]: { [WORKSPACE_SLUG]: { [PROJECT_ID]: ROLE_MEMBER } } },
        workspaceRoles: { [OTHER_USER.id]: { [WORKSPACE_SLUG]: ROLE_MEMBER } },
      },
      projects: { [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG } },
      pages: {
        [PAGE_ID]: {
          workspaceSlug: WORKSPACE_SLUG,
          projectId: PROJECT_ID,
          access: PAGE_ACCESS_PRIVATE,
          ownedBy: USER.id,
        },
      },
    });

    await expect(
      service().authorize({
        workspaceSlug: WORKSPACE_SLUG,
        projectId: PROJECT_ID,
        pageId: PAGE_ID,
        cookie: OTHER_USER.cookie,
      })
    ).rejects.toMatchObject({ code: "PAGE_FORBIDDEN" });
  });

  it("denies a user who is not a member of the project", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [WORKSPACE_SLUG]: {} } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: ROLE_MEMBER } },
      },
      projects: { [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG, network: NETWORK_PUBLIC } },
      pages: publicPage(),
    });

    await expect(
      service().authorize({
        workspaceSlug: WORKSPACE_SLUG,
        projectId: PROJECT_ID,
        pageId: PAGE_ID,
        cookie: USER.cookie,
      })
    ).rejects.toMatchObject({ code: "PAGE_FORBIDDEN" });
  });

  it("denies a workspace member of a public project who is not a member of it", async () => {
    // the network flag widens the project *listing*, not the page read: the
    // page endpoints ask for a project membership row
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [WORKSPACE_SLUG]: {} } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: ROLE_MEMBER } },
      },
      projects: { [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG, network: NETWORK_PUBLIC } },
      pages: publicPage(),
    });

    await expect(
      service().authorize({
        workspaceSlug: WORKSPACE_SLUG,
        projectId: PROJECT_ID,
        pageId: PAGE_ID,
        cookie: USER.cookie,
      })
    ).rejects.toMatchObject({ code: "PAGE_FORBIDDEN" });
  });

  it("denies a workspace admin who is not a member of the project", async () => {
    // the page endpoints have no workspace admin bypass — that bypass lives
    // in `allow_permission`, which the page views do not use
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [WORKSPACE_SLUG]: {} } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: ROLE_ADMIN } },
      },
      projects: { [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG, network: NETWORK_PUBLIC } },
      pages: publicPage(),
    });

    await expect(
      service().authorize({
        workspaceSlug: WORKSPACE_SLUG,
        projectId: PROJECT_ID,
        pageId: PAGE_ID,
        cookie: USER.cookie,
      })
    ).rejects.toMatchObject({ code: "PAGE_FORBIDDEN" });
  });

  it("allows a guest that may view all features of the project", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [WORKSPACE_SLUG]: { [PROJECT_ID]: ROLE_GUEST } } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: ROLE_MEMBER } },
      },
      projects: { [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG, guestViewAllFeatures: true } },
      pages: publicPage(OTHER_USER.id),
    });

    await expect(
      service().authorize({
        workspaceSlug: WORKSPACE_SLUG,
        projectId: PROJECT_ID,
        pageId: PAGE_ID,
        cookie: USER.cookie,
      })
    ).resolves.toBeUndefined();
  });

  it("denies a guest that may not view all features of the project", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [WORKSPACE_SLUG]: { [PROJECT_ID]: ROLE_GUEST } } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: ROLE_MEMBER } },
      },
      projects: { [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG, guestViewAllFeatures: false } },
      pages: publicPage(OTHER_USER.id),
    });

    await expect(
      service().authorize({
        workspaceSlug: WORKSPACE_SLUG,
        projectId: PROJECT_ID,
        pageId: PAGE_ID,
        cookie: USER.cookie,
      })
    ).rejects.toMatchObject({ code: "PAGE_FORBIDDEN" });
  });

  it("denies a member when the client claims another workspace slug", async () => {
    // the page is bound to its workspace — the endpoint filters on the slug
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [WORKSPACE_SLUG]: { [PROJECT_ID]: ROLE_MEMBER } } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: ROLE_MEMBER, [OTHER_WORKSPACE_SLUG]: ROLE_MEMBER } },
      },
      projects: { [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG } },
      pages: publicPage(),
    });

    await expect(
      service().authorize({
        workspaceSlug: OTHER_WORKSPACE_SLUG,
        projectId: PROJECT_ID,
        pageId: PAGE_ID,
        cookie: USER.cookie,
      })
    ).rejects.toMatchObject({ code: "PAGE_FORBIDDEN" });
  });

  it("denies a member when the client claims another project id", async () => {
    // the page is bound to its project — the endpoint filters on the
    // project link of the page
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: {
          [USER.id]: { [WORKSPACE_SLUG]: { [PROJECT_ID]: ROLE_MEMBER, [OTHER_PROJECT_ID]: ROLE_MEMBER } },
        },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: ROLE_MEMBER } },
      },
      projects: {
        [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG },
        [OTHER_PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG },
      },
      pages: publicPage(),
    });

    await expect(
      service().authorize({
        workspaceSlug: WORKSPACE_SLUG,
        projectId: OTHER_PROJECT_ID,
        pageId: PAGE_ID,
        cookie: USER.cookie,
      })
    ).rejects.toMatchObject({ code: "PAGE_FORBIDDEN" });
  });

  it("denies when the app API answers with an error that is not a denial of the page", async () => {
    // the probe is a single call — every answer that is not a 2xx is a
    // refusal, and none of them may fall through
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [WORKSPACE_SLUG]: { [PROJECT_ID]: ROLE_MEMBER } } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: ROLE_MEMBER } },
      },
      projects: { [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG } },
      pages: publicPage(),
    });

    // a page the fixture does not know answers 404
    await expect(
      service().authorize({
        workspaceSlug: WORKSPACE_SLUG,
        projectId: PROJECT_ID,
        pageId: "55555555-5555-4555-8555-555555555555",
        cookie: USER.cookie,
      })
    ).rejects.toMatchObject({ code: "PAGE_FORBIDDEN" });
  });

  it("denies when the workspace slug is missing", async () => {
    await expect(
      service().authorize({ workspaceSlug: null, projectId: PROJECT_ID, pageId: PAGE_ID, cookie: USER.cookie })
    ).rejects.toMatchObject({ code: "PAGE_MISSING_WORKSPACE" });
  });

  it("denies when the project id is missing", async () => {
    await expect(
      service().authorize({ workspaceSlug: WORKSPACE_SLUG, projectId: null, pageId: PAGE_ID, cookie: USER.cookie })
    ).rejects.toMatchObject({ code: "PAGE_MISSING_PROJECT" });
  });

  it("denies when no credential is available", async () => {
    await expect(
      service().authorize({ workspaceSlug: WORKSPACE_SLUG, projectId: PROJECT_ID, pageId: PAGE_ID, cookie: null })
    ).rejects.toMatchObject({ code: "PAGE_MISSING_CREDENTIALS" });
  });
});

describe("PageAuthorizationService — service principal path", () => {
  it("refuses a service principal with a readable reason", async () => {
    appApi.setOptions({
      user: USER,
      access: {},
      servicePrincipals: {
        [SERVICE_TOKEN]: { grants: [PROJECT_ID], scopes: ["page:read"] },
      },
    });

    await expect(
      service().authorize({
        workspaceSlug: WORKSPACE_SLUG,
        projectId: PROJECT_ID,
        pageId: PAGE_ID,
        serviceToken: SERVICE_TOKEN,
      })
    ).rejects.toMatchObject({
      code: "PAGE_SERVICE_PRINCIPAL_UNSUPPORTED",
      reason: "Service principals cannot open page documents",
    });

    // the refusal is explicit — no page is fetched for a service principal
    expect(appApi.requests).toHaveLength(0);
  });
});

describe("assertPageDocumentAccess", () => {
  it("does nothing for documents outside the page namespace", async () => {
    appApi.setOptions({ user: USER, access: {} });

    await expect(
      assertPageDocumentAccess({ context: context(), documentName: `issue-events:${PROJECT_ID}` })
    ).resolves.toBeUndefined();

    expect(appApi.requests).toHaveLength(0);
  });

  it("rejects a malformed page document name", async () => {
    await expect(assertPageDocumentAccess({ context: context(), documentName: "not-a-page-id" })).rejects.toMatchObject(
      { code: "PAGE_INVALID_DOCUMENT" }
    );

    expect(appApi.requests).toHaveLength(0);
  });

  it("uses the page id of the document name, not one of the context", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [WORKSPACE_SLUG]: { [PROJECT_ID]: ROLE_MEMBER } } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: ROLE_MEMBER } },
      },
      projects: { [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG } },
      pages: publicPage(),
    });

    // the context carries another project id (the client supplied query
    // parameter) — the document name wins
    await expect(
      assertPageDocumentAccess({
        context: context({ projectId: OTHER_PROJECT_ID }),
        documentName: PAGE_ID,
      })
    ).rejects.toMatchObject({ code: "PAGE_FORBIDDEN" });

    expect(appApi.requests.map((request) => request.url)).toEqual([
      `/api/workspaces/${WORKSPACE_SLUG}/projects/${OTHER_PROJECT_ID}/pages/${PAGE_ID}/?track_visit=false`,
    ]);
  });

  it("allows a member of the project", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [WORKSPACE_SLUG]: { [PROJECT_ID]: ROLE_MEMBER } } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: ROLE_MEMBER } },
      },
      projects: { [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG } },
      pages: publicPage(),
    });

    await expect(assertPageDocumentAccess({ context: context(), documentName: PAGE_ID })).resolves.toBeUndefined();
  });
});
