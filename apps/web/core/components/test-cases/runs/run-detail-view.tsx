/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useState } from "react";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { AlertModalCore } from "@plane/ui";
import useSWR from "swr";
// components
import { TestCaseRunAddCases } from "./run-add-cases";
import { TestCaseRunCaseCard } from "./run-case-card";
import { TestCaseRunSummaryPanel } from "./run-summary-panel";
// constants
import { TEST_RUN_DETAIL, TEST_RUN_SUMMARY_REPORT } from "./constants";
// helpers
import { parseRunErrors, refusalFallback, withRefusalSentence } from "./types";
import type { TParsedRunErrors, TRunRefusal } from "./types";
// services
import { testCaseService } from "@/services/test-cases/test-case.service";

type Props = {
  workspaceSlug: string;
  projectId: string;
  testRunId: string;
  /** The reader may write into the batch — a member and above. */
  canWrite: boolean;
};

const statusKey = (status: string): string => {
  if (status === "completed") return "project_test_case.batches.status_completed";
  if (status === "in_progress") return "project_test_case.batches.status_in_progress";
  return "project_test_case.batches.status_draft";
};

/**
 * One batch, from its header to its summary.
 *
 * The page is the execution surface: the batch header (version, environment,
 * executor, date, and the batch it re-tests), the summary the server computes,
 * the panel that fills the batch with cases, and one card per case with its
 * execution form.
 *
 * `finalize` is the only way to close a batch: the server runs the validity
 * rules and answers one entry per problem, and every entry is put next to the
 * field or the step it names. A completed batch is read-only — the header
 * says so and the forms are disabled, because the server refuses every write
 * on it.
 */
