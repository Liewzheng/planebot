/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useTranslation } from "@plane/i18n";
import { PillButton } from "@makeplane/propel/components/pill";
import { CloseOutline } from "@makeplane/propel/icons";
import type { TTestCaseFilters } from "@plane/types";
// hooks
import { useTestCaseListFilters } from "./test-case-list-filters";

type TChip = {
  key: keyof TTestCaseFilters;
  label: string;
  value: string;
};

/**
 * The filters the list is showing, as chips above the list. One chip per
 * filter, removable on its own, plus the button that clears them all —
 * the same interaction as the applied filters of the views page.
 */
export function TestCaseAppliedFiltersList() {
  const { t } = useTranslation();
  // store
  const { filters, workItem, modules, hasFilters, removeFilter, clearFilters } = useTestCaseListFilters();

  if (!hasFilters) return null;

  const statusValue = filters.status ?? "";

  const chips: TChip[] = [];

  if (filters.module) {
    chips.push({
      key: "module",
      label: t("project_test_case.list.filter_module"),
      value: modules.find((module) => module.id === filters.module)?.code ?? filters.module,
    });
  }
  if (filters.priority) {
    chips.push({ key: "priority", label: t("project_test_case.list.filter_priority"), value: filters.priority });
  }
  if (statusValue && statusValue !== "active") {
    chips.push({
      key: "status",
      label: t("project_test_case.list.filter_status"),
      value:
        statusValue === "all" ? t("project_test_case.list.all_statuses") : t(`project_test_case.status.${statusValue}`),
    });
  }
  if (filters.issue) {
    chips.push({
      key: "issue",
      label: t("project_test_case.list.filter_issue"),
      value: workItem?.label ?? t("project_test_case.list.filter_issue"),
    });
  }
  if (filters.search) {
    chips.push({ key: "search", label: t("common.search.label"), value: filters.search });
  }

  return (
    <div className="flex flex-wrap items-stretch gap-2 bg-surface-1">
      {chips.map((chip) => (
        <div
          key={chip.key}
          className="my-auto flex min-h-9 flex-wrap items-center gap-1.5 rounded-md border border-subtle p-1.5 text-11 text-tertiary capitalize hover:text-secondary"
        >
          <span className="text-11 text-tertiary">{chip.label}</span>
          <div className="flex items-center gap-1 rounded-sm bg-layer-1 px-1.5 py-1 text-11">
            <span className="normal-case">{chip.value}</span>
            <button
              type="button"
              className="grid place-items-center text-tertiary hover:text-secondary"
              aria-label={t("common.remove")}
              onClick={() => removeFilter(chip.key)}
            >
              <CloseOutline height={10} width={10} />
            </button>
          </div>
        </div>
      ))}
      <PillButton
        type="button"
        size="md"
        variant="outline"
        label={t("common.clear_all")}
        endIcon={<CloseOutline height={12} width={12} />}
        onClick={clearFilters}
      />
    </div>
  );
}
