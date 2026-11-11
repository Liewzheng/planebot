/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { AddOutline, DeleteOutline } from "@makeplane/propel/icons";
import useSWR from "swr";
import type { ITestCase, ITestCaseErrorResponse, ITestCaseIssueLink, ITestCaseModule } from "@plane/types";
import { AlertModalCore } from "@plane/ui";
// hooks
import { useAppRouter } from "@/hooks/use-app-router";
// components
import { TestCaseSaveIndicator } from "../shared/save-indicator";
import { TestCaseConflictModal } from "./test-case-conflict-modal";
import { TestCaseHeaderForm } from "./test-case-header-form";
import { TestCaseModuleModal } from "./test-case-module-modal";
import { TestCasePropertiesRail } from "./test-case-properties-rail";
import { refusalNamesRailField, unreadableReasons } from "./test-case-refusals";
import { TestCaseStepsForm } from "./test-case-steps-form";
// constants
import { TEST_CASE_AUTOSAVE_DEBOUNCE_MS, TEST_CASE_DETAIL, TEST_CASE_LINKS, TEST_CASE_MODULES } from "../constants";
// types
import { draftFromCase, draftKey, isHeaderComplete, isStepTableComplete, newStepKey } from "../types";
import type { TTestCaseDraft, TTestCaseFieldErrors, TTestCaseStepErrors } from "../types";
// services
import { testCaseService } from "@/services/test-cases/test-case.service";

type Props = {
  workspaceSlug: string;
  projectId: string;
  /** The case that is edited — without it the editor creates a case. */
  testCaseId?: string;
  canEdit: boolean;
  /**
   * The reader may delete the case — the delete endpoint only allows a
   * project admin, so this is a wider floor than `canEdit`. It defaults
   * to false: a caller that has not worked out the role must not offer
   * the action.
   */
  canDelete?: boolean;
  /** Called after a case was created — the route moves to the new case. */
  onCreated?: (testCase: ITestCase) => void;
  onDeleted?: () => void;
};

/**
 * The editor of a test case.
 *
 * The record is written to the server while it is edited: a change is sent
 * after a pause of 800 ms and when the field it was made in loses the
 * focus. The server holds the truth — it refuses a record that misses a
 * mandatory item and it refuses a write that is based on an outdated
 * version of the record. Both refusals are shown, the first one next to
 * the field it belongs to, the second one in a dialog that asks which of
 * the two versions wins.
 */
