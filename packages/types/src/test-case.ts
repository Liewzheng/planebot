/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * The shapes of the test-case feature (STD-SW-003) as the app API hands them
 * out. The field names mirror the API one to one — the server is the
 * contract, the front end never renames a field.
 */

/** The priority of a case. The server refuses anything outside this list. */
export type TTestCasePriority = "P0" | "P1" | "P2" | "P3";

/**
 * The state of a case. A voided case keeps its number and is hidden by the
 * list unless the filter asks for every status.
 */
export type TTestCaseStatus = "active" | "void";

/** A module code registered by the project before a case may use it. */
export interface ITestCaseModule {
  id: string;
  code: string;
  description: string;
  created_at: string;
  updated_at: string;
}

/** One row of the ordered step table of a case. */
export interface ITestCaseStep {
  id: string;
  sequence_id: number;
  action: string;
  expected_result: string;
  created_at: string;
  updated_at: string;
}

/** A link between a case and a work item. */
export interface ITestCaseIssueLink {
  id: string;
  issue_id: string;
  issue_identifier: string | null;
  issue_name: string | null;
  created_at: string;
}

/** The creator of a case — a human or the shadow bot of a service principal. */
export interface ITestCaseCreator {
  id: string;
  first_name: string;
  last_name: string;
  avatar: string | null;
  avatar_url: string | null;
  is_bot: boolean;
  bot_type: string | null;
  display_name: string;
}

/** A case as the API reads it. */
export interface ITestCase {
  id: string;
  number: string;
  module: string;
  module_code: string;
  title: string;
  purpose: string;
  object_overview: string;
  priority: TTestCasePriority | string;
  preconditions: string;
  environment: string;
  status: TTestCaseStatus | string;
  steps: ITestCaseStep[];
  issue_links: ITestCaseIssueLink[];
  created_at: string;
  updated_at: string;
  created_by: ITestCaseCreator | null;
}

/**
 * The step table as the client sends it: the whole table is replaced, the
 * numbers are assigned by the server.
 */
export type TTestCaseStepInput = {
  action: string;
  expected_result: string;
};

/** The header of a case as the client writes it. */
export type TTestCaseWrite = {
  module: string;
  title: string;
  purpose: string;
  object_overview: string;
  priority: string;
  preconditions: string;
  environment: string;
  status?: string;
  steps?: TTestCaseStepInput[];
};

/** The filters the list endpoint understands. */
export type TTestCaseFilters = {
  module?: string;
  priority?: string;
  issue?: string;
  status?: string;
  search?: string;
};

/** One problem of a refused write. */
export interface ITestCaseErrorEntry {
  code: string;
  message: string;
  path?: string;
  field?: string;
  detail?: string;
  step_sequence?: number | null;
  case_number?: string | null;
}

/**
 * The error envelope of the feature. `errors[].message` is the Chinese
 * sentence of the problem — the front end shows it next to the offending
 * field instead of a generic toast.
 */
export interface ITestCaseErrorResponse {
  error: string;
  code: string;
  errors?: ITestCaseErrorEntry[];
  /** The row as the server holds it — carried by a 409 conflict. */
  current?: ITestCase;
}

/**
 * The conclusion of a case inside a batch. It is written once — the server
 * refuses a second write.
 */
export type TTestRunConclusion = "PASS" | "FAIL" | "N/A" | "MANUAL" | "";

/**
 * The state of a batch. `completed` is final: every write on it answers 409
 * and a re-test opens a new batch (STD-SW-003 §6.2 rule 2).
 */
export type TTestRunStatus = "draft" | "in_progress" | "completed";

/**
 * The verdict of one step. It is written once — ✔ and ✘ are deliberate acts
 * that are never swapped; a wrong verdict is corrected by running the case
 * again in a new batch.
 */
export type TTestRunStepVerdict = "pass" | "fail" | "unset";

/**
 * One file attached to a step record (the measured value) or to a case record
 * (the evidence). The file is bound by a foreign key on the server, not by a
 * link parsed out of the Markdown source.
 */
export interface ITestRunAttachment {
  id: string;
  asset_id: string;
  name: string;
  size: number;
  url: string;
}

