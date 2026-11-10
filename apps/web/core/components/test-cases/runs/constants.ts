/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { TTestRunConclusion, TTestRunStatus } from "@plane/types";

/** The cache key of one batch, with its case records and its steps. */
export const TEST_RUN_DETAIL = (workspaceSlug: string, projectId: string, testRunId: string) =>
  `TEST_RUN_DETAIL_${workspaceSlug}_${projectId}_${testRunId}`;

/** The cache key of the server-computed summary of one batch. */
export const TEST_RUN_SUMMARY_REPORT = (workspaceSlug: string, projectId: string, testRunId: string) =>
  `TEST_RUN_SUMMARY_${workspaceSlug}_${projectId}_${testRunId}`;

/** The cache key of the cases linked to one work item (the issue block). */
export const ISSUE_LINKED_TEST_CASES = (workspaceSlug: string, projectId: string, issueId: string) =>
  `ISSUE_LINKED_TEST_CASES_${workspaceSlug}_${projectId}_${issueId}`;

/** The cache key of the active cases offered when a batch is filled. */
export const TEST_RUN_ADDABLE_CASES = (workspaceSlug: string, projectId: string) =>
  `TEST_RUN_ADDABLE_CASES_${workspaceSlug}_${projectId}`;

/**
 * The pause after which a change of the execution form is written. The
 * measured value is typed, not pasted as a whole, so the write has to wait
 * for a pause — and the server judges every write, so a half-typed sentence
 * would be refused.
 */
export const TEST_RUN_AUTOSAVE_DEBOUNCE_MS = 800;

/** The three states of a batch, in the order the standard names them. */
export const TEST_RUN_STATUSES: TTestRunStatus[] = ["draft", "in_progress", "completed"];

/** The four conclusions of STD-SW-003 §6.1 — nothing else is a conclusion. */
export const TEST_RUN_CONCLUSIONS: TTestRunConclusion[] = ["PASS", "FAIL", "N/A", "MANUAL"];

/**
 * The file types the upload endpoint accepts. It is an allow-list on the
 * server (jpeg/png/webp/jpg/gif) — anything else is refused before it is
 * stored, so the picker offers exactly those.
 */
export const TEST_RUN_ATTACHMENT_MIME_TYPES = ["image/jpeg", "image/png", "image/webp", "image/jpg", "image/gif"];

/** The `accept` attribute of the file pickers. */
export const TEST_RUN_ATTACHMENT_ACCEPT = TEST_RUN_ATTACHMENT_MIME_TYPES.join(",");

/** True when the browser reports a file type the endpoint accepts. */
export const isAcceptableAttachment = (file: File): boolean => TEST_RUN_ATTACHMENT_MIME_TYPES.includes(file.type);
