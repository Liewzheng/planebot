/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import * as Y from "yjs";
import type { FetchPayloadWithContext } from "@/types";
import { FakeAppApi } from "../helpers/fake-app-api";

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

const { fetchDocument } = await import("@/extensions/database");

const WORKSPACE_SLUG = "acme";
const PROJECT_ID = "11111111-1111-4111-8111-111111111111";
const USER = { id: "user-1", display_name: "Ada Lovelace", cookie: "session=user-1" };

const appApi = new FakeAppApi();

const context = () => ({
  projectId: PROJECT_ID,
  cookie: USER.cookie,
  documentType: "project_page" as const,
  workspaceSlug: WORKSPACE_SLUG,
  userId: USER.id,
  serviceToken: null as string | null,
  servicePrincipalId: null as string | null,
});

const callFetch = (documentName: string) =>
  fetchDocument({
    context: context(),
    documentName,
    instance: {} as never,
  } as unknown as FetchPayloadWithContext);

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

describe("fetchDocument — issue-events channel", () => {
  it("refuses to load the channel for a user who is not a member of the project", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: {} },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: 15 } },
      },
    });

    await expect(callFetch(`issue-events:${PROJECT_ID}`)).rejects.toMatchObject({
      code: "ISSUE_EVENTS_FORBIDDEN",
    });
  });

  it("loads an empty document for a member of the project", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [PROJECT_ID]: 15 } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: 15 } },
      },
    });

    const update = await callFetch(`issue-events:${PROJECT_ID}`);

    // the channel carries no persisted content
    const document = new Y.Doc();
    Y.applyUpdate(document, update);
    expect(document.getXmlFragment("default").length).toBe(0);
  });

  it("refuses a malformed issue-events document name", async () => {
    await expect(callFetch("issue-events:not-a-uuid")).rejects.toMatchObject({
      code: "ISSUE_EVENTS_INVALID_DOCUMENT",
    });
  });
});
