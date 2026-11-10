/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { useTranslation } from "@plane/i18n";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { AlertModalCore, Collapsible } from "@plane/ui";
import { ChevronDownIcon, ChevronRightIcon } from "@plane/propel/icons";
import type { ITestCaseErrorResponse, ITestRunCase } from "@plane/types";
// components
import { TestCaseResultBadge } from "../shared/badges";
import { TestCaseRunCaseForm } from "./run-case-form";
// helpers
import type { TRunCaseErrors } from "./types";
// services
import { testCaseService } from "@/services/test-cases/test-case.service";

type Props = {
  workspaceSlug: string;
  projectId: string;
  testRunId: string;
  runCase: ITestRunCase;
  disabled: boolean;
  /** The reader may take the case out of the batch (a member and above). */
  canRemove: boolean;
  errors?: TRunCaseErrors;
  onChanged: () => void;
};

/**
 * One case inside a batch, folded into a card.
 *
 * A batch of a dozen cases would otherwise be a page of a dozen full forms —
 * unusable on a phone. The card shows the number, the title, the conclusion
 * and the verdict tally, and opens into the execution form.
 *
 * A card that the server reported a problem about opens by itself: the
 * reason of a refused finalize has to be visible next to the field it names,
 * not hidden behind a fold.
 */
export function TestCaseRunCaseCard(props: Props) {
  const { workspaceSlug, projectId, testRunId, runCase, disabled, canRemove, errors, onChanged } = props;
  const { t } = useTranslation();
  // states
  const [isOpen, setIsOpen] = useState(false);
  const [isRemoveOpen, setIsRemoveOpen] = useState(false);
  const [isRemoving, setIsRemoving] = useState(false);

  const hasErrors = Boolean(Object.keys(errors?.fields ?? {}).length || Object.keys(errors?.steps ?? {}).length);

  useEffect(() => {
    if (hasErrors) setIsOpen(true);
  }, [hasErrors]);

  const passCount = runCase.steps.filter((step) => step.conclusion === "pass").length;
  const failCount = runCase.steps.filter((step) => step.conclusion === "fail").length;
  const unsetCount = runCase.steps.length - passCount - failCount;

  const remove = async () => {
    setIsRemoving(true);
    try {
      await testCaseService.removeRunCase(workspaceSlug, projectId, testRunId, runCase.id);
      setIsRemoveOpen(false);
      onChanged();
    } catch (error) {
      // A record that is already gone answers 404 (the service keeps the
      // status next to the body) — that is the outcome the caller asked for,
      // not a failure worth a toast. Every other refusal (a frozen batch, a
      // record that already carries a verdict, a missing permission) carries
      // its own sentence, and that sentence is what the toast shows.
      const envelope = error as (ITestCaseErrorResponse & { status?: number }) | undefined;
      if (envelope?.status === 404) {
        setIsRemoveOpen(false);
        onChanged();
        return;
      }
      const sentence =
        envelope?.errors?.find((entry) => entry?.message)?.message ||
        envelope?.error ||
        t("project_test_case.execution.case.remove_failed");
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("project_test_case.execution.case.remove_failed"),
        message: sentence,
      });
    } finally {
      setIsRemoving(false);
    }
  };

  return (
    <>
      <div
        className={["rounded-md border bg-layer-1", hasErrors ? "border-danger-strong" : "border-subtle-1"].join(" ")}
      >
        <Collapsible
          isOpen={isOpen}
          onToggle={() => setIsOpen((current) => !current)}
          buttonClassName="w-full"
          title={
            <div className="flex min-w-0 flex-wrap items-center gap-2 px-3 py-2.5 text-left">
              {isOpen ? (
                <ChevronDownIcon className="size-3.5 shrink-0 text-tertiary" />
              ) : (
                <ChevronRightIcon className="size-3.5 shrink-0 text-tertiary" />
              )}
              <span className="font-mono text-11 text-tertiary">{runCase.case_number}</span>
              <span className="min-w-0 flex-1 truncate text-13 text-primary">{runCase.case_title || "—"}</span>
              <span className="text-11 text-tertiary">
                {t("project_test_case.execution.case.verdict_tally", {
                  pass: passCount,
                  fail: failCount,
                  unset: unsetCount,
                })}
              </span>
              {runCase.conclusion ? (
                <TestCaseResultBadge conclusion={runCase.conclusion} />
              ) : (
                <span className="text-11 text-tertiary">{t("project_test_case.execution.case.not_concluded")}</span>
              )}
            </div>
          }
        >
          <div className="border-t border-subtle-1 px-3 py-4">
            <TestCaseRunCaseForm
              workspaceSlug={workspaceSlug}
              projectId={projectId}
              testRunId={testRunId}
              runCase={runCase}
              disabled={disabled}
              errors={errors}
              onChanged={onChanged}
              footer={
                canRemove ? (
                  <button
                    type="button"
                    onClick={() => setIsRemoveOpen(true)}
                    className="rounded-md border border-subtle-1 px-2.5 py-1.5 text-12 text-tertiary hover:border-danger-strong hover:text-danger-primary"
                  >
                    {t("project_test_case.execution.case.remove")}
                  </button>
                ) : null
              }
            />
          </div>
        </Collapsible>
      </div>

      {canRemove && (
        <AlertModalCore
          isOpen={isRemoveOpen}
          handleClose={() => setIsRemoveOpen(false)}
          handleSubmit={() => void remove()}
          isSubmitting={isRemoving}
          title={t("project_test_case.execution.case.remove_confirm_title", { number: runCase.case_number })}
          content={t("project_test_case.execution.case.remove_confirm_content")}
          primaryButtonText={{
            default: t("project_test_case.execution.case.remove"),
            loading: t("project_test_case.execution.case.remove"),
          }}
          secondaryButtonText={t("common.cancel")}
        />
      )}
    </>
  );
}
