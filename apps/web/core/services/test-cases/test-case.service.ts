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
  ITestRunSummary,
  TTestCaseFilters,
  TTestCaseWrite,
  TPaginatedResponse,
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
    config?: { per_page?: number; cursor?: string }
  ): Promise<TPaginatedResponse<ITestRunSummary[]>> {
    const params: Record<string, string | number> = {};

    if (config?.per_page) params.per_page = config.per_page;
    if (config?.cursor) params.cursor = config.cursor;

    return this.get(`${this.basePath(workspaceSlug, projectId)}/test-runs/`, { params })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}

export const testCaseService = new TestCaseService();
