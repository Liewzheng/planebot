/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo, useState } from "react";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { AddOutline } from "@makeplane/propel/icons";
import useSWR from "swr";
import type { ITestCase, TTestCaseFilters, ITestRunSummary } from "@plane/types";
// components
import { TestCaseFilters } from "./test-case-filters";
import { TestCaseCard, TestCaseTableRow, lastRunOfCase } from "./test-case-row";
// constants
import { TEST_CASE_LIST, TEST_CASE_MODULES, TEST_CASE_RUNS, hasTestCaseFilters } from "../constants";
// hooks
import { useAppRouter } from "@/hooks/use-app-router";
// services
import { testCaseService } from "@/services/test-cases/test-case.service";

type Props = {
  workspaceSlug: string;
  projectId: string;
  canCreate: boolean;
};

/**
 * The list of the test cases of a project.
 *
 * The list shows the number, the title, the module, the priority, the
 * state and the result of the last execution of every case. The filters
 * are the ones the endpoint understands: the module, the priority, the
 * linked work item, the state and the search term.
 */
export function TestCaseListView(props: Props) {
  const { workspaceSlug, projectId, canCreate } = props;
  const { t } = useTranslation();
  const router = useAppRouter();
  // states
  const [filters, setFilters] = useState<TTestCaseFilters>({ status: "active" });

  // The filter of the work item keeps the identifier of the work item;
  // the list shows the name of it next to the filter.
  const filterKey = JSON.stringify([
    filters.module ?? "",
    filters.priority ?? "",
    filters.issue ?? "",
    filters.status ?? "",
    filters.search ?? "",
  ]);

  const { data: modules } = useSWR(
    workspaceSlug && projectId ? TEST_CASE_MODULES(workspaceSlug, projectId) : null,
    () => testCaseService.getModules(workspaceSlug, projectId)
  );

  const {
    data: cases,
    error: listError,
    isLoading,
  } = useSWR(workspaceSlug && projectId ? TEST_CASE_LIST(workspaceSlug, projectId, filterKey) : null, () =>
    testCaseService.getCases(workspaceSlug, projectId, filters)
  );

  // The batches carry the conclusion of the cases they held — the list
  // derives the result of the last execution out of them.
  const { data: runs, error: runsError } = useSWR(
    workspaceSlug && projectId ? TEST_CASE_RUNS(workspaceSlug, projectId) : null,
    () => testCaseService.getRuns(workspaceSlug, projectId, { per_page: 50 })
  );

  const runList = useMemo<ITestRunSummary[]>(() => runs?.results ?? [], [runs]);

  // The window of the batches is not the whole history: when the server
  // holds more batches than the client has seen, a case that is not in
  // the window may still have been executed. The cell then says "—" — it
  // may only say "never executed" when every batch was seen.
  const runsWindowTruncated = (runs?.total_results ?? 0) > runList.length;
  // And before the first answer arrives the client has seen no batch at
  // all, so it knows nothing about the last execution of a case. The
  // batch answer is the heavy one, so this window is not short.
  const runsArrived = runs !== undefined;

  const results = useMemo(() => cases?.results ?? [], [cases]);
  // The same definition the filter bar uses: the default set of filters
  // is not a filter, so an empty list of active cases is an empty list
  // and not a list that matches no filter.
  const hasFilters = hasTestCaseFilters(filters);

  /** The last execution of a case — and whether it is known at all. */
  const lastRunFor = (testCase: ITestCase) => {
    const lastRun = lastRunOfCase(runList, testCase.number);
    const isUnknown = Boolean(runsError) || !runsArrived || (!lastRun && runsWindowTruncated);
    return { lastRun, isUnknown };
  };

  return (
    <div className="flex h-full w-full flex-col">
      <TestCaseFilters
        workspaceSlug={workspaceSlug}
        projectId={projectId}
        modules={modules ?? []}
        module={filters.module ?? ""}
        priority={filters.priority ?? ""}
        issue={filters.issue ?? ""}
        status={filters.status ?? "active"}
        search={filters.search ?? ""}
        onChange={(patch) => setFilters((current) => ({ ...current, ...patch }))}
        onClear={() => setFilters({ status: "active" })}
      />

      <p className="px-4 pt-3 text-12 text-tertiary">{t("project_test_case.list.description")}</p>

      <div className="flex-1 overflow-y-auto">
        {isLoading && <div className="p-4 text-13 text-tertiary">{t("project_test_case.list.loading")}</div>}

        {listError && !isLoading && (
          <div className="p-4 text-13 text-danger-primary">{t("project_test_case.list.load_error")}</div>
        )}

        {!isLoading && !listError && results.length === 0 && (
          <div className="flex flex-col items-start gap-2 p-6">
            <p className="text-14 text-primary">
              {hasFilters ? t("project_test_case.list.no_result_title") : t("project_test_case.list.empty_title")}
            </p>
            <p className="text-13 text-tertiary">
              {hasFilters
                ? t("project_test_case.list.no_result_description")
                : t("project_test_case.list.empty_description")}
            </p>
            {!hasFilters && canCreate && (
              <Button
                variant="primary"
                size="lg"
                onClick={() => router.push(`/${workspaceSlug}/projects/${projectId}/test-cases/new`)}
                prependIcon={<AddOutline className="size-3.5" />}
                className="mt-2"
              >
                {t("project_test_case.list.create_button")}
              </Button>
            )}
          </div>
        )}

        {results.length > 0 && (
          <>
            {/* The wide screen: a table */}
            <table className="hidden w-full table-fixed border-collapse md:table">
              <thead>
                <tr className="border-b border-subtle-1 text-left">
                  <th className="w-32 px-4 py-2 text-11 font-medium text-tertiary">
                    {t("project_test_case.list.column_number")}
                  </th>
                  <th className="px-4 py-2 text-11 font-medium text-tertiary">
                    {t("project_test_case.list.column_title")}
                  </th>
                  <th className="w-24 px-4 py-2 text-11 font-medium text-tertiary">
                    {t("project_test_case.list.column_module")}
                  </th>
                  <th className="w-24 px-4 py-2 text-11 font-medium text-tertiary">
                    {t("project_test_case.list.column_priority")}
                  </th>
                  <th className="w-24 px-4 py-2 text-11 font-medium text-tertiary">
                    {t("project_test_case.list.column_status")}
                  </th>
                  <th className="w-40 px-4 py-2 text-11 font-medium text-tertiary">
                    {t("project_test_case.list.column_last_result")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {results.map((testCase) => {
                  const { lastRun, isUnknown } = lastRunFor(testCase);
                  return (
                    <TestCaseTableRow
                      key={testCase.id}
                      workspaceSlug={workspaceSlug}
                      projectId={projectId}
                      testCase={testCase}
                      lastRun={lastRun}
                      lastResultUnknown={isUnknown}
                    />
                  );
                })}
              </tbody>
            </table>

            {/* The narrow screen: a card per case */}
            <ul className="flex flex-col md:hidden">
              {results.map((testCase) => {
                const { lastRun, isUnknown } = lastRunFor(testCase);
                return (
                  <TestCaseCard
                    key={testCase.id}
                    workspaceSlug={workspaceSlug}
                    projectId={projectId}
                    testCase={testCase}
                    lastRun={lastRun}
                    lastResultUnknown={isUnknown}
                  />
                );
              })}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}
