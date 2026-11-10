/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";
import useSWR from "swr";
import type { ITestCaseModule, TTestCaseFilters } from "@plane/types";
// constants
import { TEST_CASE_DEFAULT_STATUS, TEST_CASE_MODULES, countTestCaseFilters, hasTestCaseFilters } from "../constants";
// services
import { testCaseService } from "@/services/test-cases/test-case.service";

/**
 * The work item the list is filtered by. The filter travels as the id of
 * the work item — the label is what the chip of the applied filters shows.
 */
export type TTestCaseFilterWorkItem = { id: string; label: string };

type TTestCaseListFilters = {
  workspaceSlug: string;
  projectId: string;
  /** The module registry of the project — the filter names the modules. */
  modules: ITestCaseModule[];
  filters: TTestCaseFilters;
  workItem: TTestCaseFilterWorkItem | null;
  hasFilters: boolean;
  appliedFilterCount: number;
  updateFilters: (patch: TTestCaseFilters) => void;
  setWorkItem: (workItem: TTestCaseFilterWorkItem | null) => void;
  removeFilter: (key: keyof TTestCaseFilters) => void;
  clearFilters: () => void;
};

const TestCaseListFiltersContext = createContext<TTestCaseListFilters | null>(null);

/**
 * The filter state of the list of the cases.
 *
 * The controls of the list sit in the header of the page while the list
 * itself is rendered by the page — a provider above both of them (the
 * layout of the route) is what lets the two share one filter state, the
 * same way the views page shares its filters through the view store.
 */
export function TestCaseListFiltersProvider(props: { workspaceSlug: string; projectId: string; children: ReactNode }) {
  const { workspaceSlug, projectId, children } = props;
  // states
  const [filters, setFilters] = useState<TTestCaseFilters>({ status: TEST_CASE_DEFAULT_STATUS });
  const [workItem, setWorkItemState] = useState<TTestCaseFilterWorkItem | null>(null);

  // The module registry — the header, the filter panel and the chips all
  // read it from here, so the project is asked for it once.
  const { data: modules } = useSWR(
    workspaceSlug && projectId ? TEST_CASE_MODULES(workspaceSlug, projectId) : null,
    () => testCaseService.getModules(workspaceSlug, projectId)
  );

  const updateFilters = useCallback((patch: TTestCaseFilters) => {
    setFilters((current) => ({ ...current, ...patch }));
  }, []);

  const setWorkItem = useCallback((next: TTestCaseFilterWorkItem | null) => {
    setWorkItemState(next);
    setFilters((current) => ({ ...current, issue: next?.id ?? "" }));
  }, []);

  /**
   * Remove one filter. The state filter falls back to the default state
   * of the list — an empty state filter is not a state the server knows.
   */
  const removeFilter = useCallback(
    (key: keyof TTestCaseFilters) => {
      if (key === "issue") {
        setWorkItem(null);
        return;
      }
      const reset = key === "status" ? TEST_CASE_DEFAULT_STATUS : "";
      setFilters((current) => ({ ...current, [key]: reset }));
    },
    [setWorkItem]
  );

  const clearFilters = useCallback(() => {
    setWorkItemState(null);
    setFilters({ status: TEST_CASE_DEFAULT_STATUS });
  }, []);

  const value = useMemo<TTestCaseListFilters>(
    () => ({
      workspaceSlug,
      projectId,
      modules: modules ?? [],
      filters,
      workItem,
      hasFilters: hasTestCaseFilters(filters),
      appliedFilterCount: countTestCaseFilters(filters),
      updateFilters,
      setWorkItem,
      removeFilter,
      clearFilters,
    }),
    [workspaceSlug, projectId, modules, filters, workItem, updateFilters, setWorkItem, removeFilter, clearFilters]
  );

  return <TestCaseListFiltersContext.Provider value={value}>{children}</TestCaseListFiltersContext.Provider>;
}

/** The filter state of the list — shared by the header controls and the list. */
export const useTestCaseListFilters = (): TTestCaseListFilters => {
  const context = useContext(TestCaseListFiltersContext);
  if (!context) throw new Error("useTestCaseListFilters must be used inside TestCaseListFiltersProvider");
  return context;
};