export function TestCaseEditor(props: Props) {
  const { workspaceSlug, projectId, testCaseId, canEdit, canDelete = false, onCreated, onDeleted } = props;
  const { t } = useTranslation();
  // router
  const router = useAppRouter();
  // states
  const [draft, setDraft] = useState<TTestCaseDraft | null>(null);
  const [fieldErrors, setFieldErrors] = useState<TTestCaseFieldErrors>({});
  const [stepErrors, setStepErrors] = useState<TTestCaseStepErrors>({});
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "conflict" | "error">("idle");
  const [conflict, setConflict] = useState<ITestCase | null>(null);
  const [isModuleModalOpen, setIsModuleModalOpen] = useState(false);
  const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  // The rail is a column of the page on a wide screen. On a narrow one it is a
  // drawer over the content, so it starts closed there — and the reading
  // column keeps its width instead of being squeezed. A case being created
  // keeps it open even there: the module and the priority are mandatory items
  // and both of them live in the rail.
  const [isRailOpen, setIsRailOpen] = useState(() => !testCaseId || window.innerWidth >= 768);
  // refs
  const draftRef = useRef<TTestCaseDraft | null>(null);
  const versionRef = useRef<string | undefined>(undefined);
  const savedKeyRef = useRef<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inFlightRef = useRef(false);
  const pendingRef = useRef(false);
  const pendingForceRef = useRef(false);
  const saveRef = useRef<((force?: boolean) => Promise<void>) | null>(null);
  const initializedForRef = useRef<string | null>(null);

  // ------------------------------------------------------------------
  // The data of the server
  // ------------------------------------------------------------------

  const { data: modules, mutate: mutateModules } = useSWR(
    workspaceSlug && projectId ? TEST_CASE_MODULES(workspaceSlug, projectId) : null,
    () => testCaseService.getModules(workspaceSlug, projectId)
  );

  const {
    data: testCase,
    error: testCaseError,
    isLoading: isLoadingCase,
    mutate: mutateCase,
  } = useSWR(testCaseId ? TEST_CASE_DETAIL(workspaceSlug, projectId, testCaseId) : null, () =>
    testCaseService.getCase(workspaceSlug, projectId, testCaseId as string)
  );

  const { data: links, mutate: mutateLinks } = useSWR(
    testCaseId ? TEST_CASE_LINKS(workspaceSlug, projectId, testCaseId) : null,
    () => testCaseService.getIssueLinks(workspaceSlug, projectId, testCaseId as string)
  );

  // The draft is filled once per case: a save must not overwrite what the
  // user is typing right now.
  useEffect(() => {
    if (!testCase) return;
    if (initializedForRef.current === testCase.id) return;
    initializedForRef.current = testCase.id;

    const next = draftFromCase(testCase);
    draftRef.current = next;
    setDraft(next);
    savedKeyRef.current = draftKey(next);
    versionRef.current = testCase.updated_at;
  }, [testCase]);

  // A case that does not exist yet has no record to read — the editor
  // starts with an empty header and the first step of the table.
  useEffect(() => {
    if (testCaseId) return;
    if (draftRef.current) return;

    const next: TTestCaseDraft = {
      module: "",
      title: "",
      purpose: "",
      object_overview: "",
      priority: "",
      preconditions: "",
      environment: "",
      status: "active",
      steps: [{ key: newStepKey(), action: "", expected_result: "" }],
    };
    draftRef.current = next;
    setDraft(next);
  }, [testCaseId]);

  // ------------------------------------------------------------------
  // The write
  // ------------------------------------------------------------------

  /** The reasons of a refused write, addressed by the field they belong to. */
  const applyErrors = useCallback((envelope?: ITestCaseErrorResponse): TTestCaseFieldErrors => {
    const nextFields: TTestCaseFieldErrors = {};
    const nextSteps: TTestCaseStepErrors = {};

    for (const entry of envelope?.errors ?? []) {
      const message = entry.message || entry.detail || envelope?.error || "";
      const path = entry.path ?? "";
      const field = entry.field ?? "";
      const stepMatch = path.match(/steps\[(\d+)\]/);

      if (stepMatch) {
        const index = Number(stepMatch[1]) - 1;
        const stepField = path.split(".").pop() ?? field;
        nextSteps[index] = { ...nextSteps[index], [stepField]: message };
        continue;
      }

      if (typeof entry.step_sequence === "number") {
        const index = entry.step_sequence - 1;
        nextSteps[index] = { ...nextSteps[index], [field]: message };
        continue;
      }

      nextFields[field || "__form__"] = [...(nextFields[field || "__form__"] ?? []), message];
    }

    setFieldErrors(nextFields);
    setStepErrors(nextSteps);

    return nextFields;
  }, []);

  /**
   * The write of the case.
   *
   * `force` writes the draft even when it did not change since the last
   * successful write. The conflict dialog needs it: the user decides
   * there that *their* version wins, and that decision has to reach the
   * server even when the content is the same as the saved one.
   */
  const save = useCallback(
    async (force = false) => {
      if (!testCaseId || !canEdit) return;

      const current = draftRef.current;
      if (!current) return;

      // Nothing changed since the last successful write. The server
      // moves the version of the record on every write it accepts, so a
      // write that changes nothing would make the next person conflict
      // with a record nobody touched.
      if (!force && savedKeyRef.current === draftKey(current)) return;

      if (inFlightRef.current) {
        // A write is already on its way — the next one is sent as soon as
        // this one is through, so the versions cannot overtake each other.
        pendingRef.current = true;
        pendingForceRef.current = pendingForceRef.current || force;
        return;
      }

      inFlightRef.current = true;
      setSaveState("saving");

      const sentKey = draftKey(current);

      try {
        const updated = await testCaseService.updateCase(workspaceSlug, projectId, testCaseId, {
          module: current.module,
          title: current.title,
          purpose: current.purpose,
          object_overview: current.object_overview,
          priority: current.priority,
          preconditions: current.preconditions,
          environment: current.environment,
          status: current.status,
          steps: current.steps.map((step) => ({
            action: step.action,
            expected_result: step.expected_result,
          })),
          updated_at: versionRef.current,
        });

        versionRef.current = updated.updated_at;
        savedKeyRef.current = sentKey;
        setFieldErrors({});
        setStepErrors({});
        setSaveState("saved");
        void mutateCase(updated, { revalidate: false });
      } catch (error) {
        const envelope = error as ITestCaseErrorResponse | undefined;

        if (envelope?.code === "conflict" && envelope.current) {
          setConflict(envelope.current);
          setSaveState("conflict");
        } else {
          const refused = applyErrors(envelope);
          setSaveState("error");
          // A reason named for a field of the rail is read next to its row, so
          // the rail has to be on screen for it — it is closed on a narrow
          // viewport, and a reader may have collapsed it on a wide one.
          if (refusalNamesRailField(refused)) setIsRailOpen(true);
        }
      } finally {
        inFlightRef.current = false;
        if (pendingRef.current) {
          pendingRef.current = false;
          const forceNext = pendingForceRef.current;
          pendingForceRef.current = false;
          void saveRef.current?.(forceNext);
        }
      }
    },
    [workspaceSlug, projectId, testCaseId, canEdit, applyErrors, mutateCase]
  );

  saveRef.current = save;

  /** The write goes out now — the field lost the focus. */
  const flush = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    void saveRef.current?.();
  }, []);

  // The pause after which a change is written to the server.
  useEffect(() => {
    if (!testCaseId || !canEdit || !draft) return;
    const key = draftKey(draft);
    if (savedKeyRef.current === null || savedKeyRef.current === key) return;

    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      void saveRef.current?.();
    }, TEST_CASE_AUTOSAVE_DEBOUNCE_MS);

    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [draft, testCaseId, canEdit]);

  // ------------------------------------------------------------------
  // The changes of the editor
  // ------------------------------------------------------------------

  const applyPatch = useCallback((patch: Partial<TTestCaseDraft>) => {
    const current = draftRef.current;
    if (!current) return;
    const next = { ...current, ...patch };
    draftRef.current = next;
    setDraft(next);
  }, []);

  // ------------------------------------------------------------------
  // The conflict
  // ------------------------------------------------------------------

  const keepMine = useCallback(() => {
    if (!conflict) return;
    // The version of the server is the one the write is based on now, so
    // the write goes through — the user asked for it. The write is
    // forced: the content of the draft may be the same as the content
    // that is already saved, and the decision of the user still has to
    // reach the server.
    versionRef.current = conflict.updated_at;
    setConflict(null);
    void saveRef.current?.(true);
  }, [conflict]);

  const takeTheirs = useCallback(() => {
    if (!conflict) return;
    const next = draftFromCase(conflict);
    draftRef.current = next;
    setDraft(next);
    savedKeyRef.current = draftKey(next);
    versionRef.current = conflict.updated_at;
    setFieldErrors({});
    setStepErrors({});
    setConflict(null);
    setSaveState("saved");
    void mutateCase(conflict, { revalidate: false });
  }, [conflict, mutateCase]);

  /**
   * The user closed the dialog without choosing. The write did not land
   * and the draft still holds the change, so the state says that — it
   * must not say "all changes are saved".
   */
  const cancelConflict = useCallback(() => {
    setConflict(null);
    setSaveState("error");
  }, []);

  // ------------------------------------------------------------------
  // The creation of a case
  // ------------------------------------------------------------------

  const create = useCallback(async () => {
    const current = draftRef.current;
    if (!current) return;

    setIsCreating(true);
    setFieldErrors({});
    setStepErrors({});

    try {
      const created = await testCaseService.createCase(workspaceSlug, projectId, {
        module: current.module,
        title: current.title,
        purpose: current.purpose,
        object_overview: current.object_overview,
        priority: current.priority,
        preconditions: current.preconditions,
        environment: current.environment,
        status: current.status,
        steps: current.steps.map((step) => ({
          action: step.action,
          expected_result: step.expected_result,
        })),
      });

      onCreated?.(created);
    } catch (error) {
      const envelope = error as ITestCaseErrorResponse | undefined;
      const reasons = envelope?.errors ?? [];

      const refused = applyErrors(envelope);
      setSaveState("error");
      if (refusalNamesRailField(refused)) setIsRailOpen(true);

      // A refusal that names a field speaks through it: the sentence of the
      // server stands next to the field it belongs to, and the reading column
      // lists the ones the reader cannot see there right now (see
      // `unreadableReasons`). A refusal without such a sentence (the request
      // did not reach the server) has nothing to show inline — that one is
      // worth a toast, and the toast says what it was.
      if (reasons.length === 0) {
        setToast({
          type: TOAST_TYPE.ERROR,
          title: t("project_test_case.editor.save_failed"),
          message: envelope?.error || t("project_test_case.editor.create_failed_no_reason"),
        });
      }
    } finally {
      setIsCreating(false);
    }
  }, [workspaceSlug, projectId, applyErrors, onCreated, t]);

  const remove = useCallback(async () => {
    if (!testCaseId || !canDelete) return;
    setIsDeleting(true);
    try {
      await testCaseService.deleteCase(workspaceSlug, projectId, testCaseId);
      onDeleted?.();
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("project_test_case.editor.delete_error"),
        message: t("project_test_case.editor.delete_error"),
      });
    } finally {
      setIsDeleting(false);
      setIsDeleteModalOpen(false);
    }
  }, [workspaceSlug, projectId, testCaseId, canDelete, onDeleted, t]);

  // ------------------------------------------------------------------
  // The view
  // ------------------------------------------------------------------

  const moduleList = useMemo<ITestCaseModule[]>(() => modules ?? [], [modules]);

  const headerComplete = draft ? isHeaderComplete(draft) : false;
  const stepsComplete = draft ? isStepTableComplete(draft) : false;

  if (testCaseId && isLoadingCase && !draft)
    return <div className="p-4 text-13 text-tertiary">{t("project_test_case.list.loading")}</div>;

  if (testCaseId && testCaseError && !draft)
    return (
      <div className="flex flex-col gap-2 p-4">
        <p className="text-13 text-danger-primary">{t("project_test_case.editor.not_found")}</p>
        <p className="text-11 text-tertiary">{t("project_test_case.editor.load_failed")}</p>
      </div>
    );

  if (!draft) return null;

  const isCreateMode = !testCaseId;

  // What the reader cannot see next to a control right now: the reasons of the
  // rail while it is closed, and the reasons of a field that neither the
  // column nor the rail renders. They are listed in the column instead, so a
  // refused write always ends in something visible — the create path has no
  // save indicator at all.
  const railFieldLabels: Record<string, string> = {
    module: t("project_test_case.editor.field_module"),
    priority: t("project_test_case.editor.field_priority"),
    status: t("project_test_case.editor.field_status"),
  };
  const unreadable = unreadableReasons(fieldErrors, isRailOpen);

  // The shell of the work-item detail: a reading column and a rail of
  // properties on its right. The column scrolls on its own, the rail does not
  // move with it.
  return (
    <div className="flex h-full w-full overflow-hidden">
      <div className="h-full w-full space-y-6 overflow-y-auto px-9 py-5">
        {/* `max-w-3xl` caps the measure of the reading column — the prose of a
            case is read line by line, not scanned across the window. */}
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 pb-24">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => router.back()}
                className="text-11 text-tertiary hover:text-secondary"
              >
                ← {t("project_test_case.editor.back")}
              </button>
              {!isCreateMode && <TestCaseSaveIndicator state={saveState} />}
            </div>

            <div className="flex items-center gap-2">
              {!isRailOpen && (
                <Button
                  variant="secondary"
                  size="lg"
                  onClick={() => setIsRailOpen(true)}
                  aria-controls="test-case-properties"
                  aria-expanded={false}
                >
                  {t("project_test_case.editor.sidebar_show")}
                </Button>
              )}
              {isCreateMode ? (
                <Button
                  variant="primary"
                  size="lg"
                  onClick={create}
                  loading={isCreating}
                  disabled={!canEdit || !headerComplete || !stepsComplete}
                  prependIcon={<AddOutline className="size-3.5" />}
                >
                  {isCreating ? t("project_test_case.editor.creating") : t("project_test_case.editor.create_button")}
                </Button>
              ) : (
                canDelete && (
                  <Button
                    variant="error-outline"
                    size="lg"
                    onClick={() => setIsDeleteModalOpen(true)}
                    prependIcon={<DeleteOutline className="size-3.5" />}
                  >
                    {t("project_test_case.editor.delete_button")}
                  </Button>
                )
              )}
            </div>
          </div>

          {!canEdit && (
            <p className="rounded-md border border-subtle-1 bg-layer-2 px-3 py-2 text-12 text-secondary">
              {t("project_test_case.editor.read_only")}
            </p>
          )}

          {fieldErrors.__form__ && (
            <ul className="flex flex-col gap-0.5">
              {fieldErrors.__form__.map((error) => (
                <li key={error} className="text-11 text-danger-primary">
                  {error}
                </li>
              ))}
            </ul>
          )}

          {unreadable.length > 0 && (
            <div role="status" className="flex flex-col gap-1 rounded-md border border-subtle-1 bg-layer-2 px-3 py-2">
              <p className="text-11 font-medium text-secondary">{t("project_test_case.editor.refusal_summary")}</p>
              <ul className="flex flex-col gap-0.5">
                {unreadable.map(({ field, message }) => (
                  <li key={`${field}:${message}`} className="text-11 text-danger-primary">
                    {railFieldLabels[field] ? `${railFieldLabels[field]} — ${message}` : message}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {isCreateMode && (
            <p className="text-11 font-medium text-tertiary">{t("project_test_case.editor.create_title")}</p>
          )}

          <TestCaseHeaderForm
            draft={draft}
            errors={fieldErrors}
            disabled={!canEdit}
            onChange={applyPatch}
            onBlur={flush}
          />

          <section className="flex flex-col gap-3">
            <h2 className="text-13 font-semibold text-primary">{t("project_test_case.editor.section_steps")}</h2>
            <TestCaseStepsForm
              draft={draft}
              errors={stepErrors}
              disabled={!canEdit}
              onChange={applyPatch}
              onBlur={flush}
            />
            {fieldErrors.steps && (
              <ul className="flex flex-col gap-0.5">
                {fieldErrors.steps.map((error) => (
                  <li key={error} className="text-11 text-danger-primary">
                    {error}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>

      {isRailOpen && (
        <TestCasePropertiesRail
          workspaceSlug={workspaceSlug}
          projectId={projectId}
          testCase={testCase}
          draft={draft}
          modules={moduleList}
          links={links ?? []}
          errors={fieldErrors}
          disabled={!canEdit}
          onChange={applyPatch}
          onCreateModule={() => setIsModuleModalOpen(true)}
          onLinksChange={(nextLinks: ITestCaseIssueLink[]) => void mutateLinks(nextLinks, { revalidate: false })}
          onCollapse={() => setIsRailOpen(false)}
        />
      )}

      <TestCaseConflictModal
        isOpen={Boolean(conflict)}
        current={conflict}
        draft={draft}
        modules={moduleList}
        onKeepMine={keepMine}
        onTakeTheirs={takeTheirs}
        onClose={cancelConflict}
      />

      <TestCaseModuleModal
        isOpen={isModuleModalOpen}
        workspaceSlug={workspaceSlug}
        projectId={projectId}
        onClose={() => setIsModuleModalOpen(false)}
        onCreated={(module: ITestCaseModule) => {
          // oxlint-disable-next-line unicorn/no-array-sort
          const sorted = [...moduleList, module].sort((a, b) => a.code.localeCompare(b.code));
          void mutateModules(sorted, { revalidate: false });
          applyPatch({ module: module.id });
        }}
      />

      {/* The confirm dialog only exists for a reader who may delete: with the
          flag false there is no button to open it and no path around it. */}
      {canDelete && (
        <AlertModalCore
          isOpen={isDeleteModalOpen}
          handleClose={() => setIsDeleteModalOpen(false)}
          handleSubmit={remove}
          isSubmitting={isDeleting}
          title={t("project_test_case.editor.delete_confirm_title")}
          content={t("project_test_case.editor.delete_confirm_description")}
          primaryButtonText={{
            default: t("project_test_case.editor.delete_button"),
            loading: t("project_test_case.editor.delete_button"),
          }}
          secondaryButtonText={t("common.cancel")}
        />
      )}
    </div>
  );
}
