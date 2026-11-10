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
 */
export interface ITestRunSummary {
  id: string;
  number: string;
  run_date: string;
  status: string;
  cases: ITestRunCaseSummary[];
}
