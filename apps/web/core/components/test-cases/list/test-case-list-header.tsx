/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useRef, useState } from "react";
import type React from "react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { getButtonStyling } from "@plane/propel/button";
import { IconButton } from "@plane/propel/icon-button";
import { useOutsideClickDetector } from "@plane/hooks";
import { cn } from "@plane/utils";
import { ChevronDownOutline, CloseOutline, FilterOutline, SearchOutline } from "@makeplane/propel/icons";
// components
import { FiltersDropdown } from "@/components/issues/issue-layouts/filters";
// local imports
import { TestCaseFilterPanel } from "./test-case-filters";
import { useTestCaseListFilters } from "./test-case-list-filters";

/**
 * The controls of the list of the cases: the search, folded behind its
 * icon until the user asks for it, and the filter panel behind a button
 * that carries the number of applied filters.
 *
 * The behaviour of the search is the one of the views page: the icon opens
 * the field, Escape clears the term and then closes the field, and the
 * field closes by itself when the user clicks outside of it while it is
 * empty.
 */
export function TestCaseListHeader() {
  // translation
  const { t } = useTranslation();
  // store
  const { filters, hasFilters, appliedFilterCount, updateFilters } = useTestCaseListFilters();
  // states
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  // refs
  const inputRef = useRef<HTMLInputElement>(null);

  // derived values
  const search = filters.search ?? "";

  // handlers
  const handleInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== "Escape") return;

    if (search.trim() !== "") updateFilters({ search: "" });
    else {
      setIsSearchOpen(false);
      inputRef.current?.blur();
    }
  };

  // outside click detector hook
  useOutsideClickDetector(inputRef, () => {
    if (isSearchOpen && search.trim() === "") setIsSearchOpen(false);
  });

  useEffect(() => {
    if (search.trim() !== "") setIsSearchOpen(true);
  }, [search]);

  // The button of the filter panel. It is a plain element styled as the
  // secondary button of the app — the dropdown wraps it in the button that
  // opens the panel, so a second button inside it would be one too many.
  const filterButton = (
    <div className={cn(getButtonStyling("secondary", "lg"), "gap-1.5")}>
      <FilterOutline className="size-3" />
      <span className="hidden sm:inline">{t("common.filters")}</span>
      {appliedFilterCount > 0 && (
        <span className="grid h-4 min-w-4 place-items-center rounded-full bg-accent-primary px-1 text-10 text-on-color">
          {appliedFilterCount}
        </span>
      )}
      <ChevronDownOutline className="size-3" />
    </div>
  );

  return (
    <div className="flex h-full items-center gap-2">
      <div className="flex items-center">
        {!isSearchOpen && (
          <IconButton
            variant="ghost"
            size="lg"
            className="-mr-1"
            onClick={() => {
              setIsSearchOpen(true);
              inputRef.current?.focus();
            }}
            icon={SearchOutline}
          />
        )}
        <div
          className={cn(
            "ml-auto flex w-0 items-center justify-start gap-1 overflow-hidden rounded-md border border-transparent bg-surface-1 text-placeholder opacity-0 transition-[width] ease-linear",
            {
              "w-40 border-subtle px-2.5 py-1.5 opacity-100 sm:w-64": isSearchOpen,
            }
          )}
        >
          <SearchOutline className="h-3.5 w-3.5" />
          <input
            ref={inputRef}
            className="w-full max-w-[234px] border-none bg-transparent text-13 text-primary placeholder:text-placeholder focus:outline-none"
            placeholder={t("project_test_case.list.search_placeholder")}
            value={search}
            onChange={(event) => updateFilters({ search: event.target.value })}
            onKeyDown={handleInputKeyDown}
          />
          {isSearchOpen && (
            <button
              type="button"
              className="grid place-items-center"
              onClick={() => {
                updateFilters({ search: "" });
                setIsSearchOpen(false);
              }}
            >
              <CloseOutline className="h-3 w-3" />
            </button>
          )}
        </div>
      </div>
      <FiltersDropdown menuButton={filterButton} placement="bottom-end" isFiltersApplied={hasFilters}>
        <TestCaseFilterPanel />
      </FiltersDropdown>
    </div>
  );
}
