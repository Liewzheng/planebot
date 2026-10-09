/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { SortAscendingOutline, SortDescendingOutline, TickOutline } from "@makeplane/propel/icons";
// plane imports
import { useTranslation } from "@plane/i18n";
import { getButtonStyling } from "@plane/propel/button";
// types
import type { TPageFiltersSortBy, TPageFiltersSortKey } from "@plane/types";
import { CustomMenu } from "@plane/ui";

type Props = {
  onChange: (value: { key?: TPageFiltersSortKey; order?: TPageFiltersSortBy }) => void;
  sortBy: TPageFiltersSortBy;
  sortKey: TPageFiltersSortKey;
};

const PAGE_SORTING_KEY_OPTIONS: {
  key: TPageFiltersSortKey;
  i18nLabel: string;
}[] = [
  { key: "name", i18nLabel: "common.name" },
  { key: "created_at", i18nLabel: "page_list.sort.date_created" },
  { key: "updated_at", i18nLabel: "page_list.sort.date_modified" },
];

export function PageOrderByDropdown(props: Props) {
  const { onChange, sortBy, sortKey } = props;
  const { t } = useTranslation();

  const orderByDetails = PAGE_SORTING_KEY_OPTIONS.find((option) => sortKey === option.key);
  const isDescending = sortBy === "desc";

  return (
    <CustomMenu
      customButton={
        <div className={getButtonStyling("secondary", "lg")}>
          {!isDescending ? <SortAscendingOutline className="size-3" /> : <SortDescendingOutline className="size-3" />}
          {orderByDetails ? t(orderByDetails.i18nLabel) : ""}
        </div>
      }
      placement="bottom-end"
      maxHeight="lg"
      closeOnSelect
    >
      {PAGE_SORTING_KEY_OPTIONS.map((option) => (
        <CustomMenu.MenuItem
          key={option.key}
          className="flex items-center justify-between gap-2"
          onClick={() =>
            onChange({
              key: option.key,
            })
          }
        >
          {t(option.i18nLabel)}
          {sortKey === option.key && <TickOutline className="h-3 w-3" />}
        </CustomMenu.MenuItem>
      ))}
      <hr className="my-2 border-subtle" />
      <CustomMenu.MenuItem
        className="flex items-center justify-between gap-2"
        onClick={() => {
          if (isDescending)
            onChange({
              order: "asc",
            });
        }}
      >
        {t("common.order_by.asc")}
        {!isDescending && <TickOutline className="h-3 w-3" />}
      </CustomMenu.MenuItem>
      <CustomMenu.MenuItem
        className="flex items-center justify-between gap-2"
        onClick={() => {
          if (!isDescending)
            onChange({
              order: "desc",
            });
        }}
      >
        {t("common.order_by.desc")}
        {isDescending && <TickOutline className="h-3 w-3" />}
      </CustomMenu.MenuItem>
    </CustomMenu>
  );
}
