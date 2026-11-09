/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { Hocuspocus } from "@hocuspocus/server";
import * as Y from "yjs";
import {
  FakeAppApi,
  PAGE_ACCESS_PRIVATE,
  PAGE_ACCESS_PUBLIC,
  ROLE_ADMIN,
  ROLE_GUEST,
  ROLE_MEMBER,
} from "../helpers/fake-app-api";
import { RawHocuspocusClient } from "../helpers/hocuspocus-client";

// The live server reads the app API location from the environment when it
// builds a service. Point it at the fake app API once that one is up.
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
const { Database } = await import("@/extensions/database");

const WORKSPACE_SLUG = "acme";
const PROJECT_ID = "11111111-1111-4111-8111-111111111111";
const OTHER_PROJECT_ID = "22222222-2222-4222-8222-222222222222";
const PAGE_ID = "33333333-3333-4333-8333-333333333333";
const OTHER_PAGE_ID = "44444444-4444-4444-8444-444444444444";
const SECRET_BODY = "confidential page body of the product spec";
const USER = { id: "user-1", display_name: "Ada Lovelace", cookie: "session=user-1" };
const OTHER_USER = { id: "user-2", display_name: "Grace Hopper", cookie: "session=user-2" };
const SERVICE_TOKEN = "plane_svc_0123456789abcdef";

const appApi = new FakeAppApi();
let server: Hocuspocus;

/**
 * The collaborative binary of a page — the body the page endpoint serves.
 * The body lives in the "default" fragment of the document (the title is a
 * second fragment).
 */
const pageBinary = (text: string): Buffer => {
  const document = new Y.Doc();
  const fragment = document.getXmlFragment("default");
  const paragraph = new Y.XmlElement("paragraph");
  fragment.insert(0, [paragraph]);
  paragraph.insert(0, [new Y.XmlText(text)]);
  return Buffer.from(Y.encodeStateAsUpdate(document));
};

const clientURL = (projectId: string, workspaceSlug: string = WORKSPACE_SLUG) =>
  `ws://127.0.0.1:${server.address.port}/live/collaboration?documentType=project_page&workspaceSlug=${workspaceSlug}&projectId=${projectId}`;

const connect = async (pageId: string, token: string, projectId: string = PROJECT_ID) => {
  const client = await RawHocuspocusClient.connect({
    url: clientURL(projectId),
    documentName: pageId,
    token,
  });
  await client.waitForAuth();
  await client.waitForSync();
  return client;
};

const memberToken = (user: { id: string; cookie: string }) => JSON.stringify({ id: user.id, cookie: user.cookie });

beforeAll(async () => {
  apiState.baseURL = await appApi.start();

  server = new Hocuspocus({
    name: "page-channel-test",
    onAuthenticate,
    quiet: true,
    extensions: [new Database()],
  });
  await server.listen(0);
});

afterAll(async () => {
  await server?.destroy();
  await appApi.stop();
});

beforeEach(() => {
  appApi.requests.length = 0;
  loggerMock.warn.mockClear();
  loggerMock.error.mockClear();
});