export function TestRunDetailView(props: Props) {
  const { workspaceSlug, projectId, testRunId, canWrite } = props;
  const { t } = useTranslation();
  // states
  const [isConfirmingFinalize, setIsConfirmingFinalize] = useState(false);
  const [isFinalizing, setIsFinalizing] = useState(false);
  const [errors, setErrors] = useState<TParsedRunErrors>({ run: {}, cases: {}, form: [] });
  const [hasFinalizeErrors, setHasFinalizeErrors] = useState(false);

  const {
    data: run,
    error,
    isLoading,
    mutate,
  } = useSWR(
    workspaceSlug && projectId && testRunId ? TEST_RUN_DETAIL(workspaceSlug, projectId, testRunId) : null,
    () => testCaseService.getRun(workspaceSlug, projectId, testRunId)
  );

  const {
    data: summary,
    isLoading: isSummaryLoading,
    error: summaryError,
    mutate: mutateSummary,
  } = useSWR(
    workspaceSlug && projectId && testRunId ? TEST_RUN_SUMMARY_REPORT(workspaceSlug, projectId, testRunId) : null,
    () => testCaseService.getRunSummary(workspaceSlug, projectId, testRunId)
  );

  const refresh = useCallback(() => {
    void mutate();
    void mutateSummary();
  }, [mutate, mutateSummary]);

  const finalize = async () => {
    setIsFinalizing(true);
    setErrors({ run: {}, cases: {}, form: [] });
    setHasFinalizeErrors(false);

    try {
      await testCaseService.finalizeRun(workspaceSlug, projectId, testRunId);
      setIsConfirmingFinalize(false);
      refresh();
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("project_test_case.execution.finalize.success_title"),
        message: t("project_test_case.execution.finalize.success_message"),
      });
    } catch (finalizeError) {
      const refusal = finalizeError as TRunRefusal;
      // A 403/404/409 body carries a sentence but often no entry the parser
      // can place; without the fallback the page would only say "each reason
      // is shown next to its field" while showing none. A body with no
      // sentence at all (a proxy's 502) still gets the status sentence.
      setErrors(
        withRefusalSentence(
          parseRunErrors(refusal),
          refusal,
          refusalFallback(refusal, t, "project_test_case.execution.errors.write_not_saved")
        )
      );
      setHasFinalizeErrors(true);
      setIsConfirmingFinalize(false);
    } finally {
      setIsFinalizing(false);
    }
  };

  if (isLoading && !run) return <p className="p-4 text-13 text-tertiary">{t("project_test_case.execution.loading")}</p>;

  if (error || !run)
    return (
      <div className="flex flex-col gap-1 p-4">
        <p className="text-13 text-danger-primary">{t("project_test_case.execution.not_found")}</p>
        <p className="text-11 text-tertiary">{t("project_test_case.execution.load_failed")}</p>
      </div>
    );

  const isCompleted = run.status === "completed";
  const isEditable = canWrite && !isCompleted;
  const existingCaseIds = run.cases.map((runCase) => runCase.test_case).filter((id): id is string => Boolean(id));

  const runLevelErrors = [...Object.entries(errors.run).flatMap(([, messages]) => messages), ...errors.form];

  return (
    <div className="flex h-full w-full flex-col gap-4 overflow-y-auto px-4 py-4 pb-24">
      {/* --- the batch header ------------------------------------------ */}
      <section className="flex flex-col gap-3 rounded-md border border-subtle-1 bg-layer-1 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-12 text-tertiary">{run.number}</span>
          <h1 className="text-15 min-w-0 flex-1 truncate font-semibold text-primary">
            {run.version_number || t("project_test_case.execution.untitled_version")}
          </h1>
          <span
            className={[
              "rounded-sm px-1.5 py-0.5 text-11",
              isCompleted
                ? "bg-success-subtle text-success-primary"
                : run.status === "in_progress"
                  ? "bg-accent-primary/10 text-accent-primary"
                  : "bg-layer-3 text-tertiary",
            ].join(" ")}
          >
            {t(statusKey(run.status))}
          </span>
        </div>

        {run.retest_of_number && (
          <p className="rounded-md border border-subtle-1 bg-layer-2 px-2.5 py-1.5 text-12 text-secondary">
            {t("project_test_case.batches.retest_of", { number: run.retest_of_number })}
          </p>
        )}

        <dl className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {[
            { key: "date", label: t("project_test_case.batches.field_run_date"), value: run.run_date },
            { key: "version", label: t("project_test_case.batches.field_version_number"), value: run.version_number },
            { key: "build", label: t("project_test_case.batches.field_build_number"), value: run.build_number },
            { key: "commit", label: t("project_test_case.batches.field_commit_hash"), value: run.commit_hash },
            { key: "executor", label: t("project_test_case.batches.field_executor"), value: run.executed_by_name },
            { key: "environment", label: t("project_test_case.batches.field_environment"), value: run.environment },
          ].map((item) => (
            <div key={item.key} className="flex min-w-0 flex-col gap-0.5">
              <dt className="text-11 text-tertiary">{item.label}</dt>
              <dd className="text-12 break-words text-secondary">{item.value || "—"}</dd>
            </div>
          ))}
        </dl>

        {isCompleted && <p className="text-11 text-tertiary">{t("project_test_case.execution.completed_hint")}</p>}

        {runLevelErrors.length > 0 && (
          <div className="flex flex-col gap-1 rounded-md border border-danger-strong bg-danger-subtle px-2.5 py-2">
            <p className="text-11 font-medium text-danger-primary">
              {t("project_test_case.execution.finalize.refused_title")}
            </p>
            <ul className="flex flex-col gap-0.5">
              {runLevelErrors.map((message) => (
                <li key={message} className="text-11 text-danger-primary">
                  {message}
                </li>
              ))}
            </ul>
          </div>
        )}

        {isEditable && (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="primary"
              size="lg"
              onClick={() => setIsConfirmingFinalize(true)}
              disabled={run.cases.length === 0}
            >
              {t("project_test_case.execution.finalize.button")}
            </Button>
            {run.cases.length === 0 && (
              <span className="text-11 text-tertiary">{t("project_test_case.execution.finalize.needs_cases")}</span>
            )}
            {hasFinalizeErrors && (
              <span className="text-11 text-danger-primary">
                {t("project_test_case.execution.finalize.refused_hint")}
              </span>
            )}
          </div>
        )}
      </section>

      {/* --- the summary of the server -------------------------------- */}
      <TestCaseRunSummaryPanel
        summary={summary}
        isLoading={isSummaryLoading && !summary}
        hasError={Boolean(summaryError)}
      />

      {/* --- filling the batch ---------------------------------------- */}
      {isEditable && (
        <TestCaseRunAddCases
          workspaceSlug={workspaceSlug}
          projectId={projectId}
          testRunId={testRunId}
          existingCaseIds={existingCaseIds}
          disabled={!isEditable}
          onAdded={refresh}
        />
      )}

      {/* --- the cases ------------------------------------------------ */}
      <section className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-13 font-semibold text-primary">
            {t("project_test_case.execution.cases_title", { count: run.cases.length })}
          </h2>
          <span className="text-11 text-tertiary">{t("project_test_case.execution.cases_hint")}</span>
        </div>

        {run.cases.length === 0 ? (
          <p className="rounded-md border border-dashed border-subtle-1 bg-layer-2/40 px-3 py-6 text-12 text-tertiary">
            {t("project_test_case.execution.cases_empty")}
          </p>
        ) : (
          <ul className="flex flex-col gap-3">
            {run.cases.map((runCase) => (
              <li key={runCase.id}>
                <TestCaseRunCaseCard
                  workspaceSlug={workspaceSlug}
                  projectId={projectId}
                  testRunId={testRunId}
                  runCase={runCase}
                  disabled={!isEditable}
                  canRemove={isEditable}
                  errors={errors.cases[runCase.case_number]}
                  onChanged={refresh}
                />
              </li>
            ))}
          </ul>
        )}
      </section>

      <AlertModalCore
        isOpen={isConfirmingFinalize}
        handleClose={() => setIsConfirmingFinalize(false)}
        handleSubmit={() => void finalize()}
        isSubmitting={isFinalizing}
        title={t("project_test_case.execution.finalize.confirm_title")}
        content={t("project_test_case.execution.finalize.confirm_content")}
        primaryButtonText={{
          default: t("project_test_case.execution.finalize.button"),
          loading: t("project_test_case.execution.finalize.finalizing"),
        }}
        secondaryButtonText={t("common.cancel")}
      />
    </div>
  );
}
