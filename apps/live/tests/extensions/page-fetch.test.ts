/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import * as Y from "yjs";
import type { FetchPayloadWithContext } from "@/types";
import { FakeAppApi, PAGE_ACCESS_PUBLIC, ROLE_MEMBER } from "../helpers/fake-app-api";

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
const PAGE_ID = "33333333-3333-4333-8333-333333333333";
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

/** The collaborative binary of a page — the body the description endpoint serves. */
const pageBinary = (text: string): Buffer => {
  const document = new Y.Doc();
  const fragment = document.getXmlFragment("default");
  const paragraph = new Y.XmlElement("paragraph");
  fragment.insert(0, [paragraph]);
  paragraph.insert(0, [new Y.XmlText(text)]);
  return Buffer.from(Y.encodeStateAsUpdate(document));
};

const pageFixture = (binary: Buffer) => ({
  [PAGE_ID]: {
    workspaceSlug: WORKSPACE_SLUG,
    projectId: PROJECT_ID,
    access: PAGE_ACCESS_PUBLIC,
    ownedBy: USER.id,
    binary,
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

describe("fetchDocument — page document", () => {
  it("refuses to load the page for a user who is not a member of the project", async () => {
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [WORKSPACE_SLUG]: {} } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: ROLE_MEMBER } },
      },
      projects: { [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG } },
      pages: pageFixture(pageBinary("confidential page body")),
    });

    await expect(callFetch(PAGE_ID)).rejects.toMatchObject({ code: "PAGE_FORBIDDEN" });

    // the body was never fetched
    expect(appApi.requests.map((request) => request.url)).toEqual([
      `/api/workspaces/${WORKSPACE_SLUG}/projects/${PROJECT_ID}/pages/${PAGE_ID}/?track_visit=false`,
    ]);
  });

  it("loads the collaborative binary of the page for a member of the project", async () => {
    const binary = pageBinary("confidential page body");
    appApi.setOptions({
      user: USER,
      access: {
        projectRoles: { [USER.id]: { [WORKSPACE_SLUG]: { [PROJECT_ID]: ROLE_MEMBER } } },
        workspaceRoles: { [USER.id]: { [WORKSPACE_SLUG]: ROLE_MEMBER } },
      },
      projects: { [PROJECT_ID]: { workspaceSlug: WORKSPACE_SLUG } },
      pages: pageFixture(binary),
    });

    const update = await callFetch(PAGE_ID);

    const document = new Y.Doc();
    Y.applyUpdate(document, new Uint8Array(update));
    expect(document.getXmlFragment("default").toString()).toContain("confidential page body");
  });

  it("refuses a malformed page document name", async () => {
    await expect(callFetch("not-a-page-id")).rejects.toMatchObject({ code: "PAGE_INVALID_DOCUMENT" });
  });
});
