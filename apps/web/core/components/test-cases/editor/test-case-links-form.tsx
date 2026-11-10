/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { DeleteOutline } from "@makeplane/propel/icons";
import type { ITestCaseIssueLink } from "@plane/types";
// components
import { TestCaseWorkItemPicker } from "../shared/work-item-picker";
// services
import { testCaseService } from "@/services/test-cases/test-case.service";

type Props = {
  workspaceSlug: string;
  projectId: string;
  testCaseId: string;
  links: ITestCaseIssueLink[];
  disabled: boolean;
  onLinksChange: (links: ITestCaseIssueLink[]) => void;
};

/**
 * The work items a case belongs to.
 *
 * The links are written through their own endpoints: the case is not
 * rewritten, so a link never carries the optimistic lock of the header
 * with it.
 */
export function TestCaseLinksForm(props: Props) {
  const { workspaceSlug, projectId, testCaseId, links, disabled, onLinksChange } = props;
  const { t } = useTranslation();
  // states
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [isLinking, setIsLinking] = useState(false);

  const linkedIssueIds = links.map((link) => link.issue_id);

  const linkSelected = async () => {
    if (selectedIds.length === 0) return;
    setIsLinking(true);

    // Every work item gets its own link; one that is refused (it is
    // already linked) does not stop the others.
    const results = await Promise.allSettled(
      selectedIds.map((issueId) => testCaseService.linkIssue(workspaceSlug, projectId, testCaseId, issueId))
    );

    const created = results
      .filter((result): result is PromiseFulfilledResult<ITestCaseIssueLink> => result.status === "fulfilled")
      .map((result) => result.value);
    const failed = results.some((result) => result.status === "rejected");

    setIsLinking(false);
    setSelectedIds([]);

    if (created.length > 0) onLinksChange([...links, ...created]);

    if (failed) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("project_test_case.links.link_error"),
        message: t("project_test_case.links.already_linked"),
      });
    }
  };

  const unlink = async (issueId: string) => {
    try {
      await testCaseService.unlinkIssue(workspaceSlug, projectId, testCaseId, issueId);
      onLinksChange(links.filter((link) => link.issue_id !== issueId));
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("project_test_case.links.unlink_error"),
        message: t("project_test_case.links.unlink_error"),
      });
    }
  };

  return (
    <div className="flex flex-col gap-3">
      {links.length === 0 ? (
        <p className="text-13 text-tertiary">{t("project_test_case.links.empty")}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {links.map((link) => (
            <li
              key={link.id}
              className="flex items-center justify-between gap-3 rounded-md border border-subtle-1 bg-layer-1 px-3 py-2"
            >
              <span className="flex min-w-0 flex-col">
                <span className="truncate text-13 text-primary">{link.issue_name}</span>
                <span className="truncate text-11 text-tertiary">{link.issue_identifier}</span>
              </span>
              <button
                type="button"
                onClick={() => unlink(link.issue_id)}
                disabled={disabled}
                title={t("project_test_case.links.unlink")}
                aria-label={t("project_test_case.links.unlink")}
                className="shrink-0 rounded-sm p-1 text-tertiary hover:bg-layer-2 hover:text-danger-primary disabled:cursor-not-allowed disabled:opacity-40"
              >
                <DeleteOutline className="size-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}

      {!disabled && (
        <div className="flex flex-col gap-2">
          <TestCaseWorkItemPicker
            workspaceSlug={workspaceSlug}
            projectId={projectId}
            linkedIssueIds={linkedIssueIds}
            selectedIds={selectedIds}
            onSelectionChange={setSelectedIds}
          />
          <Button
            variant="secondary"
            size="lg"
            onClick={linkSelected}
            disabled={selectedIds.length === 0 || isLinking}
            loading={isLinking}
            className="self-start"
          >
            {isLinking ? t("project_test_case.links.linking") : t("project_test_case.links.link_selected")}
          </Button>
        </div>
      )}
    </div>
  );
}
