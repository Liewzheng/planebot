/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock the UserService (legacy cookie path) before the module is
// imported.  The SP path doesn't go through it, so the mock only
// needs to satisfy the JSON-token branch.
const userServiceMock = vi.hoisted(() => ({
  currentUser: vi.fn(),
}));

vi.mock("@/services/user.service", () => ({
  UserService: vi.fn(function () {
    return userServiceMock;
  }),
}));

// Mock the PrincipalService (SP path).  Each test sets up the
// expected resolveServiceToken behaviour.
const principalServiceMock = vi.hoisted(() => ({
  resolveServiceToken: vi.fn(),
}));

vi.mock("@/services/principal.service", () => ({
  isServiceToken: (token: string | null | undefined) => typeof token === "string" && token.startsWith("plane_svc_"),
  PrincipalService: vi.fn(function () {
    return principalServiceMock;
  }),
}));

// Module under test.  Imported lazily so each test can prime the
// mocks first.
async function loadAuthModule() {
  return await import("@/lib/auth");
}

const baseRequestParameters = (overrides: Record<string, string> = {}) => {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries({
    documentType: "project_page",
    projectId: "p-1",
    workspaceSlug: "acme",
    ...overrides,
  })) {
    params.set(k, v);
  }
  return params;
};

const emptyContext = () => ({
  projectId: null as string | null,
  cookie: "",
  documentType: "project_page" as const,
  workspaceSlug: null as string | null,
  userId: "",
  serviceToken: null as string | null,
  servicePrincipalId: null as string | null,
});

beforeEach(() => {
  userServiceMock.currentUser.mockReset();
  principalServiceMock.resolveServiceToken.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("onAuthenticate — legacy JSON token path (human)", () => {
  it("authenticates a human via cookie + user id and returns the user", async () => {
    userServiceMock.currentUser.mockResolvedValue({
      id: "u-1",
      display_name: "Ada Lovelace",
    });

    const { onAuthenticate } = await loadAuthModule();
    const context = emptyContext();
    const token = JSON.stringify({ id: "u-1", cookie: "session=abc" });

    const result = await onAuthenticate({
      requestHeaders: {},
      requestParameters: baseRequestParameters(),
      context,
      token,
    });

    expect(result).toEqual({
      user: { id: "u-1", name: "Ada Lovelace" },
    });
    expect(context.cookie).toBe("session=abc");
    expect(context.userId).toBe("u-1");
    expect(context.serviceToken).toBeNull();
    expect(context.servicePrincipalId).toBeNull();
  });

  it("falls back to the request-header cookie when the JSON blob has none", async () => {
    userServiceMock.currentUser.mockResolvedValue({
      id: "u-2",
      display_name: "Grace",
    });

    const { onAuthenticate } = await loadAuthModule();
    const context = emptyContext();
    const token = JSON.stringify({ id: "u-2" });

    const result = await onAuthenticate({
      requestHeaders: { cookie: "session=fallback" },
      requestParameters: baseRequestParameters(),
      context,
      token,
    });

    expect(context.cookie).toBe("session=fallback");
    expect(result).toEqual({ user: { id: "u-2", name: "Grace" } });
  });

  it("rejects when both the JSON token and the request headers lack credentials", async () => {
    const { onAuthenticate } = await loadAuthModule();
    const context = emptyContext();

    await expect(
      onAuthenticate({
        requestHeaders: {},
        requestParameters: baseRequestParameters(),
        context,
        token: "not-a-json-blob",
      })
    ).rejects.toMatchObject({ code: "AUTH_MISSING_CREDENTIALS" });
  });
});

describe("onAuthenticate — service-principal path (M10)", () => {
  it("verifies a plane_svc_ token against the dispatch endpoint and stores the SP identity", async () => {
    principalServiceMock.resolveServiceToken.mockResolvedValue({
      kind: "service",
      id: "sp-42",
      workspace_role: null,
    });

    const { onAuthenticate } = await loadAuthModule();
    const context = emptyContext();
    const token = "plane_svc_abcdef0123456789";

    const result = await onAuthenticate({
      requestHeaders: {},
      requestParameters: baseRequestParameters({ workspaceSlug: "acme" }),
      context,
      token,
    });

    expect(principalServiceMock.resolveServiceToken).toHaveBeenCalledWith("acme", token);
    // The userId field stays empty (the SP identity is on
    // servicePrincipalId); the page service reads the SP token
    // from context.serviceToken to forward X-Api-Key.
    expect(context.serviceToken).toBe(token);
    expect(context.servicePrincipalId).toBe("sp-42");
    expect(context.cookie).toBe("");
    expect(result.user.id).toBe("sp-42");
  });

  it("rejects the SP path when the workspace slug is missing", async () => {
    const { onAuthenticate } = await loadAuthModule();
    const context = emptyContext();
    context.workspaceSlug = null;

    await expect(
      onAuthenticate({
        requestHeaders: {},
        requestParameters: baseRequestParameters({ workspaceSlug: "" }),
        context,
        token: "plane_svc_xyz",
      })
    ).rejects.toMatchObject({ code: "AUTH_MISSING_WORKSPACE" });

    expect(principalServiceMock.resolveServiceToken).not.toHaveBeenCalled();
  });

  it("rejects the SP path when the dispatch endpoint refuses the token", async () => {
    principalServiceMock.resolveServiceToken.mockRejectedValue(
      Object.assign(new Error("403 Forbidden"), { code: "AUTH_FAILED" })
    );

    const { onAuthenticate } = await loadAuthModule();
    const context = emptyContext();

    await expect(
      onAuthenticate({
        requestHeaders: {},
        requestParameters: baseRequestParameters({ workspaceSlug: "acme" }),
        context,
        token: "plane_svc_revoked",
      })
    ).rejects.toBeDefined();

    // No SP state leaked into the context after a failed verify.
    expect(context.serviceToken).toBeNull();
    expect(context.servicePrincipalId).toBeNull();
  });

  it("rejects the SP path when the dispatch response has no service principal block", async () => {
    principalServiceMock.resolveServiceToken.mockRejectedValue(
      Object.assign(new Error("Service principal identity not found"), {
        code: "AUTH_SP_RESOLVE_FAILED",
      })
    );

    const { onAuthenticate } = await loadAuthModule();
    const context = emptyContext();

    await expect(
      onAuthenticate({
        requestHeaders: {},
        requestParameters: baseRequestParameters({ workspaceSlug: "acme" }),
        context,
        token: "plane_svc_malformed",
      })
    ).rejects.toBeDefined();
  });
});

describe("isServiceToken", () => {
  it("identifies the plane_svc_ prefix", async () => {
    const { isServiceToken } = await loadAuthModule();
    expect(isServiceToken("plane_svc_xxx")).toBe(true);
    expect(isServiceToken("plane_api_yyy")).toBe(false);
    expect(isServiceToken("")).toBe(false);
    expect(isServiceToken(null)).toBe(false);
    expect(isServiceToken(undefined)).toBe(false);
  });
});
