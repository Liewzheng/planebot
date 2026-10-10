/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock the workspace packages so the test can import the live server module
// without the rest of the monorepo being built.
const workspace = vi.hoisted(() => ({
  // Returned by `getAllDocumentFormatsFromDocumentEditorBinaryData`. Each
  // test sets `contentHTML` to drive the four store-path guards.
  conversion: vi.fn(),
}));

vi.mock("@plane/editor", () => ({
  getAllDocumentFormatsFromDocumentEditorBinaryData: workspace.conversion,
  getBinaryDataFromDocumentEditorHTMLString: vi.fn(),
}));

vi.mock("@plane/logger", () => ({
  logger: { warn: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("@plane/types", () => ({}));

const pageServiceMock = vi.hoisted(() => ({
  updateDescriptionBinary: vi.fn().mockResolvedValue(undefined),
  fetchDescriptionBinary: vi.fn(),
  fetchDetails: vi.fn(),
}));

vi.mock("@/services/page/handler", () => ({
  getPageService: vi.fn(() => pageServiceMock),
}));

// `@/env` validates required env vars at module load. Mock it so the
// test can import the database module without a full `.env` in place.
vi.mock("@/env", () => ({
  env: {
    APP_VERSION: "1.0.0",
    HOSTNAME: "test",
    PORT: "3000",
    API_BASE_URL: "http://localhost:8000",
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

const forceCloseMock = vi.hoisted(() => vi.fn().mockResolvedValue(undefined));

vi.mock("@/extensions/force-close-handler", () => ({
  forceCloseDocumentAcrossServers: forceCloseMock,
}));

// Import the module under test AFTER every mock is registered. The static
// `await import` lets vitest hoist the mocks above the dynamic import.
const { storeDocument } = await import("@/extensions/database");
const { clearContentReplacement, recordContentReplacement, recordDocumentContent } =
  await import("@/utils/content-replacement");

const DOC = "store-path-doc";
const OTHER_DOC = "other-store-path-doc";

const paragraph = (text: string) => `<p>${text}</p>`;

// A long body of 130 unique paragraphs, each > 20 chars. Passes
// `detectDocumentDuplication` (unique ratio = 1.0) and the rolling-hash
// check (no block of ≥600 chars is repeated verbatim).
const uniqueLongBody = (start = 0): string =>
  Array.from(
    { length: 130 },
    (_, index) => `第${start + index}段内容，记录一次实测的数据与结论，详尽描述这一段的内容。`
  )
    .map(paragraph)
    .join("");

// A body that triggers `detectDocumentDuplication`: 130 unique lines
// concatenated with itself → 260 lines, 130 unique, ratio 0.5 < 0.6.
const duplicatedLongBody = (): string => {
  const base = uniqueLongBody();
  return base + base;
};

// A body that triggers `looksLikeDuplicatedGrowth` only: 80 lines (below
// the 120-line gate of `detectDocumentDuplication`) plus 30 lines that
// each repeat one of the 80, so growth ratio = 110/80 = 1.375 and the
// unique-line ratio in the candidate = 80/110 ≈ 0.727. With the
// baseline primed, the growth guard fires while the other three stay
// quiet.
const growthCandidateBody = (): string => {
  const baseline = Array.from(
    { length: 80 },
    (_, i) => `第${i}段内容，记录一次实测的数据与结论，详尽描述这一段的内容。`
  )
    .map(paragraph)
    .join("");
  const extraRepeats = Array.from(
    { length: 30 },
    (_, i) => `第${i}段内容，记录一次实测的数据与结论，详尽描述这一段的内容。`
  )
    .map(paragraph)
    .join("");
  return baseline + extraRepeats;
};

// A body that triggers `duplicatedBlockLength` only: a single 700-char
// block appears verbatim twice. Lines (after the >20-char filter) are
// well below 120, no growth baseline is primed, no replacement floor
// is armed — only the rolling-hash check has a match.
const blockRepeatedBody = (): string => {
  const block = "x".repeat(700);
  return paragraph(block) + paragraph(block);
};

const context = () => ({
  projectId: "p-1",
  cookie: "",
  documentType: "project_page" as const,
  workspaceSlug: "acme",
  userId: "u-1",
  serviceToken: null,
  servicePrincipalId: null,
});

// Stub Hocuspocus instance. `storeDocument` only forwards it to the
// force-close helper, so an empty object is fine.
const instance = {} as never;

const callStore = (pageId: string) =>
  storeDocument({
    context: context(),
    state: new Uint8Array([1]) as never,
    documentName: pageId,
    instance,
  });

const setupConversion = (contentHTML: string) =>
  workspace.conversion.mockReturnValue({
    contentBinaryEncoded: new Uint8Array([0]),
    contentHTML,
    contentJSON: "{}",
  });

beforeEach(() => {
  workspace.conversion.mockReset();
  pageServiceMock.updateDescriptionBinary.mockClear();
  forceCloseMock.mockClear();
});

afterEach(() => {
  clearContentReplacement(DOC);
  clearContentReplacement(OTHER_DOC);
  recordDocumentContent(DOC, "");
  recordDocumentContent(OTHER_DOC, "");
});

describe("storeDocument — guard chain", () => {
  it("persists a clean body and primes the duplicated-growth baseline", async () => {
    const clean = uniqueLongBody();
    setupConversion(clean);

    await callStore(DOC);

    expect(pageServiceMock.updateDescriptionBinary).toHaveBeenCalledTimes(1);
    expect(forceCloseMock).not.toHaveBeenCalled();
  });

  it("detectDocumentDuplication forces a close and refuses the store", async () => {
    const body = duplicatedLongBody();
    setupConversion(body);

    await callStore(DOC);

    expect(pageServiceMock.updateDescriptionBinary).not.toHaveBeenCalled();
    // The line-only guard keeps the historical force-close contract —
    // its job is to drop a ballooned document from memory so the next
    // load reads the clean DB copy.
    expect(forceCloseMock).toHaveBeenCalledTimes(1);
  });

  it("getContentReplacementFloor refuses below the armed floor and forces a close", async () => {
    // Arm the floor with the externally written length; the floor is
    // deliberately loose (70%) so a legitimate edit that drops more
    // than ~30% of the page within the 10-min window is treated as a
    // stale-client revert and force-closed.
    recordContentReplacement(DOC, 6867);

    const shrunkHTML = paragraph("x".repeat(3000));
    setupConversion(shrunkHTML);

    await callStore(DOC);

    expect(pageServiceMock.updateDescriptionBinary).not.toHaveBeenCalled();
    expect(forceCloseMock).toHaveBeenCalledTimes(1);
  });

  it("looksLikeDuplicatedGrowth refuses WITHOUT forcing a close (false-positive guard)", async () => {
    // The 24-h memory on this guard means it can fire on legitimate
    // repetitive content (a large pasted table, templated rows). The
    // store is refused (the DB stays authoritative) but the client's
    // in-memory edit survives — no force-close, no reload loop, no
    // lost author edit.
    const baseline = growthCandidateBody().slice(0, growthCandidateBody().length / 2);
    recordDocumentContent(DOC, baseline);

    const merged = growthCandidateBody();
    setupConversion(merged);

    await callStore(DOC);

    expect(pageServiceMock.updateDescriptionBinary).not.toHaveBeenCalled();
    expect(forceCloseMock).not.toHaveBeenCalled();
  });

  it("duplicatedBlockLength refuses WITHOUT forcing a close (false-positive guard)", async () => {
    // The rolling-hash block detector can fire on templated content
    // that legitimately repeats a 600-char block (recurring rows,
    // boilerplate). Same refusal-without-force-close reasoning as the
    // growth check.
    const body = blockRepeatedBody();
    setupConversion(body);

    await callStore(DOC);

    expect(pageServiceMock.updateDescriptionBinary).not.toHaveBeenCalled();
    expect(forceCloseMock).not.toHaveBeenCalled();
  });

  it("guards do not bleed across documents", async () => {
    // The floor is armed for DOC only; OTHER_DOC must not see it.
    recordContentReplacement(DOC, 6867);

    const clean = uniqueLongBody(500);
    setupConversion(clean);

    await callStore(OTHER_DOC);

    expect(pageServiceMock.updateDescriptionBinary).toHaveBeenCalledTimes(1);
    expect(forceCloseMock).not.toHaveBeenCalled();
  });
});
