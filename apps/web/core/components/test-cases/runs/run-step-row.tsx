/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "@plane/i18n";
import { AlertModalCore } from "@plane/ui";
import { EFileAssetType } from "@plane/types";
import type { ITestRunStepRecord } from "@plane/types";
// components
import { TestCaseSaveIndicator } from "../shared/save-indicator";
import type { TTestCaseSaveState } from "../shared/save-indicator";
import { TestCaseRunAttachmentList } from "./attachments/attachment-list";
import { TestCaseRunAttachmentUploader } from "./attachments/attachment-uploader";
import type { TTestCaseRunAttachmentUploaderHandle } from "./attachments/attachment-uploader";
import { TestCaseMarkdownEditor } from "./markdown-editor";
import type { TTestCaseMarkdownEditorHandle } from "./markdown-editor";
// constants
import { TEST_RUN_AUTOSAVE_DEBOUNCE_MS } from "./constants";
// helpers
import { markdownText, missingMeasurement, parseRunErrors } from "./types";
import type { TRunFieldErrors } from "./types";
// services
import { testCaseService } from "@/services/test-cases/test-case.service";

/** The two verdicts a tester may mark. There is no third act. */
type TVerdict = "pass" | "fail";

type Props = {
  workspaceSlug: string;
  projectId: string;
  testRunId: string;
  runCaseId: string;
  caseNumber: string;
  step: ITestRunStepRecord;
  disabled: boolean;
  errors?: TRunFieldErrors;
  /** The batch changed on the server — the caller refreshes it. */
  onChanged: () => void;
};

/**
 * One step of the execution form.
 *
 * Two writes live here, and they are not the same kind of act:
 *
 * * the **verdict** (✔/✘) is written once. It asks for a confirmation, goes
 *   out immediately, and the two buttons disappear afterwards — a wrong
 *   verdict is corrected by voiding the record and running the case in a new
 *   batch, never by swapping the box (STD-SW-003 §6.1, M63 ruling);
 * * the **measured value** is the observation and stays writable. It is
 *   saved while it is typed, after a pause, with the save state next to it.
 *
 * A ✘ without a measured value is an invalid record (§4.4 rule 1), so the ✘
 * guard refuses to open the confirmation while the step has neither text nor
 * an attached screenshot.
 */
