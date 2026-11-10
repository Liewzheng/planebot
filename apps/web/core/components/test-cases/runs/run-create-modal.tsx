/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { EModalPosition, EModalWidth, Input, ModalCore, TextArea } from "@plane/ui";
import type { ITestCaseErrorResponse, ITestRun, ITestRunSummary } from "@plane/types";
// helpers
import { parseRunErrors } from "./types";
// services
import { testCaseService } from "@/services/test-cases/test-case.service";

type Props = {
  isOpen: boolean;
  onClose: () => void;
  workspaceSlug: string;
  projectId: string;
  /** The batches of the project — a re-test names the one it re-tests. */
  runs: ITestRunSummary[];
  /** The executor of the batch — the reader, by default. */
  executorId?: string;
  executorName?: string;
  onCreated: (run: ITestRun) => void;
};

const pad = (value: number): string => String(value).padStart(2, "0");

/** The day of the browser, as the batch header stores it. */
const today = (): string => {
  const date = new Date();
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
};

/**
 * The creation of a batch.
 *
 * The six header items of §6.2 are asked for; the **number** is not one of
 * them — `RUN-<YYYYMMDD>-<NN>` is assigned by the server, and the number of a
 * re-test points at the batch it re-tests (§6.2 rule 2). The batch is opened
 * as `in_progress`: it exists because somebody started executing, and a
 * completed batch is closed through `finalize`, never by a status write.
 */
