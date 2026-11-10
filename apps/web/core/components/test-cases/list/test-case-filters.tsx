/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { useTranslation } from "@plane/i18n";
import { CustomSelect } from "@plane/ui";
import type { ITestCaseModule } from "@plane/types";
import { cn } from "@plane/utils";
// components
import { TestCaseWorkItemPicker } from "../shared/work-item-picker";
// constants
import { TEST_CASE_PRIORITIES, hasTestCaseFilters } from "../constants";

type Props = {
  workspaceSlug: string;
  projectId: string;
  modules: ITestCaseModule[];
  module: string;
  priority: string;
  issue: string;
  status: string;
  search: string;
  onChange: (patch: { module?: string; priority?: string; issue?: string; status?: string; search?: string }) => void;
  onClear: () => void;
};

/**
 * The filters of the list of the cases: the module, the priority, the
 * linked work item, the status and the search term.
 *
 * The bar wraps on a narrow screen — every filter keeps its own line
 * instead of the bar scrolling sideways.
 */
export function TestCaseFilters(props: Props) {
  const { workspaceSlug, projectId, modules, module, priority, issue, status, search, onChange, onClear } = props;
  const { t } = useTranslation();
  // state — the work item filter is a picker of its own, it stays closed
  // until the user asks for it.
  const [isIssuePickerOpen, setIsIssuePickerOpen] = useState(false);

  // The same definition the list uses — the bar offers "clear the
  // filters" exactly when there is something to clear.
  const hasFilters = hasTestCaseFilters({ module, priority, issue, status, search });

  return (
    <div className="flex flex-col gap-3 border-b border-subtle-1 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="text"
          value={search}
          onChange={(event) => onChange({ search: event.target.value })}
          placeholder={t("project_test_case.list.search_placeholder")}
          className="placeholder-tertiary min-w-40 flex-1 rounded-md border border-subtle-1 bg-layer-2 px-3 py-1.5 text-13 text-primary focus:ring-1 focus:ring-accent-strong focus:outline-none"
        />

        <CustomSelect
          value={module}
          onChange={(value: string) => onChange({ module: value })}
          label={
            <span className={module ? "text-primary" : "text-tertiary"}>
              {module
                ? (modules.find((entry) => entry.id === module)?.code ?? t("project_test_case.list.all_modules"))
                : t("project_test_case.list.filter_module")}
            </span>
          }
          className="w-40"
        >
          <CustomSelect.Option value="">{t("project_test_case.list.all_modules")}</CustomSelect.Option>
          {modules.map((entry) => (
            <CustomSelect.Option key={entry.id} value={entry.id}>
              {entry.code}
            </CustomSelect.Option>
          ))}
        </CustomSelect>

        <CustomSelect
          value={priority}
          onChange={(value: string) => onChange({ priority: value })}
          label={
            <span className={priority ? "text-primary" : "text-tertiary"}>
              {priority || t("project_test_case.list.filter_priority")}
            </span>
          }
          className="w-36"
        >
          <CustomSelect.Option value="">{t("project_test_case.list.all_priorities")}</CustomSelect.Option>
          {TEST_CASE_PRIORITIES.map((entry) => (
            <CustomSelect.Option key={entry} value={entry}>
              {entry}
            </CustomSelect.Option>
          ))}
        </CustomSelect>

        <CustomSelect
          value={status}
          onChange={(value: string) => onChange({ status: value })}
          label={
            <span className={status && status !== "active" ? "text-primary" : "text-tertiary"}>
              {status && status !== "active"
                ? t(`project_test_case.status.${status}`)
                : t("project_test_case.list.filter_status")}
            </span>
          }
          className="w-36"
        >
          <CustomSelect.Option value="active">{t("project_test_case.status.active")}</CustomSelect.Option>
          <CustomSelect.Option value="void">{t("project_test_case.status.void")}</CustomSelect.Option>
          <CustomSelect.Option value="all">{t("project_test_case.list.all_statuses")}</CustomSelect.Option>
        </CustomSelect>

        <button
          type="button"
          onClick={() => setIsIssuePickerOpen((open) => !open)}
          className={cn(
            "rounded-md border border-subtle-1 px-3 py-1.5 text-13",
            issue ? "text-primary" : "text-tertiary"
          )}
        >
          {issue ? t("project_test_case.list.filter_issue") : t("project_test_case.list.select_work_item")}
        </button>

        {hasFilters && (
          <button
            type="button"
            onClick={onClear}
            className="rounded-md px-3 py-1.5 text-13 text-accent-primary hover:underline"
          >
            {t("project_test_case.list.clear_filters")}
          </button>
        )}
      </div>

      {isIssuePickerOpen && (
        <div className="max-w-md">
          <TestCaseWorkItemPicker
            workspaceSlug={workspaceSlug}
            projectId={projectId}
            selectedIds={issue ? [issue] : []}
            multiple={false}
            onSelectionChange={(ids) => {
              onChange({ issue: ids[0] ?? "" });
              setIsIssuePickerOpen(false);
            }}
          />
        </div>
      )}
    </div>
  );
}
