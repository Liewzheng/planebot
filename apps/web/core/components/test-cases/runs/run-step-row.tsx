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
import { firstStepReason, missingMeasurement, parseRunErrors, refusalFallback } from "./types";
import type { TRunFieldErrors, TRunRefusal } from "./types";
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
  /** The batch is completed — its record is frozen, so no verdict is offered. */
  frozen: boolean;
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
  const { workspaceSlug, projectId, testRunId, runCaseId, caseNumber, step, disabled, frozen, errors, onChanged } =
    props;
  const { t } = useTranslation();
  // states
  const [value, setValue] = useState(step.actual_value ?? "");
  const [saveState, setSaveState] = useState<TTestCaseSaveState>("idle");
  const [localError, setLocalError] = useState<string | null>(null);
  const [pendingVerdict, setPendingVerdict] = useState<TVerdict | null>(null);
  const [isWritingVerdict, setIsWritingVerdict] = useState(false);
  const [verdictError, setVerdictError] = useState<string | null>(null);
  // refs
  const savedRef = useRef(step.actual_value ?? "");
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inFlightPromiseRef = useRef<Promise<void> | null>(null);
  const editorRef = useRef<TTestCaseMarkdownEditorHandle | null>(null);
  const uploaderRef = useRef<TTestCaseRunAttachmentUploaderHandle | null>(null);
  const unmountedRef = useRef(false);
  const valueRef = useRef(value);
  valueRef.current = value;

  // A refresh of the batch replaces the record; a value that is being typed
  // must not be overwritten by the answer to its own write.
  useEffect(() => {
    if (inFlightPromiseRef.current) return;
    if (step.actual_value === savedRef.current) return;
    savedRef.current = step.actual_value ?? "";
    setValue(step.actual_value ?? "");
  }, [step.actual_value]);

  const save = useCallback(
    async (nextValue: string): Promise<void> => {
      if (disabled) return;

      // A write is already on its way: wait it out, then send what is still
      // unsaved, so a caller that awaits `flush()` really sees the text on the
      // server before it writes the verdict about that text.
      if (inFlightPromiseRef.current) await inFlightPromiseRef.current;
      if (nextValue === savedRef.current) return;

      if (!unmountedRef.current) {
        setSaveState("saving");
        setLocalError(null);
      }

      const write = (async () => {
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
          if (!unmountedRef.current) setSaveState("saved");
          onChanged();
        } catch (error) {
          if (unmountedRef.current) return;
          // The refusal names the field, not the step, so the batch-level
          // entries, DRF's `detail` and — last — the HTTP status are read too.
          const refusal = error as TRunRefusal;
          const reason = firstStepReason(
            parseRunErrors(refusal),
            refusal,
            caseNumber,
            step.sequence_id,
            refusalFallback(refusal, t, "project_test_case.execution.errors.write_not_saved")
          );
          setLocalError(reason ?? t("project_test_case.execution.step.save_failed"));
          setSaveState("error");
        }
      })();

      const tracked = write.finally(() => {
        if (inFlightPromiseRef.current === tracked) inFlightPromiseRef.current = null;
      });
      inFlightPromiseRef.current = tracked;
      await tracked;
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

  /** Send the typed value now, and hand back the write so callers can await it. */
  const flush = useCallback((): Promise<void> => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    return saveRef.current(value);
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
    setVerdictError(null);

    // §4.4 rule 1 / §6.1 rule 1: a failed step with nothing observed is an
    // invalid record — the measured value is its text or its screenshot.
    if (next === "fail" && missingMeasurement({ actual_value: value, attachments: step.attachments })) {
      setLocalError(t("project_test_case.execution.step.fail_needs_measurement"));
      return;
    }

    // The typed text has to be on the server before the verdict that
    // describes it; `writeVerdict` awaits the flush, so the order is real.
    setPendingVerdict(next);
  };

  const writeVerdict = async () => {
    if (!pendingVerdict) return;

    setIsWritingVerdict(true);
    setLocalError(null);
    try {
      await flush();

      await testCaseService.updateRunStep(workspaceSlug, projectId, testRunId, runCaseId, step.sequence_id, {
        conclusion: pendingVerdict,
      });
      setPendingVerdict(null);
      onChanged();
    } catch (error) {
      // `_assert_verdict_is_final` names the field only; DRF's `detail` and
      // the HTTP status close the chain. A verdict that was not written must
      // never close the dialog as if it had been.
      const refusal = error as TRunRefusal;
      const reason = firstStepReason(
        parseRunErrors(refusal),
        refusal,
        caseNumber,
        step.sequence_id,
        refusalFallback(refusal, t, "project_test_case.execution.errors.verdict_not_written")
      );
      const sentence = reason ?? t("project_test_case.execution.step.verdict_failed");
      setLocalError(sentence);
      // The dialog stays open and carries the reason itself; the sentence also
      // stays next to the verdict once it is closed.
      setVerdictError(sentence);
    } finally {
      setIsWritingVerdict(false);
    }
  };

  // One predicate for the guard and the hint — a second copy could disagree
  // with the rule it is meant to show.
  const needsMeasurement =
    verdict === "fail" && missingMeasurement({ actual_value: value, attachments: step.attachments });
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
        ) : frozen ? (
          /* A frozen batch offers no verdict — the same rule the batch header
             and the conclusion follow: absent, not merely disabled. */
          <p className="text-11 text-tertiary">{t("project_test_case.execution.step.verdict_frozen")}</p>
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

        {/* The reason of a refused verdict stands with the verdict: it used to
            sit at the bottom of the row, below the editor and the uploader,
            where a phone viewport can hide it. */}
        {localError && (
          <p className="text-11 text-danger-primary" role="alert">
            {localError}
          </p>
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
          {[...new Set(fieldErrors)].map((message) => (
            <li key={message} className="text-11 text-danger-primary">
              {message}
            </li>
          ))}
        </ul>
      )}

      <AlertModalCore
        isOpen={pendingVerdict !== null}
        handleClose={() => {
          setPendingVerdict(null);
          setVerdictError(null);
        }}
        handleSubmit={() => void writeVerdict()}
        isSubmitting={isWritingVerdict}
        title={t("project_test_case.execution.step.confirm_verdict_title", { number: step.sequence_id })}
        content={
          <div className="flex flex-col gap-2">
            <p>
              {pendingVerdict === "pass"
                ? t("project_test_case.execution.step.confirm_verdict_pass")
                : t("project_test_case.execution.step.confirm_verdict_fail")}
            </p>
            {verdictError && (
              <p className="text-12 font-medium text-danger-primary" role="alert">
                {verdictError}
              </p>
            )}
          </div>
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
