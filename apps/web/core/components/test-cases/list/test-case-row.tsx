/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { Link } from "react-router";
import { useTranslation } from "@plane/i18n";
import { IconButton } from "@plane/propel/icon-button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { AlertModalCore, CustomMenu } from "@plane/ui";
import { CheckSquareOutline, DeleteOutline, EditOutline, MoreHorizontalOutline } from "@makeplane/propel/icons";
import type { ITestCase, ITestCaseErrorResponse, ITestRunSummary } from "@plane/types";
// components
import { TestCasePriorityBadge, TestCaseResultBadge, TestCaseStatusBadge } from "../shared/badges";
// hooks
import { useAppRouter } from "@/hooks/use-app-router";

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
  /** The reader may edit the case — a project member and above. */
  canEdit: boolean;
  /**
   * The reader may delete the case — the endpoint only allows a project
   * admin, so an edit-only reader must not see the delete action at all.
   */
  canDelete: boolean;
  onDelete: (testCase: ITestCase) => Promise<void>;
};

/**
 * The sentence of a refused delete, as the server wrote it. The envelope
 * of the feature carries one message per problem; when it is missing the
 * caller falls back to its own sentence.
 */
const refusalMessage = (refusal: unknown): string | null => {
  if (!refusal || typeof refusal !== "object") return null;

  const envelope = refusal as ITestCaseErrorResponse;
  const entries = Array.isArray(envelope.errors) ? envelope.errors : [];
  const first = entries.find((entry) => typeof entry?.message === "string" && entry.message.trim() !== "");
  if (first) return first.message;

  return typeof envelope.error === "string" && envelope.error.trim() !== "" ? envelope.error : null;
};

/**
 * One row of the list of the cases.
 *
 * The row follows the row of the views page: a tile with the icon of the
 * feature, the title, and the actions at the end of the row. A case
 * carries more metadata than a view does, so the row adds one meta line
 * under the title instead of the trailing columns a table would need —
 * that keeps a row a single line of reading on a phone as well.
 */
export function TestCaseRow(props: Props) {
  const { workspaceSlug, projectId, testCase, lastRun, lastResultUnknown, canEdit, canDelete, onDelete } = props;
  // translation
  const { t } = useTranslation();
  // router
  const router = useAppRouter();
  // states
  const [isDeleteOpen, setIsDeleteOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  const href = `/${workspaceSlug}/projects/${projectId}/test-cases/${testCase.id}`;

  const remove = async () => {
    setIsDeleting(true);
    try {
      await onDelete(testCase);
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("project_test_case.list.delete_error_title"),
        message: refusalMessage(error) ?? t("project_test_case.list.delete_error"),
      });
    } finally {
      setIsDeleting(false);
      setIsDeleteOpen(false);
    }
  };

  return (
    <>
      <div className="group relative flex min-h-[52px] w-full items-center gap-3 border-b border-subtle bg-layer-transparent px-4 py-2.5 text-13 transition-colors hover:bg-layer-transparent-hover">
        <Link to={href} className="flex min-w-0 flex-1 items-center gap-3">
          <span className="grid size-8 shrink-0 place-items-center rounded-md border border-subtle bg-layer-1 text-tertiary">
            <CheckSquareOutline className="size-4" />
          </span>

          <span className="flex min-w-0 flex-1 flex-col gap-1">
            <span className="flex min-w-0 items-center gap-2">
              <span className="font-mono shrink-0 text-11 text-tertiary">{testCase.number}</span>
              <span className="truncate text-13 text-primary">{testCase.title || "—"}</span>
            </span>

            <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-11 text-tertiary">
              <span className="font-mono">{testCase.module_code}</span>
              <TestCasePriorityBadge priority={testCase.priority} />
              <TestCaseStatusBadge status={testCase.status} />
              {lastResultUnknown ? (
                <span className="text-tertiary">—</span>
              ) : (
                <TestCaseResultBadge conclusion={lastRun?.conclusion} />
              )}
              {lastRun && !lastResultUnknown && <span className="text-11 text-tertiary">{lastRun.runNumber}</span>}
              {testCase.issue_links.map((link) => (
                <span
                  key={link.id}
                  className="rounded-sm bg-layer-3 px-1.5 py-0.5 text-10 text-tertiary"
                  title={link.issue_name ?? undefined}
                >
                  {link.issue_identifier}
                </span>
              ))}
            </span>
          </span>
        </Link>

        {(canEdit || canDelete) && (
          <div className="flex shrink-0 items-center">
            <CustomMenu
              customButton={
                <IconButton
                  variant="tertiary"
                  size="lg"
                  icon={MoreHorizontalOutline}
                  aria-label={t("project_test_case.list.row_actions")}
                />
              }
              placement="bottom-end"
              closeOnSelect
            >
              {canEdit && (
                <CustomMenu.MenuItem onClick={() => router.push(href)}>
                  <span className="flex items-center gap-2">
                    <EditOutline className="size-3" />
                    {t("common.edit")}
                  </span>
                </CustomMenu.MenuItem>
              )}
              {canDelete && (
                <CustomMenu.MenuItem onClick={() => setIsDeleteOpen(true)}>
                  <span className="flex items-center gap-2">
                    <DeleteOutline className="size-3" />
                    {t("common.delete")}
                  </span>
                </CustomMenu.MenuItem>
              )}
            </CustomMenu>
          </div>
        )}
      </div>

      {canDelete && (
        <AlertModalCore
          isOpen={isDeleteOpen}
          handleClose={() => setIsDeleteOpen(false)}
          handleSubmit={remove}
          isSubmitting={isDeleting}
          title={t("project_test_case.list.delete_confirm_title")}
          content={t("project_test_case.list.delete_confirm_description")}
          primaryButtonText={{
            default: t("common.delete"),
            loading: t("common.delete"),
          }}
          secondaryButtonText={t("common.cancel")}
        />
      )}
    </>
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