export function TestCaseRunCreateModal(props: Props) {
  const { isOpen, onClose, workspaceSlug, projectId, runs, executorId, executorName, onCreated } = props;
  const { t } = useTranslation();
  // states
  const [runDate, setRunDate] = useState(today());
  const [versionNumber, setVersionNumber] = useState("");
  const [buildNumber, setBuildNumber] = useState("");
  const [commitHash, setCommitHash] = useState("");
  const [environment, setEnvironment] = useState("");
  const [retestOf, setRetestOf] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const [formError, setFormError] = useState<string | null>(null);

  // Every opening starts from an empty header — a half-filled batch of the
  // last attempt is not a draft anybody asked to keep.
  useEffect(() => {
    if (!isOpen) return;
    setRunDate(today());
    setVersionNumber("");
    setBuildNumber("");
    setCommitHash("");
    setEnvironment("");
    setRetestOf("");
    setErrors({});
    setFormError(null);
  }, [isOpen]);

  const required = [runDate, versionNumber, buildNumber, commitHash, environment];
  // §6.4 asks for the executor as one of the four items of the record; a
  // batch without one would only be refused at finalize.
  const isComplete = required.every((value) => value.trim().length > 0) && Boolean(executorId);

  const submit = async () => {
    setIsCreating(true);
    setErrors({});
    setFormError(null);

    try {
      const created = await testCaseService.createRun(workspaceSlug, projectId, {
        run_date: runDate,
        version_number: versionNumber.trim(),
        build_number: buildNumber.trim(),
        commit_hash: commitHash.trim(),
        environment: environment.trim(),
        executed_by: executorId ?? null,
        status: "in_progress",
        retest_of: retestOf || null,
      });
      onCreated(created);
    } catch (error) {
      const envelope = error as ITestCaseErrorResponse | undefined;
      const parsed = parseRunErrors(envelope);
      setErrors(parsed.run);
      setFormError(parsed.form[0] ?? (Object.keys(parsed.run).length === 0 ? (envelope?.error ?? null) : null));
    } finally {
      setIsCreating(false);
    }
  };

  const renderErrors = (field: string) =>
    errors[field]?.length > 0 ? (
      <ul className="flex flex-col gap-0.5">
        {errors[field].map((message) => (
          <li key={message} className="text-11 text-danger-primary">
            {message}
          </li>
        ))}
      </ul>
    ) : null;

  return (
    <ModalCore isOpen={isOpen} handleClose={onClose} position={EModalPosition.CENTER} width={EModalWidth.XXL}>
      <div className="flex max-h-[85vh] flex-col gap-4 overflow-y-auto p-5">
        <div className="flex flex-col gap-1">
          <h2 className="text-16 font-semibold text-primary">{t("project_test_case.batches.create_title")}</h2>
          <p className="text-12 text-secondary">{t("project_test_case.batches.create_description")}</p>
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="flex flex-col gap-1">
            <label htmlFor="run-date" className="text-11 font-medium text-secondary">
              {t("project_test_case.batches.field_run_date")}
            </label>
            <Input
              id="run-date"
              type="date"
              value={runDate}
              onChange={(event) => setRunDate(event.target.value)}
              hasError={Boolean(errors.run_date?.length)}
            />
            {renderErrors("run_date")}
          </div>

          <div className="flex flex-col gap-1">
            <label htmlFor="run-version" className="text-11 font-medium text-secondary">
              {t("project_test_case.batches.field_version_number")}
            </label>
            <Input
              id="run-version"
              value={versionNumber}
              onChange={(event) => setVersionNumber(event.target.value)}
              placeholder={t("project_test_case.batches.placeholder_version_number")}
              hasError={Boolean(errors.version_number?.length)}
            />
            {renderErrors("version_number")}
          </div>

          <div className="flex flex-col gap-1">
            <label htmlFor="run-build" className="text-11 font-medium text-secondary">
              {t("project_test_case.batches.field_build_number")}
            </label>
            <Input
              id="run-build"
              value={buildNumber}
              onChange={(event) => setBuildNumber(event.target.value)}
              placeholder={t("project_test_case.batches.placeholder_build_number")}
              hasError={Boolean(errors.build_number?.length)}
            />
            {renderErrors("build_number")}
          </div>

          <div className="flex flex-col gap-1">
            <label htmlFor="run-commit" className="text-11 font-medium text-secondary">
              {t("project_test_case.batches.field_commit_hash")}
            </label>
            <Input
              id="run-commit"
              value={commitHash}
              onChange={(event) => setCommitHash(event.target.value)}
              placeholder={t("project_test_case.batches.placeholder_commit_hash")}
              hasError={Boolean(errors.commit_hash?.length)}
            />
            {renderErrors("commit_hash")}
          </div>
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="run-environment" className="text-11 font-medium text-secondary">
            {t("project_test_case.batches.field_environment")}
          </label>
          <TextArea
            id="run-environment"
            value={environment}
            onChange={(event) => setEnvironment(event.target.value)}
            placeholder={t("project_test_case.batches.placeholder_environment")}
            hasError={Boolean(errors.environment?.length)}
          />
          {renderErrors("environment")}
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="run-executor" className="text-11 font-medium text-secondary">
            {t("project_test_case.batches.field_executor")}
          </label>
          <p
            id="run-executor"
            className="rounded-md border-[0.5px] border-subtle-1 bg-layer-2 px-2.5 py-2 text-13 text-secondary"
          >
            {executorName || t("project_test_case.batches.executor_unknown")}
          </p>
          <p className="text-11 text-tertiary">{t("project_test_case.batches.executor_hint")}</p>
          {renderErrors("executed_by")}
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="run-retest-of" className="text-11 font-medium text-secondary">
            {t("project_test_case.batches.field_retest_of")}
          </label>
          <select
            id="run-retest-of"
            value={retestOf}
            onChange={(event) => setRetestOf(event.target.value)}
            className="rounded-md border-[0.5px] border-subtle-1 bg-layer-2 px-2 py-2 text-13 text-primary focus:outline-none"
          >
            <option value="">{t("project_test_case.batches.retest_of_none")}</option>
            {runs.map((run) => (
              <option key={run.id} value={run.id}>
                {run.number} · {run.run_date}
              </option>
            ))}
          </select>
          <p className="text-11 text-tertiary">{t("project_test_case.batches.retest_of_hint")}</p>
          {renderErrors("retest_of")}
        </div>

        {formError && (
          <p className="text-12 text-danger-primary" role="alert">
            {formError}
          </p>
        )}

        <div className="flex items-center justify-end gap-2">
          <Button variant="secondary" size="lg" onClick={onClose} disabled={isCreating}>
            {t("common.cancel")}
          </Button>
          <Button variant="primary" size="lg" onClick={() => void submit()} loading={isCreating} disabled={!isComplete}>
            {isCreating ? t("project_test_case.batches.creating") : t("project_test_case.batches.create_button")}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
}
