/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { Hocuspocus } from "@hocuspocus/server";
import { FakeAppApi } from "../helpers/fake-app-api";
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
const SECRET_PROJECT_ID = "22222222-2222-4222-8222-222222222222";
const USER = { id: "user-1", display_name: "Ada Lovelace", cookie: "session=user-1" };
const OTHER_USER = { id: "user-2", display_name: "Grace Hopper", cookie: "session=user-2" };

const appApi = new FakeAppApi();
let server: Hocuspocus;

const issueEventPayload = (projectId: string) =>
  JSON.stringify({
    event: "issue_changed",
    data: {
      project_id: projectId,
      issue_id: "issue-1",
      type: "issue.activity.created",
      actor_id: "actor-1",
    },
  });

const clientURL = (projectId: string) =>
  `ws://127.0.0.1:${server.address.port}/live/collaboration?documentType=project_page&workspaceSlug=${WORKSPACE_SLUG}&projectId=${projectId}`;

const connect = async (projectId: string, token: string) => {
  const client = await RawHocuspocusClient.connect({
    url: clientURL(projectId),
    documentName: `issue-events:${projectId}`,
    token,
  });
  await client.waitForAuth();
  await client.waitForSync();
  return client;
};

beforeAll(async () => {
  apiState.baseURL = await appApi.start();

  server = new Hocuspocus({
    name: "issue-events-test",
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

describe("issue-events broadcast channel — subscription authorization", () => {
  it("refuses a user who is not a member of the project", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        // the user is a workspace member, but not a member of the project
        projectRoles: { [USER.id]: {} },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: 15 } },
      },
    });

    const client = await connect(PROJECT_ID, JSON.stringify({ id: USER.id, cookie: USER.cookie }));

    expect(client.permissionDenied).toBe(true);
    expect(client.authenticated).toBe(false);
    expect(client.statelessPayloads).toHaveLength(0);

    client.destroy();
  });

  it("refuses a user who is not a member of the project even when the channel is already loaded", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [PROJECT_ID]: 15 } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: 15 } },
      },
    });

    // a project member opens the channel first — the document is now in memory
    const member = await connect(PROJECT_ID, JSON.stringify({ id: USER.id, cookie: USER.cookie }));
    expect(member.authenticated).toBe(true);

    // a second user without project membership joins the same, already loaded channel
    appApi.setOptions({
      user: OTHER_USER,
      access: {
        projectRoles: { [OTHER_USER.id]: {} },
        workspaceRoles: { [OTHER_USER.id]: { [WORKSPACE_SLUG]: 15 } },
      },
    });

    const intruder = await connect(PROJECT_ID, JSON.stringify({ id: OTHER_USER.id, cookie: OTHER_USER.cookie }));

    expect(intruder.permissionDenied).toBe(true);
    expect(intruder.authenticated).toBe(false);

    // the intruder receives no event of the channel
    const document = server.documents.get(`issue-events:${PROJECT_ID}`);
    document?.broadcastStateless(issueEventPayload(PROJECT_ID));
    await intruder.settle(300);
    expect(intruder.statelessPayloads).toHaveLength(0);

    member.destroy();
    intruder.destroy();
  });

  it("lets a project member subscribe and receive the issue events", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [PROJECT_ID]: 15 } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: 15 } },
      },
    });

    const client = await connect(PROJECT_ID, JSON.stringify({ id: USER.id, cookie: USER.cookie }));

    expect(client.permissionDenied).toBe(false);
    expect(client.authenticated).toBe(true);

    const document = server.documents.get(`issue-events:${PROJECT_ID}`);
    expect(document).toBeDefined();
    document?.broadcastStateless(issueEventPayload(PROJECT_ID));

    const payload = await client.waitForStateless();
    expect(payload).not.toBeNull();
    expect(JSON.parse(payload as string)).toEqual({
      event: "issue_changed",
      data: {
        project_id: PROJECT_ID,
        issue_id: "issue-1",
        type: "issue.activity.created",
        actor_id: "actor-1",
      },
    });

    client.destroy();
  });

  it("does not let a forged projectId query parameter buy access to another project", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        // the user is a member of PROJECT_ID only
        projectRoles: { [USER.id]: { [PROJECT_ID]: 15 } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: 15 } },
      },
    });

    // the document name asks for the secret project, the query parameter
    // claims the project the user is a member of
    const client = await RawHocuspocusClient.connect({
      url: `ws://127.0.0.1:${server.address.port}/live/collaboration?documentType=project_page&workspaceSlug=${WORKSPACE_SLUG}&projectId=${PROJECT_ID}`,
      documentName: `issue-events:${SECRET_PROJECT_ID}`,
      token: JSON.stringify({ id: USER.id, cookie: USER.cookie }),
    });
    await client.waitForAuth();

    expect(client.permissionDenied).toBe(true);
    expect(client.authenticated).toBe(false);

    client.destroy();
  });
});
