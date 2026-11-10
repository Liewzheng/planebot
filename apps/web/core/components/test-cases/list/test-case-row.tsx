/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { Link } from "react-router";
import type { ITestCase, ITestRunSummary } from "@plane/types";
// components
import { TestCasePriorityBadge, TestCaseResultBadge, TestCaseStatusBadge } from "../shared/badges";

type Props = {
  workspaceSlug: string;
  projectId: string;
  testCase: ITestCase;
  /** The batch the case was executed in the last time. */
  lastRun?: { runNumber: string; conclusion: string };
  /**
   * The batches could not be read — the last result is unknown then, and
   * saying "never executed" would be a guess.
   */
  lastResultUnknown?: boolean;
};

/**
 * One row of the table of the cases — the wide screen.
 */
export function TestCaseTableRow(props: Props) {
  const { workspaceSlug, projectId, testCase, lastRun, lastResultUnknown } = props;

  const href = `/${workspaceSlug}/projects/${projectId}/test-cases/${testCase.id}`;

  return (
    <tr className="group border-b border-subtle-1 transition-colors hover:bg-layer-2">
      <td className="w-32 px-4 py-3 align-middle whitespace-nowrap">
        <Link to={href} className="font-mono text-12 text-secondary hover:text-primary">
          {testCase.number}
        </Link>
      </td>
      <td className="px-4 py-3 align-middle">
        <Link to={href} className="block truncate text-13 text-primary hover:underline">
          {testCase.title || "—"}
        </Link>
        {testCase.issue_links.length > 0 && (
          <div className="mt-1 flex flex-wrap items-center gap-1">
            {testCase.issue_links.map((link) => (
              <span
                key={link.id}
                className="rounded-sm bg-layer-3 px-1.5 py-0.5 text-10 text-tertiary"
                title={link.issue_name ?? undefined}
              >
                {link.issue_identifier}
              </span>
            ))}
          </div>
        )}
      </td>
      <td className="w-24 px-4 py-3 align-middle whitespace-nowrap">
        <span className="font-mono text-12 text-secondary">{testCase.module_code}</span>
      </td>
      <td className="w-24 px-4 py-3 align-middle whitespace-nowrap">
        <TestCasePriorityBadge priority={testCase.priority} />
      </td>
      <td className="w-24 px-4 py-3 align-middle whitespace-nowrap">
        <TestCaseStatusBadge status={testCase.status} />
      </td>
      <td className="w-40 px-4 py-3 align-middle whitespace-nowrap">
        <div className="flex items-center gap-2">
          {lastResultUnknown ? (
            <span className="text-tertiary">—</span>
          ) : (
            <TestCaseResultBadge conclusion={lastRun?.conclusion} />
          )}
          {lastRun && !lastResultUnknown && <span className="text-11 text-tertiary">{lastRun.runNumber}</span>}
        </div>
      </td>
    </tr>
  );
}

/**
 * One card of the list of the cases — the narrow screen. The columns of
 * the table stack inside the card, so nothing has to be scrolled sideways
 * to read a case on a phone.
 */
export function TestCaseCard(props: Props) {
  const { workspaceSlug, projectId, testCase, lastRun, lastResultUnknown } = props;

  const href = `/${workspaceSlug}/projects/${projectId}/test-cases/${testCase.id}`;

  return (
    <li className="flex flex-col gap-2 border-b border-subtle-1 px-4 py-3">
      <div className="flex items-start justify-between gap-2">
        <Link to={href} className="min-w-0 flex-1">
          <span className="block truncate text-13 text-primary">{testCase.title || "—"}</span>
          <span className="font-mono mt-0.5 block text-11 text-tertiary">
            {testCase.number} · {testCase.module_code}
          </span>
        </Link>
        <TestCasePriorityBadge priority={testCase.priority} />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <TestCaseStatusBadge status={testCase.status} />
        {lastResultUnknown ? (
          <span className="text-tertiary">—</span>
        ) : (
          <TestCaseResultBadge conclusion={lastRun?.conclusion} />
        )}
        {lastRun && !lastResultUnknown && <span className="text-11 text-tertiary">{lastRun.runNumber}</span>}
      </div>

      {testCase.issue_links.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          {testCase.issue_links.map((link) => (
            <span
              key={link.id}
              className="rounded-sm bg-layer-3 px-1.5 py-0.5 text-10 text-tertiary"
              title={link.issue_name ?? undefined}
            >
              {link.issue_identifier}
            </span>
          ))}
        </div>
      )}
    </li>
  );
}

/** The batches a case was executed in — the newest one wins. */
export const lastRunOfCase = (
  runs: ITestRunSummary[],
  caseNumber: string
): { runNumber: string; conclusion: string } | undefined => {
  for (const run of runs) {
    const record = run.cases?.find((entry) => entry.case_number === caseNumber);
    if (record) return { runNumber: run.number, conclusion: record.conclusion };
  }
  return undefined;
};