export function TestCaseRunStepRow(props: Props) {
  const { workspaceSlug, projectId, testRunId, runCaseId, caseNumber, step, disabled, errors, onChanged } = props;
  const { t } = useTranslation();
  // states
  const [value, setValue] = useState(step.actual_value ?? "");
  const [saveState, setSaveState] = useState<TTestCaseSaveState>("idle");
  const [localError, setLocalError] = useState<string | null>(null);
  const [pendingVerdict, setPendingVerdict] = useState<TVerdict | null>(null);
  const [isWritingVerdict, setIsWritingVerdict] = useState(false);
  // refs
  const savedRef = useRef(step.actual_value ?? "");
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inFlightRef = useRef(false);
  const editorRef = useRef<TTestCaseMarkdownEditorHandle | null>(null);
  const uploaderRef = useRef<TTestCaseRunAttachmentUploaderHandle | null>(null);
  const unmountedRef = useRef(false);
  const valueRef = useRef(value);
  valueRef.current = value;

  // A refresh of the batch replaces the record; a value that is being typed
  // must not be overwritten by the answer to its own write.
  useEffect(() => {
    if (inFlightRef.current) return;
    if (step.actual_value === savedRef.current) return;
    savedRef.current = step.actual_value ?? "";
    setValue(step.actual_value ?? "");
  }, [step.actual_value]);

  const save = useCallback(
    async (nextValue: string) => {
      if (disabled) return;
      if (nextValue === savedRef.current) return;

      inFlightRef.current = true;
      setSaveState("saving");
      setLocalError(null);

      try {
        const updated = await testCaseService.updateRunStep(
          workspaceSlug,
          projectId,
          testRunId,
          runCaseId,
          step.sequence_id,
          {
            actual_value: nextValue,
          }
        );
        savedRef.current = updated.actual_value ?? nextValue;
        setSaveState("saved");
        onChanged();
      } catch (error) {
        const parsed = parseRunErrors(error as never);
        const stepErrors = parsed.cases[caseNumber]?.steps[step.sequence_id] ?? {};
        const first = Object.values(stepErrors)[0]?.[0] ?? parsed.form[0];
        setLocalError(first ?? t("project_test_case.execution.step.save_failed"));
        setSaveState("error");
      } finally {
        inFlightRef.current = false;
      }
    },
    [caseNumber, disabled, onChanged, projectId, runCaseId, step.sequence_id, t, testRunId, workspaceSlug]
  );

  const saveRef = useRef(save);
  saveRef.current = save;

  // Folding the card unmounts the row; a value typed in the last moment
  // before that still has to reach the server. The latest text is read from
  // a ref, so the effect runs once for the lifetime of the row.
  useEffect(
    () => () => {
      unmountedRef.current = true;
      void saveRef.current(valueRef.current);
    },
    []
  );

  const flush = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    void saveRef.current(value);
  }, [value]);

  useEffect(() => {
    if (disabled) return;
    if (value === savedRef.current) return;

    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      void saveRef.current(value);
    }, TEST_RUN_AUTOSAVE_DEBOUNCE_MS);

    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [disabled, value]);

  const verdict = step.conclusion as string;
  const isWritten = verdict === "pass" || verdict === "fail";

  const askVerdict = (next: TVerdict) => {
    setLocalError(null);

    // §4.4 rule 1 / §6.1 rule 1: a failed step with nothing observed is an
    // invalid record — the measured value is its text or its screenshot.
    if (next === "fail" && missingMeasurement({ actual_value: value, attachments: step.attachments })) {
      setLocalError(t("project_test_case.execution.step.fail_needs_measurement"));
      return;
    }

    // The typed text has to be on the server before the verdict that
    // describes it; the two writes are separate rows.
    if (value !== savedRef.current) flush();
    setPendingVerdict(next);
  };

  const writeVerdict = async () => {
    if (!pendingVerdict) return;

    setIsWritingVerdict(true);
    setLocalError(null);
    try {
      await testCaseService.updateRunStep(workspaceSlug, projectId, testRunId, runCaseId, step.sequence_id, {
        conclusion: pendingVerdict,
      });
      setPendingVerdict(null);
      onChanged();
    } catch (error) {
      const parsed = parseRunErrors(error as never);
      const stepErrors = parsed.cases[caseNumber]?.steps[step.sequence_id] ?? {};
      const first = Object.values(stepErrors)[0]?.[0] ?? parsed.form[0];
      setLocalError(first ?? t("project_test_case.execution.step.verdict_failed"));
    } finally {
      setIsWritingVerdict(false);
    }
  };

  const needsMeasurement = verdict === "fail" && !markdownText(value) && (step.attachments ?? []).length === 0;
  // Every reason the server addresses to this step is shown here — the
  // finalize rules can name the expected result (`expected_is_unjudgeable`,
  // §5.6) just as they name the measured value or the verdict.
  const fieldErrors = [
    ...(errors?.action ?? []),
    ...(errors?.expected_result ?? []),
    ...(errors?.actual_value ?? []),
    ...(errors?.conclusion ?? []),
  ];

  return (
    <li className="flex flex-col gap-3 rounded-md border border-subtle-1 bg-layer-1 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-11 font-medium text-secondary">
          {t("project_test_case.execution.step.title", { number: step.sequence_id })}
        </span>
        <div className="flex items-center gap-2">
          <TestCaseSaveIndicator state={saveState} />
          {verdict === "pass" && (
            <span className="rounded-sm bg-success-subtle px-1.5 py-0.5 text-11 text-success-primary">
              {t("project_test_case.execution.step.pass_written")}
            </span>
          )}
          {verdict === "fail" && (
            <span className="rounded-sm bg-danger-subtle px-1.5 py-0.5 text-11 text-danger-primary">
              {t("project_test_case.execution.step.fail_written")}
            </span>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
        <div className="flex min-w-0 flex-col gap-1">
          <span className="text-11 font-medium text-tertiary">{t("project_test_case.execution.step.action")}</span>
          <p className="text-12 break-words whitespace-pre-wrap text-secondary">{step.action_snapshot || "—"}</p>
        </div>
        <div className="flex min-w-0 flex-col gap-1">
          <span className="text-11 font-medium text-tertiary">{t("project_test_case.execution.step.expected")}</span>
          <p className="text-12 break-words whitespace-pre-wrap text-secondary">
            {step.expected_result_snapshot || "—"}
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-11 font-medium text-secondary">{t("project_test_case.execution.step.verdict")}</span>
        {isWritten ? (
          <p className="text-11 text-tertiary">
            {verdict === "pass"
              ? t("project_test_case.execution.step.verdict_pass_locked")
              : t("project_test_case.execution.step.verdict_fail_locked")}
          </p>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={disabled}
              onClick={() => askVerdict("pass")}
              className="rounded-md border border-success-strong px-2.5 py-1.5 text-12 text-success-primary hover:bg-success-subtle disabled:cursor-not-allowed disabled:opacity-40"
            >
              {t("project_test_case.execution.step.mark_pass")}
            </button>
            <button
              type="button"
              disabled={disabled}
              onClick={() => askVerdict("fail")}
              className="rounded-md border border-danger-strong px-2.5 py-1.5 text-12 text-danger-primary hover:bg-danger-subtle disabled:cursor-not-allowed disabled:opacity-40"
            >
              {t("project_test_case.execution.step.mark_fail")}
            </button>
            <span className="text-11 text-tertiary">{t("project_test_case.execution.step.verdict_hint")}</span>
          </div>
        )}
      </div>

      <div className="flex flex-col gap-2">
        <TestCaseMarkdownEditor
          ref={editorRef}
          id={`step-actual-${step.id}`}
          label={t("project_test_case.execution.step.actual_value")}
          value={value}
          onChange={(next) => {
            setValue(next);
            setSaveState("idle");
          }}
          onBlur={flush}
          disabled={disabled}
          placeholder={t("project_test_case.execution.step.actual_placeholder")}
          hasError={needsMeasurement || Boolean(errors?.actual_value?.length)}
          onRequestImage={() => uploaderRef.current?.openPicker()}
        />

        {needsMeasurement && (
          <p className="text-11 text-danger-primary">{t("project_test_case.execution.step.fail_needs_measurement")}</p>
        )}

        <TestCaseRunAttachmentUploader
          ref={uploaderRef}
          workspaceSlug={workspaceSlug}
          entityType={EFileAssetType.TEST_RUN_STEP_RECORD}
          entityIdentifier={step.id}
          disabled={disabled}
          compact
          onUploaded={onChanged}
          onUploadedMarkdown={(url) => editorRef.current?.insertAtCursor(`\n![](${url})\n`)}
        />

        <TestCaseRunAttachmentList
          attachments={step.attachments ?? []}
          disabled={disabled}
          onRemove={async (assetId) => {
            await testCaseService.deleteRunStepAttachment(
              workspaceSlug,
              projectId,
              testRunId,
              runCaseId,
              step.sequence_id,
              assetId
            );
            onChanged();
          }}
        />
      </div>

      {fieldErrors.length > 0 && (
        <ul className="flex flex-col gap-0.5">
          {fieldErrors.map((message) => (
            <li key={message} className="text-11 text-danger-primary">
              {message}
            </li>
          ))}
        </ul>
      )}

      {localError && (
        <p className="text-11 text-danger-primary" role="alert">
          {localError}
        </p>
      )}

      <AlertModalCore
        isOpen={pendingVerdict !== null}
        handleClose={() => setPendingVerdict(null)}
        handleSubmit={() => void writeVerdict()}
        isSubmitting={isWritingVerdict}
        title={t("project_test_case.execution.step.confirm_verdict_title", { number: step.sequence_id })}
        content={
          pendingVerdict === "pass"
            ? t("project_test_case.execution.step.confirm_verdict_pass")
            : t("project_test_case.execution.step.confirm_verdict_fail")
        }
        primaryButtonText={{
          default: t("project_test_case.execution.step.confirm_verdict_submit"),
          loading: t("project_test_case.execution.step.confirm_verdict_submit"),
        }}
        secondaryButtonText={t("common.cancel")}
      />
    </li>
  );
}
