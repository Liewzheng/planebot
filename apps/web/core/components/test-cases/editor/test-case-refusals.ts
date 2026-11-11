/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { TTestCaseFieldErrors } from "../types";

/** The fields the rail of properties renders a reason for, next to its row. */
export const RAIL_FIELDS = ["module", "priority", "status"] as const;

/** The fields the reading column renders a reason for, next to its control. */
export const READING_COLUMN_FIELDS = [
  "title",
  "purpose",
  "object_overview",
  "preconditions",
  "environment",
  "steps",
  "__form__",
] as const;

const RAIL_FIELD_SET: ReadonlySet<string> = new Set(RAIL_FIELDS);
const READING_COLUMN_FIELD_SET: ReadonlySet<string> = new Set(READING_COLUMN_FIELDS);

/** True when the refusal of the server names a field the rail owns. */
export const refusalNamesRailField = (fields: TTestCaseFieldErrors): boolean =>
  RAIL_FIELDS.some((field) => (fields[field] ?? []).length > 0);

/**
 * The reasons that are not on screen next to the control they belong to: the
 * reasons of the rail while it is closed, and the reasons of a field that
 * neither the column nor the rail renders (the number format, the code of a
 * module the server refused).
 *
 * The editor uses this in two places: it opens the rail when a reason names a
 * rail field, and it lists whatever is still unreadable in the reading column.
 * A refused write therefore always ends in something the reader can see — the
 * create path has no save indicator at all, and a refusal that names a field
 * suppresses the toast.
 */
export const unreadableReasons = (
  fields: TTestCaseFieldErrors,
  isRailOpen: boolean
): { field: string; message: string }[] => {
  const reasons: { field: string; message: string }[] = [];

  for (const [field, messages] of Object.entries(fields)) {
    const inRail = RAIL_FIELD_SET.has(field);
    if (inRail && isRailOpen) continue;
    if (!inRail && READING_COLUMN_FIELD_SET.has(field)) continue;
    for (const message of messages) reasons.push({ field, message });
  }

  return reasons;
};
