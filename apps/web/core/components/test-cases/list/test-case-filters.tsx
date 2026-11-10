/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useTranslation } from "@plane/i18n";
import { CloseOutline } from "@makeplane/propel/icons";
import type { ISearchIssueResponse } from "@plane/types";
// components
import { FilterOption } from "@/components/issues/issue-layouts/filters";
import { TestCaseWorkItemPicker } from "../shared/work-item-picker";
// constants
import { TEST_CASE_PRIORITIES } from "../constants";
// hooks
import { useTestCaseListFilters } from "./test-case-list-filters";

/** The identifier of a work item — the part of its name the list shows. */
const workItemLabel = (issue: ISearchIssueResponse) =>
  issue.project__identifier ? `${issue.project__identifier}-${issue.sequence_id} · ${issue.name}` : issue.name;

/** The three states the list filter offers — "all" is the unset one. */
const STATUS_OPTIONS = ["active", "void", "all"] as const;

/**
 * The filters of the list of the cases, as the body of the filter button:
 * the module, the priority, the state and the linked work item.
 *
 * The panel mirrors the filter panel of the views page: one section per
 * filter, one checkable row per value.
 */
export function TestCaseFilterPanel() {
  const { t } = useTranslation();
  // store
  const { workspaceSlug, projectId, modules, filters, workItem, hasFilters, updateFilters, setWorkItem, clearFilters } =
    useTestCaseListFilters();

  // derived values
  const moduleId = filters.module ?? "";
  const priority = filters.priority ?? "";
  const status = filters.status ?? "active";

  return (
    <div className="flex h-full w-full flex-col overflow-hidden">
      <div className="flex items-center justify-between gap-2 bg-surface-1 px-2.5 pt-2.5 pb-2">
        <span className="text-11 text-tertiary">{t("common.filters")}</span>
        {hasFilters && (
          <button type="button" className="text-11 text-accent-primary hover:underline" onClick={clearFilters}>
            {t("common.clear_all")}
          </button>
        )}
      </div>

      <div className="vertical-scrollbar scrollbar-sm h-full w-full divide-y divide-subtle-1 overflow-y-auto px-2.5">
        <section className="py-2">
          <h6 className="px-1.5 pb-1 text-11 text-tertiary">{t("project_test_case.list.filter_module")}</h6>
          <FilterOption
            isChecked={!moduleId}
            multiple={false}
            onClick={() => updateFilters({ module: "" })}
            title={t("project_test_case.list.all_modules")}
          />
          {modules.map((module) => (
            <FilterOption
              key={module.id}
              isChecked={moduleId === module.id}
              multiple={false}
              onClick={() => updateFilters({ module: moduleId === module.id ? "" : module.id })}
              title={module.code}
            />
          ))}
        </section>

        <section className="py-2">
          <h6 className="px-1.5 pb-1 text-11 text-tertiary">{t("project_test_case.list.filter_priority")}</h6>
          <FilterOption
            isChecked={!priority}
            multiple={false}
            onClick={() => updateFilters({ priority: "" })}
            title={t("project_test_case.list.all_priorities")}
          />
          {TEST_CASE_PRIORITIES.map((option) => (
            <FilterOption
              key={option}
              isChecked={priority === option}
              multiple={false}
              onClick={() => updateFilters({ priority: priority === option ? "" : option })}
              title={option}
            />
          ))}
        </section>

        <section className="py-2">
          <h6 className="px-1.5 pb-1 text-11 text-tertiary">{t("project_test_case.list.filter_status")}</h6>
          {STATUS_OPTIONS.map((option) => (
            <FilterOption
              key={option}
              isChecked={status === option}
              multiple={false}
              onClick={() => updateFilters({ status: option })}
              title={
                option === "all" ? t("project_test_case.list.all_statuses") : t(`project_test_case.status.${option}`)
              }
            />
          ))}
        </section>

        <section className="flex flex-col gap-2 py-2">
          <h6 className="px-1.5 text-11 text-tertiary">{t("project_test_case.list.filter_issue")}</h6>
          {workItem && (
            <div className="flex items-center justify-between gap-2 rounded-sm bg-layer-1 px-2 py-1.5 text-11 text-secondary">
              <span className="truncate">{workItem.label}</span>
              <button
                type="button"
                className="grid place-items-center text-tertiary hover:text-secondary"
                aria-label={t("common.remove")}
                onClick={() => setWorkItem(null)}
              >
                <CloseOutline height={10} width={10} />
              </button>
            </div>
          )}
          <TestCaseWorkItemPicker
            workspaceSlug={workspaceSlug}
            projectId={projectId}
            selectedIds={filters.issue ? [filters.issue] : []}
            multiple={false}
            onSelectionChange={(ids) => {
              if (ids.length === 0) setWorkItem(null);
            }}
            onPick={(issue) => setWorkItem(issue ? { id: issue.id, label: workItemLabel(issue) } : null)}
          />
        </section>
      </div>
    </div>
  );
}
