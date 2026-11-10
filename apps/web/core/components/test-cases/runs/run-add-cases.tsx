/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo, useState } from "react";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { AddOutline } from "@makeplane/propel/icons";
import { SearchIcon } from "@plane/propel/icons";
import useSWR from "swr";
import type { ITestCase } from "@plane/types";
// components
import { TestCasePriorityBadge } from "../shared/badges";
// constants
import { TEST_RUN_ADDABLE_CASES } from "./constants";
import { refusalFallback, refusalText } from "./types";
import type { TRunRefusal } from "./types";
// services
import { testCaseService } from "@/services/test-cases/test-case.service";

type Props = {
  workspaceSlug: string;
  projectId: string;
  testRunId: string;
  /** The cases already in the batch — they are not offered again. */
  existingCaseIds: string[];
  disabled: boolean;
  onAdded: () => void;
};

/**
 * The panel that fills a batch with cases.
 *
 * Every case that is added is **snapshotted** by the server: its header and
 * its step table are copied into the batch, so editing the case later never
 * rewrites what the batch recorded. A case that is already in the batch is
 * not offered; a concurrent add is refused by the server with
 * `duplicate_case_number`, and that refusal is shown instead of a generic
 * error.
 *
 * The list is the active cases of the project — a voided case is history, it
 * may not enter a new batch.
 */
export function TestCaseRunAddCases(props: Props) {
  const { workspaceSlug, projectId, testRunId, existingCaseIds, disabled, onAdded } = props;
  const { t } = useTranslation();
  // states
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [isAdding, setIsAdding] = useState(false);

  const { data, isLoading, error } = useSWR(
    workspaceSlug && projectId ? TEST_RUN_ADDABLE_CASES(workspaceSlug, projectId) : null,
    () => testCaseService.getCases(workspaceSlug, projectId, { status: "active" }, { per_page: 100 })
  );

  const existing = useMemo(() => new Set(existingCaseIds), [existingCaseIds]);

  const offerable = useMemo(() => {
    const term = search.trim().toLowerCase();
    return (data?.results ?? []).filter((testCase) => {
      if (existing.has(testCase.id)) return false;
      if (!term) return true;
      return (
        testCase.number.toLowerCase().includes(term) ||
        testCase.title.toLowerCase().includes(term) ||
        (testCase.module_code ?? "").toLowerCase().includes(term)
      );
    });
  }, [data, existing, search]);

  const toggle = (id: string) => {
    setSelected((current) => (current.includes(id) ? current.filter((entry) => entry !== id) : [...current, id]));
  };

  const add = async () => {
    if (selected.length === 0) return;

    setIsAdding(true);
    try {
      await testCaseService.addRunCases(workspaceSlug, projectId, testRunId, selected);
      setSelected([]);
      onAdded();
    } catch (addError) {
      const refusal = addError as TRunRefusal;
      const reason =
        refusal?.errors?.find((entry) => entry.message)?.message ||
        refusalText(refusal) ||
        refusalFallback(refusal, t, "project_test_case.execution.errors.write_not_saved");
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("project_test_case.execution.add_cases.failed"),
        message: reason,
      });
      // The batch on the server is the truth: a case that was added by
      // somebody else in the meantime disappears from the offer list.
      if (refusal?.code === "duplicate_case_number") onAdded();
    } finally {
      setIsAdding(false);
    }
  };

  return (
    <section className="flex flex-col gap-3 rounded-md border border-subtle-1 bg-layer-1 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-12 font-semibold text-primary">{t("project_test_case.execution.add_cases.title")}</h3>
        <span className="text-11 text-tertiary">{t("project_test_case.execution.add_cases.hint")}</span>
      </div>

      <label className="flex items-center gap-2 rounded-md border border-subtle-1 bg-layer-2 px-2 py-1.5">
        <SearchIcon className="size-3.5 shrink-0 text-tertiary" />
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder={t("project_test_case.execution.add_cases.search_placeholder")}
          className="min-w-0 flex-1 bg-transparent text-12 text-primary placeholder:text-tertiary focus:outline-none"
          aria-label={t("project_test_case.execution.add_cases.search_placeholder")}
        />
      </label>

      {isLoading && <p className="text-12 text-tertiary">{t("project_test_case.execution.add_cases.loading")}</p>}
      {error && <p className="text-12 text-danger-primary">{t("project_test_case.execution.add_cases.load_error")}</p>}

      {!isLoading && !error && offerable.length === 0 && (
        <p className="rounded-md border border-dashed border-subtle-1 bg-layer-2/40 px-3 py-4 text-12 text-tertiary">
          {(data?.results ?? []).length === 0
            ? t("project_test_case.execution.add_cases.no_case")
            : t("project_test_case.execution.add_cases.nothing_left")}
        </p>
      )}

      {offerable.length > 0 && (
        <ul className="flex max-h-72 flex-col gap-1 overflow-y-auto">
          {offerable.map((testCase: ITestCase) => (
            <li key={testCase.id}>
              <label className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 hover:bg-layer-2">
                <input
                  type="checkbox"
                  checked={selected.includes(testCase.id)}
                  onChange={() => toggle(testCase.id)}
                  disabled={disabled}
                  className="size-3.5 shrink-0"
                />
                <span className="font-mono shrink-0 text-11 text-tertiary">{testCase.number}</span>
                <span className="min-w-0 flex-1 truncate text-12 text-primary">{testCase.title || "—"}</span>
                <TestCasePriorityBadge priority={testCase.priority} />
              </label>
            </li>
          ))}
        </ul>
      )}

      <div className="flex items-center justify-end">
        <Button
          variant="primary"
          size="lg"
          onClick={() => void add()}
          disabled={disabled || isAdding || selected.length === 0}
          loading={isAdding}
          prependIcon={<AddOutline className="size-3.5" />}
        >
          {t("project_test_case.execution.add_cases.submit", { count: selected.length })}
        </Button>
      </div>
    </section>
  );
}
