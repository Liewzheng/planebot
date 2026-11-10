/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { SearchOutline } from "@makeplane/propel/icons";
import type { ISearchIssueResponse } from "@plane/types";
import { cn } from "@plane/utils";
// services
import { testCaseService } from "@/services/test-cases/test-case.service";

/** The debounce of the search — the endpoint answers on every request. */
const SEARCH_DEBOUNCE_MS = 300;

/** The identifier of a work item — the identifier of its project and its number. */
const identifierOf = (issue: ISearchIssueResponse) =>
  issue.project__identifier ? `${issue.project__identifier}-${issue.sequence_id}` : String(issue.sequence_id);

type Props = {
  workspaceSlug: string;
  projectId: string;
  /** The work items that are already linked — they are shown as taken. */
  linkedIssueIds?: string[];
  /** The ids the user picked. */
  selectedIds: string[];
  /** Called with the whole selection whenever it changes. */
  onSelectionChange: (ids: string[]) => void;
  /**
   * Called with the work item that was picked (and with `null` when the
   * picked one was un-picked). A single-choice caller needs it to keep the
   * readable name of the item next to the id it stores.
   */
  onPick?: (issue: ISearchIssueResponse | null) => void;
  /** Several work items at a time (the editor) or only one (a filter). */
  multiple?: boolean;
  className?: string;
};

/**
 * The search of the work items of the project. The list of the cases and
 * the editor of a case both need one: the list filters by a linked work
 * item, the editor links work items to a case.
 *
 * The panel is rendered below the input instead of in a floating popover:
 * a popover that opens out of the screen is exactly the thing that breaks
 * on a phone.
 */
export function TestCaseWorkItemPicker(props: Props) {
  const {
    workspaceSlug,
    projectId,
    linkedIssueIds = [],
    selectedIds,
    onSelectionChange,
    onPick,
    multiple = true,
    className,
  } = props;
  const { t } = useTranslation();
  // states
  const [searchTerm, setSearchTerm] = useState("");
  const [results, setResults] = useState<ISearchIssueResponse[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  // refs
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // The search runs when the term changes; the answer of an older term is
  // dropped, so the list never shows the results of another term.
  useEffect(() => {
    const term = searchTerm.trim();
    if (timerRef.current) clearTimeout(timerRef.current);

    if (term.length < 2) {
      setResults([]);
      setIsSearching(false);
      return;
    }

    setIsSearching(true);
    timerRef.current = setTimeout(() => {
      testCaseService
        .searchWorkItems(workspaceSlug, projectId, term)
        .then((data) => setResults(Array.isArray(data) ? data : []))
        .catch(() => setResults([]))
        .finally(() => setIsSearching(false));
    }, SEARCH_DEBOUNCE_MS);

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [searchTerm, workspaceSlug, projectId]);

  const linkedIds = useMemo(() => new Set(linkedIssueIds), [linkedIssueIds]);

  const toggle = (issue: ISearchIssueResponse) => {
    const isSelected = selectedIds.includes(issue.id);

    if (!multiple) {
      onSelectionChange(isSelected ? [] : [issue.id]);
      onPick?.(isSelected ? null : issue);
      return;
    }
    onSelectionChange(isSelected ? selectedIds.filter((id) => id !== issue.id) : [...selectedIds, issue.id]);
  };

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <div className="relative">
        <SearchOutline className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-tertiary" />
        <input
          type="text"
          value={searchTerm}
          onChange={(event) => setSearchTerm(event.target.value)}
          placeholder={t("project_test_case.picker.search_placeholder")}
          className="placeholder-tertiary w-full rounded-md border border-subtle-1 bg-layer-2 py-2 pr-2 pl-8 text-13 text-primary focus:ring-1 focus:ring-accent-strong focus:outline-none"
        />
      </div>

      {isSearching && <div className="text-11 text-tertiary">{t("project_test_case.picker.searching")}</div>}

      {searchTerm.trim().length >= 2 && !isSearching && results.length === 0 && (
        <div className="text-11 text-tertiary">{t("project_test_case.picker.empty")}</div>
      )}

      {results.length > 0 && (
        <ul className="flex max-h-64 flex-col gap-1 overflow-y-auto rounded-md border border-subtle-1 bg-layer-1 p-1">
          {results.map((issue) => {
            const isLinked = linkedIds.has(issue.id);
            const isSelected = selectedIds.includes(issue.id);

            return (
              <li key={issue.id}>
                <button
                  type="button"
                  disabled={isLinked}
                  onClick={() => toggle(issue)}
                  className={cn(
                    "flex w-full items-start gap-2 rounded-sm px-2 py-1.5 text-left transition-colors",
                    isLinked ? "cursor-not-allowed opacity-60" : "hover:bg-layer-2",
                    isSelected && "bg-layer-2"
                  )}
                >
                  <span
                    className={cn(
                      "mt-0.5 flex size-3.5 shrink-0 items-center justify-center rounded border border-subtle-1",
                      multiple && "rounded",
                      !multiple && "rounded-full",
                      isSelected && "border-accent-primary bg-accent-primary"
                    )}
                    aria-hidden
                  >
                    {isSelected && <span className="size-1.5 rounded-sm bg-on-color" />}
                  </span>
                  <span className="flex min-w-0 flex-col">
                    <span className="truncate text-13 text-primary">{issue.name}</span>
                    <span className="truncate text-11 text-tertiary">
                      {identifierOf(issue)}
                      {isLinked ? ` · ${t("project_test_case.links.already_linked")}` : ""}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {selectedIds.length > 0 && (
        <div className="flex items-center justify-between gap-2">
          <span className="text-11 text-tertiary">
            {t("project_test_case.picker.selected")}: {selectedIds.length}
          </span>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              onSelectionChange([]);
              onPick?.(null);
            }}
          >
            {t("project_test_case.picker.clear")}
          </Button>
        </div>
      )}
    </div>
  );
}
