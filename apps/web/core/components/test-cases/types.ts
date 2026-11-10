/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { ITestCase } from "@plane/types";

/**
 * One step of the table as the editor holds it. The key is local — the
 * server assigns the numbers of the steps, and a save replaces the whole
 * table, so the ids of the steps cannot be used to address them.
 */
export type TTestCaseDraftStep = {
  key: string;
  action: string;
  expected_result: string;
};

/**
 * The eight items of the case header plus the step table — the whole
 * record as the editor holds it. The editor is the source of truth while
 * the record is edited; the server only ever sees a complete draft.
 */
export type TTestCaseDraft = {
  module: string;
  title: string;
  purpose: string;
  object_overview: string;
  priority: string;
  preconditions: string;
  environment: string;
  status: string;
  steps: TTestCaseDraftStep[];
};

/** The reasons of a refused write, addressed by the field they belong to. */
export type TTestCaseFieldErrors = Record<string, string[]>;

/** The reasons of a refused step table, addressed by the step and the field. */
export type TTestCaseStepErrors = Record<number, Record<string, string>>;

let stepKeyCounter = 0;

/** A key that is unique inside the browser session. */
export const newStepKey = (): string => {
  stepKeyCounter += 1;
  return `step-${stepKeyCounter}-${Math.random().toString(36).slice(2, 8)}`;
};

/** The draft of a case as the server holds it. */
export const draftFromCase = (testCase: ITestCase): TTestCaseDraft => ({
  module: testCase.module ?? "",
  title: testCase.title ?? "",
  purpose: testCase.purpose ?? "",
  object_overview: testCase.object_overview ?? "",
  priority: testCase.priority ?? "",
  preconditions: testCase.preconditions ?? "",
  environment: testCase.environment ?? "",
  status: testCase.status ?? "active",
  steps: (testCase.steps ?? []).map((step) => ({
    key: newStepKey(),
    action: step.action ?? "",
    expected_result: step.expected_result ?? "",
  })),
});

/** The comparison key of a draft — it tells whether the record changed. */
export const draftKey = (draft: TTestCaseDraft): string =>
  JSON.stringify([
    draft.module,
    draft.title,
    draft.purpose,
    draft.object_overview,
    draft.priority,
    draft.preconditions,
    draft.environment,
    draft.status,
    draft.steps.map((step) => [step.action, step.expected_result]),
  ]);

/**
 * The mandatory items of the case header. The server refuses a case that
 * does not carry them — the editor marks them before the write goes out.
 */
export const TEST_CASE_MANDATORY_FIELDS = [
  "title",
  "purpose",
  "object_overview",
  "preconditions",
  "environment",
  "priority",
] as const;

/** True when the draft carries every mandatory item of the header. */
export const isHeaderComplete = (draft: TTestCaseDraft): boolean =>
  Boolean(draft.module) && TEST_CASE_MANDATORY_FIELDS.every((field) => String(draft[field] ?? "").trim().length > 0);

/** True when the step table carries at least one complete step. */
export const isStepTableComplete = (draft: TTestCaseDraft): boolean =>
  draft.steps.length > 0 &&
  draft.steps.every((step) => step.action.trim().length > 0 && step.expected_result.trim().length > 0);
