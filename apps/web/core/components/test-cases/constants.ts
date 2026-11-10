/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { TTestCaseFilters, TTestCasePriority, TTestCaseStatus } from "@plane/types";

/** The keys the lists and the detail views are cached under. */
export const TEST_CASE_LIST = (workspaceSlug: string, projectId: string, filterKey: string) =>
  `TEST_CASE_LIST_${workspaceSlug}_${projectId}_${filterKey}`;

export const TEST_CASE_MODULES = (workspaceSlug: string, projectId: string) =>
  `TEST_CASE_MODULES_${workspaceSlug}_${projectId}`;

export const TEST_CASE_DETAIL = (workspaceSlug: string, projectId: string, testCaseId: string) =>
  `TEST_CASE_DETAIL_${workspaceSlug}_${projectId}_${testCaseId}`;

export const TEST_CASE_LINKS = (workspaceSlug: string, projectId: string, testCaseId: string) =>
  `TEST_CASE_LINKS_${workspaceSlug}_${projectId}_${testCaseId}`;

export const TEST_CASE_RUNS = (workspaceSlug: string, projectId: string) =>
  `TEST_CASE_RUNS_${workspaceSlug}_${projectId}`;

/** The priorities the server accepts. */
export const TEST_CASE_PRIORITIES: TTestCasePriority[] = ["P0", "P1", "P2", "P3"];

/** The states of a case. A voided case keeps its number. */
export const TEST_CASE_STATUSES: TTestCaseStatus[] = ["active", "void"];

/**
 * The pause after which a change is written to the server. The server
 * hard-validates every write, so a pause that is too short would send a
 * half-written record; 800 ms is the delay the mission asks for.
 */
export const TEST_CASE_AUTOSAVE_DEBOUNCE_MS = 800;

/**
 * The module code is 2 to 6 upper-case letters — the same rule the server
 * applies (`is_valid_module_code`).
 */
export const isValidModuleCode = (code: string): boolean => /^[A-Z]{2,6}$/.test(code);

/** The state the list starts with — the cases that are not voided. */
export const TEST_CASE_DEFAULT_STATUS = "active";

/**
 * True when the filters ask for something else than the default set.
 *
 * The default state of the list is the active cases without a filter, so
 * the default state is not a filter. Both the filter bar and the list
 * use this one definition: the bar offers "clear the filters" exactly
 * when there is something to clear, and the list says "no test case
 * matches the filters" exactly when the filters are the reason the list
 * is empty — a list of active cases that is empty is an empty list, not
 * a list that matches no filter.
 */
export const hasTestCaseFilters = (filters: TTestCaseFilters): boolean =>
  Boolean(
    filters.module ||
    filters.priority ||
    filters.issue ||
    filters.search ||
    (filters.status && filters.status !== TEST_CASE_DEFAULT_STATUS)
  );
