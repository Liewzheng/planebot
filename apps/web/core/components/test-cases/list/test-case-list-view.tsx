/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useMemo } from "react";
import { useTranslation } from "@plane/i18n";
import { EmptyStateDetailed } from "@plane/propel/empty-state";
import useSWR, { useSWRConfig } from "swr";
import type { ITestCase, ITestRunSummary } from "@plane/types";
// components
import { ListLayout } from "@/components/core/list";
import { TestCaseAppliedFiltersList } from "./test-case-applied-filters";
import { TestCaseRow, lastRunOfCase } from "./test-case-row";
// constants
import { TEST_CASE_LIST, TEST_CASE_RUNS } from "../constants";
// hooks
import { useAppRouter } from "@/hooks/use-app-router";
import { useTestCaseListFilters } from "./test-case-list-filters";
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
 * linked work item, the state and the search term — the controls of them
 * sit in the header of the page and share their state with the list
 * through `TestCaseListFiltersProvider`.
 */
export function TestCaseListView(props: Props) {
  const { workspaceSlug, projectId, canCreate } = props;
  const { t } = useTranslation();
  const router = useAppRouter();
  const { mutate } = useSWRConfig();
  // store
  const { filters, hasFilters, clearFilters } = useTestCaseListFilters();

  // The filter of the work item keeps the identifier of the work item
  // inside the query of the endpoint — the key of the cache has to carry
  // every value the endpoint is asked for, and nothing else.
  const filterKey = JSON.stringify([
    filters.module ?? "",
    filters.priority ?? "",
    filters.issue ?? "",
    filters.status ?? "",
    filters.search ?? "",
  ]);

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

  /** The last execution of a case — and whether it is known at all. */
  const lastRunFor = (testCase: ITestCase) => {
    const lastRun = lastRunOfCase(runList, testCase.number);
    const isUnknown = Boolean(runsError) || !runsArrived || (!lastRun && runsWindowTruncated);
    return { lastRun, isUnknown };
  };

  const handleDelete = useCallback(
    async (testCase: ITestCase) => {
      await testCaseService.deleteCase(workspaceSlug, projectId, testCase.id);
      await mutate(TEST_CASE_LIST(workspaceSlug, projectId, filterKey));
    },
    [workspaceSlug, projectId, filterKey, mutate]
  );

  const renderList = () => {
    if (isLoading) return <div className="p-4 text-13 text-tertiary">{t("project_test_case.list.loading")}</div>;

    if (listError)
      return <div className="p-4 text-13 text-danger-primary">{t("project_test_case.list.load_error")}</div>;

    // A list that matches no filter says so and offers to drop them;
    // a project without a case says that and offers to write the first
    // one. The two states never share their copy.
    if (results.length === 0)
      return hasFilters ? (
        <EmptyStateDetailed
          assetKey="search"
          title={t("project_test_case.list.no_result_title")}
          description={t("project_test_case.list.no_result_description")}
          actions={[
            {
              label: t("project_test_case.list.clear_filters"),
              onClick: clearFilters,
              variant: "secondary",
            },
          ]}
        />
      ) : (
        <EmptyStateDetailed
          assetKey="work-item"
          title={t("project_test_case.list.empty_title")}
          description={t("project_test_case.list.empty_description")}
          actions={[
            {
              label: t("project_test_case.list.create_button"),
              onClick: () => router.push(`/${workspaceSlug}/projects/${projectId}/test-cases/new`),
              disabled: !canCreate,
              variant: "primary",
            },
          ]}
        />
      );

    return (
      <ListLayout>
        {results.map((testCase) => {
          const { lastRun, isUnknown } = lastRunFor(testCase);
          return (
            <TestCaseRow
              key={testCase.id}
              workspaceSlug={workspaceSlug}
              projectId={projectId}
              testCase={testCase}
              lastRun={lastRun}
              lastResultUnknown={isUnknown}
              canEdit={canCreate}
              onDelete={handleDelete}
            />
          );
        })}
      </ListLayout>
    );
  };

  return (
    <div className="flex h-full w-full flex-col">
      {hasFilters && (
        <div className="border-b border-subtle px-4 py-2">
          <TestCaseAppliedFiltersList />
        </div>
      )}

      {/* The caption of the list. An empty list carries its own copy —
          the empty state — so this line would only repeat it. */}
      {results.length > 0 && (
        <p className="px-4 pt-3 pb-2 text-12 text-tertiary">{t("project_test_case.list.description")}</p>
      )}

      <div className="flex min-h-0 flex-1 flex-col">{renderList()}</div>
    </div>
  );
}