describe("page channel — subscription authorization", () => {
  it("refuses a user who is not a member of the project", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        // the user is a workspace member, but not a member of the project
        projectRoles: { [USER.id]: { [WORKSPACE_SLUG]: {} } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: ROLE_MEMBER } },
      },
      projects: { [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG } },
      pages: {
        [PAGE_ID]: {
          workspaceSlug: WORKSPACE_SLUG,
          projectId: PROJECT_ID,
          access: PAGE_ACCESS_PUBLIC,
          ownedBy: USER.id,
          binary: pageBinary(SECRET_BODY),
        },
      },
    });

    const client = await connect(PAGE_ID, memberToken(USER));

    expect(client.permissionDenied).toBe(true);
    expect(client.authenticated).toBe(false);
    await client.settle(300);
    expect(client.documentBody()).not.toContain(SECRET_BODY);

    client.destroy();
  });

  it("refuses a user who is not a member of the project even when the page is already loaded", async () => {
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
          access: PAGE_ACCESS_PUBLIC,
          ownedBy: USER.id,
          binary: pageBinary(SECRET_BODY),
        },
      },
    });

    // a project member opens the page first — the document is now in memory
    const member = await connect(PAGE_ID, memberToken(USER));
    expect(member.authenticated).toBe(true);
    await member.waitForSync();
    expect(member.documentBody()).toContain(SECRET_BODY);

    // a second user without project membership joins the same, already loaded page
    appApi.setOptions({
      user: OTHER_USER,
      access: {
        projectRoles: { [OTHER_USER.id]: { [WORKSPACE_SLUG]: {} } },
        workspaceRoles: { [OTHER_USER.id]: { [WORKSPACE_SLUG]: ROLE_MEMBER } },
      },
      projects: { [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG } },
      pages: {
        [PAGE_ID]: {
          workspaceSlug: WORKSPACE_SLUG,
          projectId: PROJECT_ID,
          access: PAGE_ACCESS_PUBLIC,
          ownedBy: USER.id,
          binary: pageBinary(SECRET_BODY),
        },
      },
    });

    const intruder = await connect(PAGE_ID, memberToken(OTHER_USER));

    // the intruder receives no part of the page body
    await intruder.settle(300);
    expect(intruder.documentBody()).not.toContain(SECRET_BODY);
    expect(intruder.documentBody()).toBe("");

    expect(intruder.permissionDenied).toBe(true);
    expect(intruder.authenticated).toBe(false);

    member.destroy();
    intruder.destroy();
  });

  it("lets a project member open the page and receive the body", async () => {
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
          access: PAGE_ACCESS_PUBLIC,
          ownedBy: USER.id,
          binary: pageBinary(SECRET_BODY),
        },
      },
    });

    const client = await connect(PAGE_ID, memberToken(USER));

    expect(client.permissionDenied).toBe(false);
    expect(client.authenticated).toBe(true);
    await client.waitForSync();
    expect(client.documentBody()).toContain(SECRET_BODY);

    client.destroy();
  });

  it("lets the owner of a private page open it", async () => {
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
          binary: pageBinary(SECRET_BODY),
        },
      },
    });

    const client = await connect(PAGE_ID, memberToken(USER));

    expect(client.permissionDenied).toBe(false);
    expect(client.authenticated).toBe(true);

    client.destroy();
  });

  it("refuses a member of the project for a private page they do not own", async () => {
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
          binary: pageBinary(SECRET_BODY),
        },
      },
    });

    const client = await connect(PAGE_ID, memberToken(OTHER_USER));

    expect(client.permissionDenied).toBe(true);
    expect(client.authenticated).toBe(false);

    client.destroy();
  });

  it("refuses a guest that may not view all features of the project", async () => {
    appApi.setOptions({
      user: OTHER_USER,
      access: {
        projectRoles: { [OTHER_USER.id]: { [WORKSPACE_SLUG]: { [PROJECT_ID]: ROLE_GUEST } } },
        workspaceRoles: { [OTHER_USER.id]: { [WORKSPACE_SLUG]: ROLE_MEMBER } },
      },
      projects: { [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG, guestViewAllFeatures: false } },
      pages: {
        [PAGE_ID]: {
          workspaceSlug: WORKSPACE_SLUG,
          projectId: PROJECT_ID,
          access: PAGE_ACCESS_PUBLIC,
          ownedBy: USER.id,
          binary: pageBinary(SECRET_BODY),
        },
      },
    });

    const client = await connect(PAGE_ID, memberToken(OTHER_USER));

    expect(client.permissionDenied).toBe(true);
    expect(client.authenticated).toBe(false);

    client.destroy();
  });

  it("refuses a workspace admin that is not a member of the project", async () => {
    // the page endpoints ask for a project membership row — unlike the issue
    // endpoints there is no workspace admin bypass for pages
    appApi.setOptions({
      user: OTHER_USER,
      access: {
        projectRoles: { [OTHER_USER.id]: { [WORKSPACE_SLUG]: {} } },
        workspaceRoles: { [OTHER_USER.id]: { [WORKSPACE_SLUG]: ROLE_ADMIN } },
      },
      projects: { [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG } },
      pages: {
        [PAGE_ID]: {
          workspaceSlug: WORKSPACE_SLUG,
          projectId: PROJECT_ID,
          access: PAGE_ACCESS_PUBLIC,
          ownedBy: USER.id,
          binary: pageBinary(SECRET_BODY),
        },
      },
    });

    const client = await connect(PAGE_ID, memberToken(OTHER_USER));

    expect(client.permissionDenied).toBe(true);
    expect(client.authenticated).toBe(false);

    client.destroy();
  });

  it("does not let a forged projectId query parameter buy access to another page", async () => {
    appApi.setOptions({
      user: OTHER_USER,
      access: {
        // the caller is a member of OTHER_PROJECT_ID only
        projectRoles: { [OTHER_USER.id]: { [WORKSPACE_SLUG]: { [OTHER_PROJECT_ID]: ROLE_MEMBER } } },
        workspaceRoles: { [OTHER_USER.id]: { [WORKSPACE_SLUG]: ROLE_MEMBER } },
      },
      projects: {
        [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG },
        [OTHER_PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG },
      },
      pages: {
        [PAGE_ID]: {
          workspaceSlug: WORKSPACE_SLUG,
          projectId: PROJECT_ID,
          access: PAGE_ACCESS_PUBLIC,
          ownedBy: USER.id,
          binary: pageBinary(SECRET_BODY),
        },
        [OTHER_PAGE_ID]: {
          workspaceSlug: WORKSPACE_SLUG,
          projectId: OTHER_PROJECT_ID,
          access: PAGE_ACCESS_PUBLIC,
          ownedBy: OTHER_USER.id,
          binary: pageBinary("another page"),
        },
      },
    });

    // the document name asks for the page of PROJECT_ID, the query parameter
    // claims the project the caller is a member of
    const client = await connect(PAGE_ID, memberToken(OTHER_USER), OTHER_PROJECT_ID);

    expect(client.permissionDenied).toBe(true);
    expect(client.authenticated).toBe(false);

    client.destroy();
  });

  it("refuses a service principal with a readable reason", async () => {
    appApi.setOptions({
      user: USER,
      access: {},
      servicePrincipals: {
        [SERVICE_TOKEN]: { grants: [PROJECT_ID], scopes: ["page:read"] },
      },
      projects: { [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG } },
      pages: {
        [PAGE_ID]: {
          workspaceSlug: WORKSPACE_SLUG,
          projectId: PROJECT_ID,
          access: PAGE_ACCESS_PUBLIC,
          ownedBy: USER.id,
          binary: pageBinary(SECRET_BODY),
        },
      },
    });

    const client = await connect(PAGE_ID, SERVICE_TOKEN);

    expect(client.permissionDenied).toBe(true);
    expect(client.authenticated).toBe(false);
    expect(client.permissionDeniedReason).toContain("Service principals cannot open page documents");

    client.destroy();
  });

  it("refuses a blank document name without asking the app API for a page", async () => {
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
          access: PAGE_ACCESS_PUBLIC,
          ownedBy: USER.id,
          binary: pageBinary(SECRET_BODY),
        },
      },
    });

    // a blank name is not "outside the page namespace" — it is an invalid
    // document, and the gate has to fail closed on it
    const client = await connect("", memberToken(USER));

    // the session lookup is the only app API call — the gate must not fall
    // through to a page lookup with an empty page id
    expect(appApi.requests.map((request) => request.url)).toEqual(["/api/users/me/"]);

    expect(client.permissionDenied).toBe(true);
    expect(client.authenticated).toBe(false);
    await client.settle(300);
    expect(client.documentBody()).not.toContain(SECRET_BODY);

    client.destroy();
  });

  it("refuses a malformed document name without asking the app API for a page", async () => {
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
          access: PAGE_ACCESS_PUBLIC,
          ownedBy: USER.id,
          binary: pageBinary(SECRET_BODY),
        },
      },
    });

    const client = await connect("not-a-page-id", memberToken(USER));

    expect(client.permissionDenied).toBe(true);
    expect(client.authenticated).toBe(false);
    await client.settle(300);
    expect(client.documentBody()).not.toContain(SECRET_BODY);

    expect(appApi.requests.map((request) => request.url)).toEqual(["/api/users/me/"]);

    client.destroy();
  });
});