/** The verdict and the measured value of one step of a batch case. */
export interface ITestRunStepRecord {
  id: string;
  sequence_id: number;
  action_snapshot: string;
  expected_result_snapshot: string;
  conclusion: TTestRunStepVerdict | string;
  actual_value: string;
  attachments: ITestRunAttachment[];
  created_at: string;
  updated_at: string;
}

/**
 * One case inside a batch, with the snapshot of the case header taken when
 * the case was added. A later edit of the case never rewrites the batch.
 */
export interface ITestRunCase {
  id: string;
  test_case: string | null;
  case_number: string;
  case_module_code: string;
  case_title: string;
  case_purpose: string;
  case_object_overview: string;
  case_priority: string;
  case_preconditions: string;
  case_environment: string;
  conclusion: TTestRunConclusion;
  evidence: string;
  remarks: string;
  actual_result_summary: string;
  na_reason: string;
  na_criteria_source: string;
  manual_executor: string;
  manual_condition: string;
  steps: ITestRunStepRecord[];
  attachments: ITestRunAttachment[];
  created_at: string;
  updated_at: string;
}

/** A batch (a test run): one continuous execution against one build. */
export interface ITestRun {
  id: string;
  number: string;
  run_date: string;
  version_number: string;
  build_number: string;
  commit_hash: string;
  environment: string;
  executed_by: string | null;
  executed_by_name: string;
  status: TTestRunStatus | string;
  /**
   * The batch this one re-tests. A soft-deleted original keeps its link and
   * its number, so `null` means "no original" — never "the original is gone".
   */
  retest_of: string | null;
  retest_of_number: string | null;
  cases: ITestRunCase[];
  created_at: string;
  updated_at: string;
  created_by: ITestCaseCreator | null;
}

/**
 * The header of a batch as the client writes it. The number is assigned by the
 * server; a batch is completed through `finalize`, never by writing the status.
 */
export type TTestRunWrite = {
  run_date: string;
  version_number: string;
  build_number: string;
  commit_hash: string;
  environment: string;
  executed_by?: string | null;
  status?: string;
  retest_of?: string | null;
};

/** The outcome of one case inside a batch, as the client writes it. */
export type TTestRunCaseWrite = {
  conclusion?: string;
  evidence?: string;
  remarks?: string;
  actual_result_summary?: string;
  na_reason?: string;
  na_criteria_source?: string;
  manual_executor?: string;
  manual_condition?: string;
};

/** The verdict and the measured value of one step, as the client writes them. */
export type TTestRunStepWrite = {
  conclusion?: string;
  actual_value?: string;
};

/** One deviation of the batch summary: a case number and one sentence. */
export interface ITestRunDeviation {
  case_number: string;
  summary: string;
}

/**
 * The summary of a batch (STD-SW-003 §6.2 rule 3) as the server computes it.
 *
 * The counts are **not** a partition of `total`: a case that failed on one
 * step and never ran another is counted in both `fail` and `manual`, exactly
 * as the REC-SW-001 sample prints it. The client never re-counts — it renders
 * this object.
 */
export interface ITestRunSummaryReport {
  total: number;
  pass: number;
  fail: number;
  na: number;
  manual: number;
  deviations: ITestRunDeviation[];
}

/** One case inside a batch, as far as the list of the cases needs it. */
export interface ITestRunCaseSummary {
  id: string;
  test_case: string;
  case_number: string;
  case_module_code: string;
  case_title: string;
  conclusion: TTestRunConclusion;
}

/**
 * A batch, as far as the list of the cases needs it: the case list derives
 * the *last execution result* of a case from the batches that carried it.
 *
 * The list endpoint answers with the whole batch (the same serializer the
 * detail read uses), so the header fields the batch list prints are declared
 * here as well: the tested version, the environment, the executor and the
 * re-test link — including `retest_of_number`, which lets the list print
 * 「补测：RUN-…」 without a second call.
 */
export interface ITestRunSummary {
  id: string;
  number: string;
  run_date: string;
  status: string;
  version_number: string;
  build_number: string;
  commit_hash: string;
  environment: string;
  executed_by_name: string;
  retest_of: string | null;
  retest_of_number: string | null;
  cases: ITestRunCaseSummary[];
}
