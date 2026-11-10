/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// types
import { API_BASE_URL } from "@plane/constants";
import type {
  ISearchIssueResponse,
  ITestCase,
  ITestCaseIssueLink,
  ITestCaseModule,
  ITestRun,
  ITestRunCase,
  ITestRunStepRecord,
  ITestRunSummary,
  ITestRunSummaryReport,
  TTestCaseFilters,
  TTestCaseWrite,
  TPaginatedResponse,
  TTestRunCaseWrite,
  TTestRunStepWrite,
  TTestRunWrite,
} from "@plane/types";
// services
import { APIService } from "@/services/api.service";

/**
 * The service of the test-case feature (STD-SW-003).
 *
 * A refusal of the server is thrown as the **body** of the answer — the
 * envelope of this feature: `{error, code, errors[]}` with a Chinese
 * sentence per problem, plus `current` on a 409 conflict. The editor shows
 * `errors[].message` next to the offending field, so the envelope has to
 * survive the trip through the service unchanged.
 */
export class TestCaseService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  private basePath(workspaceSlug: string, projectId: string): string {
    return `/api/workspaces/${workspaceSlug}/projects/${projectId}`;
  }

  // ------------------------------------------------------------------
  // The module registry
  // ------------------------------------------------------------------

  async getModules(workspaceSlug: string, projectId: string): Promise<ITestCaseModule[]> {
    return this.get(`${this.basePath(workspaceSlug, projectId)}/test-case-modules/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async createModule(
    workspaceSlug: string,
    projectId: string,
    data: { code: string; description: string }
  ): Promise<ITestCaseModule> {
    return this.post(`${this.basePath(workspaceSlug, projectId)}/test-case-modules/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  // ------------------------------------------------------------------
  // The cases
  // ------------------------------------------------------------------

  async getCases(
    workspaceSlug: string,
    projectId: string,
    filters?: TTestCaseFilters,
    config?: { per_page?: number; cursor?: string }
  ): Promise<TPaginatedResponse<ITestCase[]>> {
    const params: Record<string, string | number> = {};

    if (filters?.module) params.module = filters.module;
    if (filters?.priority) params.priority = filters.priority;
    if (filters?.issue) params.issue = filters.issue;
    if (filters?.status) params.status = filters.status;
    if (filters?.search) params.search = filters.search;
    if (config?.per_page) params.per_page = config.per_page;
    if (config?.cursor) params.cursor = config.cursor;

    return this.get(`${this.basePath(workspaceSlug, projectId)}/test-cases/`, { params })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async getCase(workspaceSlug: string, projectId: string, testCaseId: string): Promise<ITestCase> {
    return this.get(`${this.basePath(workspaceSlug, projectId)}/test-cases/${testCaseId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async createCase(workspaceSlug: string, projectId: string, data: TTestCaseWrite): Promise<ITestCase> {
    return this.post(`${this.basePath(workspaceSlug, projectId)}/test-cases/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * The write of the header. `updated_at` carries the optimistic lock: the
   * server answers 409 when the row changed in the meantime.
   */
  async updateCase(
    workspaceSlug: string,
    projectId: string,
    testCaseId: string,
    data: Partial<TTestCaseWrite> & { updated_at?: string }
  ): Promise<ITestCase> {
    return this.patch(`${this.basePath(workspaceSlug, projectId)}/test-cases/${testCaseId}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async deleteCase(workspaceSlug: string, projectId: string, testCaseId: string): Promise<void> {
    return this.delete(`${this.basePath(workspaceSlug, projectId)}/test-cases/${testCaseId}/`)
      .then(() => undefined)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  // ------------------------------------------------------------------
  // The links to the work items
  // ------------------------------------------------------------------

  async getIssueLinks(workspaceSlug: string, projectId: string, testCaseId: string): Promise<ITestCaseIssueLink[]> {
    return this.get(`${this.basePath(workspaceSlug, projectId)}/test-cases/${testCaseId}/issues/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async linkIssue(
    workspaceSlug: string,
    projectId: string,
    testCaseId: string,
    issueId: string
  ): Promise<ITestCaseIssueLink> {
    return this.post(`${this.basePath(workspaceSlug, projectId)}/test-cases/${testCaseId}/issues/`, {
      issue_id: issueId,
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async unlinkIssue(workspaceSlug: string, projectId: string, testCaseId: string, issueId: string): Promise<void> {
    return this.delete(`${this.basePath(workspaceSlug, projectId)}/test-cases/${testCaseId}/issues/${issueId}/`)
      .then(() => undefined)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  // ------------------------------------------------------------------
  // The work items of the project
  // ------------------------------------------------------------------

  /**
   * The work items of the project that match the term. The endpoint is
   * the one of the work items of the project — the picker of the editor
   * and the filter of the list both need it.
   */
  async searchWorkItems(workspaceSlug: string, projectId: string, search: string): Promise<ISearchIssueResponse[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/search-issues/`, {
      params: { search, workspace_search: false },
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  // ------------------------------------------------------------------
  // The batches — read only, for the *last execution result* of a case
  // ------------------------------------------------------------------

  /**
   * The batches of the project, newest day first. The case list derives
   * the last result of a case from them: the batches carry the case
   * records with their conclusion.
   */
  async getRuns(
    workspaceSlug: string,
    projectId: string,
    config?: { per_page?: number; cursor?: string; status?: string }
  ): Promise<TPaginatedResponse<ITestRunSummary[]>> {
    const params: Record<string, string | number> = {};

    if (config?.per_page) params.per_page = config.per_page;
    if (config?.cursor) params.cursor = config.cursor;
    if (config?.status) params.status = config.status;

    return this.get(`${this.basePath(workspaceSlug, projectId)}/test-runs/`, { params })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  // ------------------------------------------------------------------
  // One batch, its records and its summary
  // ------------------------------------------------------------------

  /** The path of one batch of the project. */
  private runPath(workspaceSlug: string, projectId: string, testRunId: string): string {
    return `${this.basePath(workspaceSlug, projectId)}/test-runs/${testRunId}`;
  }

  /** The path of one case record inside a batch. */
  private runCasePath(workspaceSlug: string, projectId: string, testRunId: string, runCaseId: string): string {
    return `${this.runPath(workspaceSlug, projectId, testRunId)}/cases/${runCaseId}`;
  }

  async getRun(workspaceSlug: string, projectId: string, testRunId: string): Promise<ITestRun> {
    return this.get(`${this.runPath(workspaceSlug, projectId, testRunId)}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** Create a batch. The number is assigned by the server. */
  async createRun(workspaceSlug: string, projectId: string, data: TTestRunWrite): Promise<ITestRun> {
    return this.post(`${this.basePath(workspaceSlug, projectId)}/test-runs/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * The header of a batch. The version fields and the execution date are
   * frozen once recorded, and a completed batch refuses every write.
   */
  async updateRun(
    workspaceSlug: string,
    projectId: string,
    testRunId: string,
    data: Partial<TTestRunWrite>
  ): Promise<ITestRun> {
    return this.patch(`${this.runPath(workspaceSlug, projectId, testRunId)}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * Close the batch. The server runs the validity rules first and answers
   * one entry per problem — `errors[].path` says which field or step it
   * belongs to.
   */
  async finalizeRun(workspaceSlug: string, projectId: string, testRunId: string): Promise<ITestRun> {
    return this.post(`${this.runPath(workspaceSlug, projectId, testRunId)}/finalize/`, {})
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * The four counts and the deviation list of the batch (STD-SW-003 §6.2
   * rule 3), computed by the server. The client never re-counts.
   */
  async getRunSummary(workspaceSlug: string, projectId: string, testRunId: string): Promise<ITestRunSummaryReport> {
    return this.get(`${this.runPath(workspaceSlug, projectId, testRunId)}/summary/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * Add cases to a batch. Every added case is snapshotted by the server; a
   * case that is already in the batch refuses the whole add (409
   * `duplicate_case_number`).
   */
  async addRunCases(
    workspaceSlug: string,
    projectId: string,
    testRunId: string,
    testCaseIds: string[]
  ): Promise<ITestRunCase[]> {
    return this.post(`${this.runPath(workspaceSlug, projectId, testRunId)}/cases/`, {
      test_case_ids: testCaseIds,
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * Take a case out of a batch.
   *
   * The refusal body of this feature is `{error, code, errors[]}` — and the
   * 404 of "this record is not in that batch" is only
   * `{"error": "Test run case not found"}`, with no code. The caller has to
   * tell "already gone" (404, nothing to do) from "refused" (409 on a frozen
   * batch or on a record that already carries a verdict, 403), so the HTTP
   * status travels with the body instead of being dropped here.
   */
  async removeRunCase(workspaceSlug: string, projectId: string, testRunId: string, runCaseId: string): Promise<void> {
    return this.delete(`${this.runCasePath(workspaceSlug, projectId, testRunId, runCaseId)}/`)
      .then(() => undefined)
      .catch((error) => {
        const body = error?.response?.data;
        const status = error?.response?.status;
        throw typeof body === "object" && body !== null ? { ...body, status } : { error: body, status };
      });
  }

  /** The outcome of one case inside a batch (conclusion and its fields). */
  async updateRunCase(
    workspaceSlug: string,
    projectId: string,
    testRunId: string,
    runCaseId: string,
    data: TTestRunCaseWrite
  ): Promise<ITestRunCase> {
    return this.patch(`${this.runCasePath(workspaceSlug, projectId, testRunId, runCaseId)}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * The verdict and the measured value of one step. The verdict is written
   * once; the measured value stays writable. The update is atomic for the
   * step and the last write wins.
   */
  async updateRunStep(
    workspaceSlug: string,
    projectId: string,
    testRunId: string,
    runCaseId: string,
    sequenceId: number,
    data: TTestRunStepWrite
  ): Promise<ITestRunStepRecord> {
    return this.patch(`${this.runCasePath(workspaceSlug, projectId, testRunId, runCaseId)}/steps/${sequenceId}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** Unbind (soft-delete) one file of a case record's evidence. */
  async deleteRunCaseAttachment(
    workspaceSlug: string,
    projectId: string,
    testRunId: string,
    runCaseId: string,
    assetId: string
  ): Promise<void> {
    return this.delete(`${this.runCasePath(workspaceSlug, projectId, testRunId, runCaseId)}/attachments/${assetId}/`)
      .then(() => undefined)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** Unbind (soft-delete) one file of a step record's measured value. */
  async deleteRunStepAttachment(
    workspaceSlug: string,
    projectId: string,
    testRunId: string,
    runCaseId: string,
    sequenceId: number,
    assetId: string
  ): Promise<void> {
    return this.delete(
      `${this.runCasePath(workspaceSlug, projectId, testRunId, runCaseId)}/steps/${sequenceId}/attachments/${assetId}/`
    )
      .then(() => undefined)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}

export const testCaseService = new TestCaseService();
