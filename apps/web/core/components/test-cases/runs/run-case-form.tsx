/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { AlertModalCore } from "@plane/ui";
import { EFileAssetType } from "@plane/types";
import type { ITestRunCase, TTestRunConclusion } from "@plane/types";
// components
import { TestCaseResultBadge, TestCasePriorityBadge } from "../shared/badges";
import { TestCaseSaveIndicator } from "../shared/save-indicator";
import type { TTestCaseSaveState } from "../shared/save-indicator";
import { TestCaseRunAttachmentList } from "./attachments/attachment-list";
import { TestCaseRunAttachmentUploader } from "./attachments/attachment-uploader";
import type { TTestCaseRunAttachmentUploaderHandle } from "./attachments/attachment-uploader";
import { TestCaseMarkdownEditor } from "./markdown-editor";
import type { TTestCaseMarkdownEditorHandle } from "./markdown-editor";
import { TestCaseRunStepRow } from "./run-step-row";
// constants
import { TEST_RUN_AUTOSAVE_DEBOUNCE_MS, TEST_RUN_CONCLUSIONS } from "./constants";
// helpers
import { changedCaseFields, draftFromRunCase, parseRunErrors } from "./types";
import type { TRunCaseDraft, TRunCaseErrors, TRunCaseTextField } from "./types";
// services
import { testCaseService } from "@/services/test-cases/test-case.service";

type Props = {
  workspaceSlug: string;
  projectId: string;
  testRunId: string;
  runCase: ITestRunCase;
  /** A completed batch is frozen — the server refuses every write on it. */
  disabled: boolean;
  /** The reasons of a refused finalize, addressed to this case. */
  errors?: TRunCaseErrors;
  /** The batch changed on the server — the caller refreshes it. */
  onChanged: () => void;
  /** Extra control rendered in the footer (the removal of the case). */
  footer?: ReactNode;
};

/** The label key of one conclusion. */
const conclusionKey = (conclusion: TTestRunConclusion): string =>
  conclusion === "N/A" ? "na" : conclusion.toLowerCase();

/**
 * The execution form of one case inside a batch.
 *
 * Three kinds of write live here and they differ in what they mean:
 *
 * * the **text fields** (the measured values, the evidence, the remarks, the
 *   summary and the two justifications) are saved while they are typed;
 * * the **conclusion** is an explicit act — a state is chosen, the button is
 *   pressed, the dialog is confirmed — and it is written once (§6.1 rule 4);
 * * the **verdicts** of the steps belong to the steps themselves.
 *
 * The form never decides on its own what is valid: it shows the reasons the
 * server returned next to the field or the step they name. The one check that
 * runs locally is the ✘ guard of a step (a ✘ with nothing observed is an
 * invalid record) — and it is the step row that owns it.
 */
