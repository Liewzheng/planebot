/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { Link } from "react-router";
import { useTranslation } from "@plane/i18n";
import { ChevronDownIcon, ChevronRightIcon } from "@plane/propel/icons";
import { Collapsible } from "@plane/ui";
import useSWR from "swr";
import type { ITestCase, ITestRunSummary } from "@plane/types";
// components
import { TestCaseResultBadge, lastRunOfCase } from "@/components/test-cases";
// constants
import { ISSUE_LINKED_TEST_CASES } from "@/components/test-cases/runs/constants";
import { TEST_CASE_RUNS } from "@/components/test-cases/constants";
// services
import { testCaseService } from "@/services/test-cases/test-case.service";

type Props = {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
};

/**
 * The test cases linked to this work item (STD-SW-003 §4.1 field 10).
 *
 * The block reads the cases of the issue through the app API filter
 * (`test-cases/?issue=<id>`) and derives the last execution result from the
 * batches of the project — the same derivation the case list uses, so the two
 * screens never disagree about a case. A linked case opens in the case page.
 *
 * The block is only rendered when the issue has a linked case: a work item
 * whose test coverage is empty says nothing extra here, and the case page is
 * where a case is created and linked.
 */
export function IssueLinkedTestCases(props: Props) {
  const { workspaceSlug, projectId, issueId } = props;
  const { t } = useTranslation();
  // states
  const [isOpen, setIsOpen] = useState(false);

  const { data: cases } = useSWR(
    workspaceSlug && projectId && issueId ? ISSUE_LINKED_TEST_CASES(workspaceSlug, projectId, issueId) : null,
    () => testCaseService.getCases(workspaceSlug, projectId, { issue: issueId, status: "active" }, { per_page: 50 })
  );

  const { data: runs, error: runsError } = useSWR(
    workspaceSlug && projectId ? TEST_CASE_RUNS(workspaceSlug, projectId) : null,
    () => testCaseService.getRuns(workspaceSlug, projectId, { per_page: 50 })
  );

  const linkedCases = cases?.results ?? [];
  if (linkedCases.length === 0) return null;

  const runList: ITestRunSummary[] = runs?.results ?? [];
  const runsArrived = runs !== undefined;
  const runsWindowTruncated = (runs?.total_results ?? 0) > runList.length;

  const lastResultOf = (testCase: ITestCase) => {
    const lastRun = lastRunOfCase(runList, testCase.number);
    // A window that does not hold every batch cannot prove a case was never
    // executed — the cell says "—" instead of inventing "never".
    const isUnknown = Boolean(runsError) || !runsArrived || (!lastRun && runsWindowTruncated);
    return { lastRun, isUnknown };
  };

  return (
    <Collapsible
      isOpen={isOpen}
      onToggle={() => setIsOpen((current) => !current)}
      buttonClassName="w-full"
      title={
        <div className="flex w-full items-center gap-2 px-0 py-1.5 text-left">
          {isOpen ? (
            <ChevronDownIcon className="size-3.5 shrink-0 text-tertiary" />
          ) : (
            <ChevronRightIcon className="size-3.5 shrink-0 text-tertiary" />
          )}
          <span className="text-11 font-medium text-secondary">
            {t("project_test_case.issue_block.title", { count: linkedCases.length })}
          </span>
        </div>
      }
    >
      <ul className="flex flex-col gap-1 pl-5">
        {linkedCases.map((testCase) => {
          const { lastRun, isUnknown } = lastResultOf(testCase);
          return (
            <li key={testCase.id} className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
              <Link
                to={`/${workspaceSlug}/projects/${projectId}/test-cases/${testCase.id}`}
                className="min-w-0 flex-1 truncate text-12 text-secondary hover:text-primary hover:underline"
              >
                <span className="font-mono mr-1 text-11 text-tertiary">{testCase.number}</span>
                {testCase.title || "—"}
              </Link>
              {isUnknown ? (
                <span className="text-11 text-tertiary">—</span>
              ) : (
                <TestCaseResultBadge conclusion={lastRun?.conclusion} />
              )}
              {lastRun && !isUnknown && <span className="text-11 text-tertiary">{lastRun.runNumber}</span>}
            </li>
          );
        })}
      </ul>
    </Collapsible>
  );
}
