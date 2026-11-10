/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo, useState } from "react";
import { Link } from "react-router";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { EmptyStateDetailed } from "@plane/propel/empty-state";
import { AddOutline, PlayOutline } from "@makeplane/propel/icons";
import useSWR from "swr";
import type { ITestRun, ITestRunSummary } from "@plane/types";
// components
import { TestCaseRunCreateModal } from "./run-create-modal";
// constants
import { TEST_CASE_RUNS } from "../constants";
// hooks
import { useUser } from "@/hooks/store/user";
import { useAppRouter } from "@/hooks/use-app-router";
// services
import { testCaseService } from "@/services/test-cases/test-case.service";

type Props = {
  workspaceSlug: string;
  projectId: string;
  /** The reader may open a batch — a member and above. */
  canWrite: boolean;
};

/** The label key of one batch state. */
const statusKey = (status: string): string => {
  if (status === "completed") return "project_test_case.batches.status_completed";
  if (status === "in_progress") return "project_test_case.batches.status_in_progress";
  return "project_test_case.batches.status_draft";
};

/**
 * The batches of a project.
 *
 * A batch is one continuous execution against one build (§6.2). Its number
 * is assigned by the server; a re-test prints 「补测：RUN-…」 from the
 * `retest_of_number` the read already carries — and a soft-deleted original
 * still reports its number, so a missing link means "no original", never
 * "the original is gone".
 */
export function TestRunListView(props: Props) {
  const { workspaceSlug, projectId, canWrite } = props;
  const { t } = useTranslation();
  // router
  const router = useAppRouter();
  // store
  const { data: currentUser } = useUser();
  // states
  const [isCreateOpen, setIsCreateOpen] = useState(false);

  const { data, error, isLoading, mutate } = useSWR(
    workspaceSlug && projectId ? TEST_CASE_RUNS(workspaceSlug, projectId) : null,
    () => testCaseService.getRuns(workspaceSlug, projectId, { per_page: 50 })
  );

  const runs = useMemo<ITestRunSummary[]>(() => data?.results ?? [], [data]);

  const openRun = (run: ITestRun) => {
    setIsCreateOpen(false);
    void mutate();
    router.push(`/${workspaceSlug}/projects/${projectId}/test-runs/${run.id}`);
  };

  const renderBody = () => {
    if (isLoading) return <p className="p-4 text-13 text-tertiary">{t("project_test_case.batches.loading")}</p>;

    if (error) return <p className="p-4 text-13 text-danger-primary">{t("project_test_case.batches.load_error")}</p>;

    if (runs.length === 0)
      return (
        <EmptyStateDetailed
          assetKey="work-item"
          title={t("project_test_case.batches.empty_title")}
          description={t("project_test_case.batches.empty_description")}
          actions={[
            {
              label: t("project_test_case.batches.create_button"),
              onClick: () => setIsCreateOpen(true),
              disabled: !canWrite,
              variant: "primary",
            },
          ]}
        />
      );

    return (
      <ul className="flex flex-col">
        {runs.map((run) => (
          <li key={run.id}>
            <Link
              to={`/${workspaceSlug}/projects/${projectId}/test-runs/${run.id}`}
              className="flex min-h-[52px] w-full flex-wrap items-center gap-x-3 gap-y-1 border-b border-subtle px-4 py-2.5 text-13 transition-colors hover:bg-layer-transparent-hover"
            >
              <span className="grid size-8 shrink-0 place-items-center rounded-md border border-subtle bg-layer-1 text-tertiary">
                <PlayOutline className="size-4" />
              </span>
              <span className="font-mono shrink-0 text-11 text-tertiary">{run.number}</span>
              <span className="min-w-0 flex-1 truncate text-13 text-primary">
                {run.run_date}
                {run.cases?.length
                  ? ` · ${t("project_test_case.batches.case_count", { count: run.cases.length })}`
                  : ""}
              </span>
              {run.retest_of_number && (
                <span className="rounded-sm bg-layer-3 px-1.5 py-0.5 text-10 text-tertiary">
                  {t("project_test_case.batches.retest_of", { number: run.retest_of_number })}
                </span>
              )}
              <span
                className={[
                  "rounded-sm px-1.5 py-0.5 text-11",
                  run.status === "completed"
                    ? "bg-success-subtle text-success-primary"
                    : run.status === "in_progress"
                      ? "bg-accent-primary/10 text-accent-primary"
                      : "bg-layer-3 text-tertiary",
                ].join(" ")}
              >
                {t(statusKey(run.status))}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    );
  };

  return (
    <div className="flex h-full w-full flex-col">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-subtle px-4 py-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h1 className="text-14 font-semibold text-primary">{t("project_test_case.batches.title")}</h1>
          <p className="text-11 text-tertiary">{t("project_test_case.batches.description")}</p>
        </div>
        {canWrite && (
          <Button
            variant="primary"
            size="lg"
            onClick={() => setIsCreateOpen(true)}
            prependIcon={<AddOutline className="size-3.5" />}
          >
            {t("project_test_case.batches.create_button")}
          </Button>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">{renderBody()}</div>

      {canWrite && (
        <TestCaseRunCreateModal
          isOpen={isCreateOpen}
          onClose={() => setIsCreateOpen(false)}
          workspaceSlug={workspaceSlug}
          projectId={projectId}
          runs={runs}
          executorId={currentUser?.id}
          executorName={currentUser?.display_name}
          onCreated={openRun}
        />
      )}
    </div>
  );
}