export function TestCaseRunCaseForm(props: Props) {
  const { workspaceSlug, projectId, testRunId, runCase, disabled, errors, onChanged, footer } = props;
  const { t } = useTranslation();
  // states
  const [draft, setDraft] = useState<TRunCaseDraft>(() => draftFromRunCase(runCase));
  const [saveState, setSaveState] = useState<TTestCaseSaveState>("idle");
  const [localErrors, setLocalErrors] = useState<Record<string, string[]>>({});
  const [chosenConclusion, setChosenConclusion] = useState<TTestRunConclusion>(runCase.conclusion ?? "");
  const [isConfirmingConclusion, setIsConfirmingConclusion] = useState(false);
  const [isWritingConclusion, setIsWritingConclusion] = useState(false);
  // refs
  const savedRef = useRef<TRunCaseDraft>(draftFromRunCase(runCase));
  const initializedForRef = useRef<string>(runCase.id);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inFlightRef = useRef(false);
  const evidenceEditorRef = useRef<TTestCaseMarkdownEditorHandle | null>(null);
  const evidenceUploaderRef = useRef<TTestCaseRunAttachmentUploaderHandle | null>(null);
  const unmountedRef = useRef(false);
  const draftRef = useRef<TRunCaseDraft>(draft);
  draftRef.current = draft;

  // The draft is filled once per case record: a save must not overwrite what
  // the tester is typing right now.
  useEffect(() => {
    if (initializedForRef.current === runCase.id) return;
    initializedForRef.current = runCase.id;
    const next = draftFromRunCase(runCase);
    savedRef.current = next;
    setDraft(next);
  }, [runCase]);

  const save = useCallback(async () => {
    if (disabled) return;

    const changed = changedCaseFields(savedRef.current, draftRef.current);
    if (Object.keys(changed).length === 0) return;
    if (inFlightRef.current) return;

    inFlightRef.current = true;
    if (!unmountedRef.current) {
      setSaveState("saving");
      setLocalErrors({});
    }

    try {
      const updated = await testCaseService.updateRunCase(workspaceSlug, projectId, testRunId, runCase.id, changed);
      savedRef.current = draftFromRunCase(updated);
      if (!unmountedRef.current) setSaveState("saved");
      onChanged();
    } catch (error) {
      if (unmountedRef.current) return;
      const parsed = parseRunErrors(error as never);
      const caseErrors = parsed.cases[runCase.case_number]?.fields ?? {};
      setLocalErrors({
        ...caseErrors,
        ...(parsed.form.length > 0 ? { __form__: parsed.form } : {}),
      });
      setSaveState("error");
    } finally {
      inFlightRef.current = false;
    }
  }, [disabled, onChanged, projectId, runCase.case_number, runCase.id, testRunId, workspaceSlug]);

  const saveRef = useRef(save);
  saveRef.current = save;

  // Folding the card unmounts the form; the fields typed in the last moment
  // before that still have to reach the server.
  useEffect(
    () => () => {
      unmountedRef.current = true;
      void saveRef.current();
    },
    []
  );

  const flush = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    void saveRef.current();
  }, []);

  // The pause after which the typed fields go out.
  useEffect(() => {
    if (disabled) return;
    if (Object.keys(changedCaseFields(savedRef.current, draft)).length === 0) return;

    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      void saveRef.current();
    }, TEST_RUN_AUTOSAVE_DEBOUNCE_MS);

    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [disabled, draft]);

  const setField = (field: TRunCaseTextField, value: string) => {
    setDraft((current) => ({ ...current, [field]: value }));
    setSaveState("idle");
  };

  const conclusion = (runCase.conclusion ?? "") as TTestRunConclusion;
  const isConcluded = conclusion !== "";

  const writeConclusion = async () => {
    if (!chosenConclusion) return;

    setIsWritingConclusion(true);
    setLocalErrors({});
    try {
      // The typed fields go first: the conclusion is a statement about them,
      // and the two writes are separate rows.
      if (Object.keys(changedCaseFields(savedRef.current, draftRef.current)).length > 0) flush();

      await testCaseService.updateRunCase(workspaceSlug, projectId, testRunId, runCase.id, {
        conclusion: chosenConclusion,
      });
      setIsConfirmingConclusion(false);
      onChanged();
    } catch (error) {
      const parsed = parseRunErrors(error as never);
      setLocalErrors(parsed.cases[runCase.case_number]?.fields ?? {});
    } finally {
      setIsWritingConclusion(false);
    }
  };

  const fieldErrors = useMemo(() => {
    const merged: Record<string, string[]> = {};
    for (const [field, messages] of Object.entries(errors?.fields ?? {})) merged[field] = [...messages];
    for (const [field, messages] of Object.entries(localErrors)) {
      merged[field] = [...(merged[field] ?? []), ...messages];
    }
    return merged;
  }, [errors, localErrors]);

  // The fields this form renders inline. A reason that names another field
  // (a duplicate number, a rule about the record as a whole) still has to be
  // read somewhere, so the rest is collected at the bottom of the form.
  const inlineFields = new Set([
    "evidence",
    "remarks",
    "actual_result_summary",
    "na_reason",
    "na_criteria_source",
    "manual_executor",
    "manual_condition",
    "conclusion",
    "__case__",
  ]);
  const orphanErrors = Object.entries(fieldErrors)
    .filter(([field]) => !inlineFields.has(field))
    .flatMap(([, messages]) => messages);

  const renderErrors = (field: string) =>
    fieldErrors[field]?.length > 0 ? (
      <ul className="flex flex-col gap-0.5">
        {fieldErrors[field].map((message) => (
          <li key={message} className="text-11 text-danger-primary">
            {message}
          </li>
        ))}
      </ul>
    ) : null;

  const conclusionChoices: { value: TTestRunConclusion; label: string }[] = TEST_RUN_CONCLUSIONS.map((value) => ({
    value,
    label: t(`project_test_case.last_result.${conclusionKey(value)}`),
  }));

  return (
    <div className="flex flex-col gap-5">
      {/* --- the snapshot of the case header --------------------------- */}
      <section className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-11 text-tertiary">{runCase.case_number}</span>
          <TestCasePriorityBadge priority={runCase.case_priority} />
          <span className="text-13 font-medium text-primary">{runCase.case_title || "—"}</span>
        </div>
        <dl className="grid grid-cols-1 gap-2 lg:grid-cols-2">
          <div className="flex min-w-0 flex-col gap-0.5">
            <dt className="text-11 font-medium text-tertiary">{t("project_test_case.execution.case.purpose")}</dt>
            <dd className="text-12 break-words whitespace-pre-wrap text-secondary">{runCase.case_purpose || "—"}</dd>
          </div>
          <div className="flex min-w-0 flex-col gap-0.5">
            <dt className="text-11 font-medium text-tertiary">{t("project_test_case.execution.case.overview")}</dt>
            <dd className="text-12 break-words whitespace-pre-wrap text-secondary">
              {runCase.case_object_overview || "—"}
            </dd>
          </div>
          <div className="flex min-w-0 flex-col gap-0.5">
            <dt className="text-11 font-medium text-tertiary">{t("project_test_case.execution.case.preconditions")}</dt>
            <dd className="text-12 break-words whitespace-pre-wrap text-secondary">
              {runCase.case_preconditions || "—"}
            </dd>
          </div>
          <div className="flex min-w-0 flex-col gap-0.5">
            <dt className="text-11 font-medium text-tertiary">{t("project_test_case.execution.case.environment")}</dt>
            <dd className="text-12 break-words whitespace-pre-wrap text-secondary">
              {runCase.case_environment || "—"}
            </dd>
          </div>
        </dl>
      </section>

      {fieldErrors.__case__ && renderErrors("__case__")}

      {/* --- the steps -------------------------------------------------- */}
      <section className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-12 font-semibold text-primary">
            {t("project_test_case.execution.case.steps", { count: runCase.steps.length })}
          </h3>
          <p className="text-11 text-tertiary">{t("project_test_case.execution.case.steps_hint")}</p>
        </div>
        {runCase.steps.length === 0 ? (
          <p className="rounded-md border border-dashed border-subtle-1 bg-layer-2/40 px-3 py-4 text-12 text-tertiary">
            {t("project_test_case.execution.case.no_steps")}
          </p>
        ) : (
          <ul className="flex flex-col gap-3">
            {runCase.steps.map((step) => (
              <TestCaseRunStepRow
                key={step.id}
                workspaceSlug={workspaceSlug}
                projectId={projectId}
                testRunId={testRunId}
                runCaseId={runCase.id}
                caseNumber={runCase.case_number}
                step={step}
                disabled={disabled}
                errors={errors?.steps?.[step.sequence_id]}
                onChanged={onChanged}
              />
            ))}
          </ul>
        )}
      </section>

      {/* --- the conclusion -------------------------------------------- */}
      <section className="flex flex-col gap-2 rounded-md border border-subtle-1 bg-layer-1 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-12 font-semibold text-primary">{t("project_test_case.execution.case.conclusion")}</h3>
          <div className="flex items-center gap-2">
            <TestCaseSaveIndicator state={saveState} />
            {isConcluded && <TestCaseResultBadge conclusion={conclusion} />}
          </div>
        </div>

        {isConcluded ? (
          <>
            <p className="text-11 text-tertiary">{t("project_test_case.execution.case.conclusion_locked")}</p>
            {renderErrors("conclusion")}
          </>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-1.5">
              {conclusionChoices.map((choice) => (
                <button
                  key={choice.value}
                  type="button"
                  disabled={disabled}
                  onClick={() => setChosenConclusion(choice.value)}
                  aria-pressed={chosenConclusion === choice.value}
                  className={[
                    "rounded-md border px-2.5 py-1.5 text-12 transition-colors disabled:cursor-not-allowed disabled:opacity-40",
                    chosenConclusion === choice.value
                      ? "border-accent-strong bg-accent-primary/10 text-accent-primary"
                      : "border-subtle-1 text-secondary hover:bg-layer-2",
                  ].join(" ")}
                >
                  {choice.label}
                </button>
              ))}
            </div>
            <p className="text-11 text-tertiary">{t("project_test_case.execution.case.conclusion_hint")}</p>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="primary"
                size="lg"
                disabled={disabled || !chosenConclusion}
                onClick={() => setIsConfirmingConclusion(true)}
              >
                {t("project_test_case.execution.case.conclude")}
              </Button>
              {renderErrors("conclusion")}
            </div>
          </>
        )}

        {(chosenConclusion === "N/A" || conclusion === "N/A") && (
          <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
            <div className="flex flex-col gap-1">
              <TestCaseMarkdownEditor
                id={`na-reason-${runCase.id}`}
                label={t("project_test_case.execution.case.na_reason")}
                value={draft.na_reason}
                onChange={(value) => setField("na_reason", value)}
                onBlur={flush}
                disabled={disabled}
                rows={2}
                hasError={Boolean(fieldErrors.na_reason?.length)}
              />
              {renderErrors("na_reason")}
            </div>
            <div className="flex flex-col gap-1">
              <TestCaseMarkdownEditor
                id={`na-source-${runCase.id}`}
                label={t("project_test_case.execution.case.na_criteria_source")}
                value={draft.na_criteria_source}
                onChange={(value) => setField("na_criteria_source", value)}
                onBlur={flush}
                disabled={disabled}
                rows={2}
                hasError={Boolean(fieldErrors.na_criteria_source?.length)}
              />
              {renderErrors("na_criteria_source")}
            </div>
          </div>
        )}

        {(chosenConclusion === "MANUAL" || conclusion === "MANUAL") && (
          <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
            <div className="flex flex-col gap-1">
              <TestCaseMarkdownEditor
                id={`manual-executor-${runCase.id}`}
                label={t("project_test_case.execution.case.manual_executor")}
                value={draft.manual_executor}
                onChange={(value) => setField("manual_executor", value)}
                onBlur={flush}
                disabled={disabled}
                rows={2}
                hasError={Boolean(fieldErrors.manual_executor?.length)}
              />
              {renderErrors("manual_executor")}
            </div>
            <div className="flex flex-col gap-1">
              <TestCaseMarkdownEditor
                id={`manual-condition-${runCase.id}`}
                label={t("project_test_case.execution.case.manual_condition")}
                value={draft.manual_condition}
                onChange={(value) => setField("manual_condition", value)}
                onBlur={flush}
                disabled={disabled}
                rows={2}
                hasError={Boolean(fieldErrors.manual_condition?.length)}
              />
              {renderErrors("manual_condition")}
            </div>
          </div>
        )}
      </section>

      {/* --- the evidence, the summary and the remarks ----------------- */}
      <section className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div className="flex min-w-0 flex-col gap-2">
          <TestCaseMarkdownEditor
            ref={evidenceEditorRef}
            id={`evidence-${runCase.id}`}
            label={t("project_test_case.execution.case.evidence")}
            value={draft.evidence}
            onChange={(value) => setField("evidence", value)}
            onBlur={flush}
            disabled={disabled}
            placeholder={t("project_test_case.execution.case.evidence_placeholder")}
            hasError={Boolean(fieldErrors.evidence?.length)}
            onRequestImage={() => evidenceUploaderRef.current?.openPicker()}
          />
          {renderErrors("evidence")}
          <TestCaseRunAttachmentUploader
            ref={evidenceUploaderRef}
            workspaceSlug={workspaceSlug}
            entityType={EFileAssetType.TEST_RUN_CASE_EVIDENCE}
            entityIdentifier={runCase.id}
            disabled={disabled}
            compact
            onUploaded={onChanged}
            onUploadedMarkdown={(url) => evidenceEditorRef.current?.insertAtCursor(`\n![](${url})\n`)}
          />
          <TestCaseRunAttachmentList
            attachments={runCase.attachments ?? []}
            disabled={disabled}
            onRemove={async (assetId) => {
              await testCaseService.deleteRunCaseAttachment(workspaceSlug, projectId, testRunId, runCase.id, assetId);
              onChanged();
            }}
          />
        </div>

        <div className="flex min-w-0 flex-col gap-3">
          <div className="flex flex-col gap-1">
            <TestCaseMarkdownEditor
              id={`summary-${runCase.id}`}
              label={t("project_test_case.execution.case.actual_result_summary")}
              value={draft.actual_result_summary}
              onChange={(value) => setField("actual_result_summary", value)}
              onBlur={flush}
              disabled={disabled}
              rows={2}
              hasError={Boolean(fieldErrors.actual_result_summary?.length)}
            />
            {renderErrors("actual_result_summary")}
          </div>
          <div className="flex flex-col gap-1">
            <TestCaseMarkdownEditor
              id={`remarks-${runCase.id}`}
              label={t("project_test_case.execution.case.remarks")}
              value={draft.remarks}
              onChange={(value) => setField("remarks", value)}
              onBlur={flush}
              disabled={disabled}
              rows={2}
              hasError={Boolean(fieldErrors.remarks?.length)}
            />
            {renderErrors("remarks")}
          </div>
        </div>
      </section>

      {orphanErrors.length > 0 && (
        <ul className="flex flex-col gap-0.5">
          {orphanErrors.map((message) => (
            <li key={message} className="text-11 text-danger-primary">
              {message}
            </li>
          ))}
        </ul>
      )}

      {footer && <div className="flex flex-wrap items-center justify-end gap-2">{footer}</div>}

      <AlertModalCore
        isOpen={isConfirmingConclusion}
        handleClose={() => setIsConfirmingConclusion(false)}
        handleSubmit={() => void writeConclusion()}
        isSubmitting={isWritingConclusion}
        title={t("project_test_case.execution.case.confirm_conclusion_title")}
        content={t("project_test_case.execution.case.confirm_conclusion_content", {
          conclusion: chosenConclusion ? t(`project_test_case.last_result.${conclusionKey(chosenConclusion)}`) : "",
        })}
        primaryButtonText={{
          default: t("project_test_case.execution.case.conclude"),
          loading: t("project_test_case.execution.case.concluding"),
        }}
        secondaryButtonText={t("common.cancel")}
      />
    </div>
  );
}
