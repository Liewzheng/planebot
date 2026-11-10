/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo } from "react";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { EModalPosition, EModalWidth, ModalCore } from "@plane/ui";
import type { ITestCase } from "@plane/types";
// types
import type { TTestCaseDraft } from "../types";

type Props = {
  isOpen: boolean;
  /** The row as the server holds it — the body of the 409. */
  current: ITestCase | null;
  draft: TTestCaseDraft;
  onKeepMine: () => void;
  onTakeTheirs: () => void;
  onClose: () => void;
};

/** The header fields the dialog compares, in the order they are shown. */
const COMPARED_FIELDS = [
  "module",
  "title",
  "purpose",
  "object_overview",
  "priority",
  "preconditions",
  "environment",
  "status",
] as const;

/**
 * The dialog of an optimistic-lock conflict.
 *
 * The server answers 409 with the row it holds. The user chooses which of
 * the two versions wins — the front end never overwrites the record of
 * somebody else on its own.
 */
export function TestCaseConflictModal(props: Props) {
  const { isOpen, current, draft, onKeepMine, onTakeTheirs, onClose } = props;
  const { t } = useTranslation();

  const differences = useMemo(() => {
    if (!current) return [];
    const rows: { field: string; mine: string; theirs: string }[] = [];

    for (const field of COMPARED_FIELDS) {
      const mine = String(draft[field] ?? "");
      const theirs = String((current as unknown as Record<string, unknown>)[field] ?? "");
      if (mine !== theirs) rows.push({ field, mine, theirs });
    }

    const mineSteps = JSON.stringify(draft.steps.map((step) => [step.action, step.expected_result]));
    const theirsSteps = JSON.stringify((current.steps ?? []).map((step) => [step.action, step.expected_result]));
    if (mineSteps !== theirsSteps) rows.push({ field: "steps", mine: mineSteps, theirs: theirsSteps });

    return rows;
  }, [current, draft]);

  const labelOf = (field: string) => {
    const keyByField: Record<string, string> = {
      module: "project_test_case.editor.field_module",
      title: "project_test_case.editor.field_title",
      purpose: "project_test_case.editor.field_purpose",
      object_overview: "project_test_case.editor.field_object_overview",
      priority: "project_test_case.editor.field_priority",
      preconditions: "project_test_case.editor.field_preconditions",
      environment: "project_test_case.editor.field_environment",
      status: "project_test_case.editor.field_status",
      steps: "project_test_case.editor.section_steps",
    };
    return t(keyByField[field] ?? field);
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={onClose} position={EModalPosition.CENTER} width={EModalWidth.XL}>
      <div className="flex flex-col gap-4 p-5">
        <div className="flex flex-col gap-1">
          <h2 className="text-16 font-semibold text-primary">{t("project_test_case.save.conflict_title")}</h2>
          <p className="text-13 text-secondary">{t("project_test_case.save.conflict_description")}</p>
        </div>

        {differences.length === 0 ? (
          <p className="text-13 text-tertiary">{t("project_test_case.save.conflict_no_difference")}</p>
        ) : (
          <div className="flex flex-col gap-2">
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              <span className="text-11 font-medium text-tertiary" />
              <span className="text-11 font-medium text-tertiary">{t("project_test_case.save.conflict_mine")}</span>
              <span className="text-11 font-medium text-tertiary">{t("project_test_case.save.conflict_theirs")}</span>
            </div>
            {differences.map((difference) => (
              <div
                key={difference.field}
                className="grid grid-cols-1 gap-2 rounded-md border border-subtle-1 bg-layer-1 p-2 sm:grid-cols-3"
              >
                <span className="text-11 font-medium text-secondary">{labelOf(difference.field)}</span>
                <span className="text-11 break-words text-primary">{difference.mine || "—"}</span>
                <span className="text-11 break-words text-secondary">{difference.theirs || "—"}</span>
              </div>
            ))}
          </div>
        )}

        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button variant="tertiary" size="lg" onClick={onClose}>
            {t("project_test_case.save.conflict_cancel")}
          </Button>
          <Button variant="secondary" size="lg" onClick={onTakeTheirs}>
            {t("project_test_case.save.conflict_take_theirs")}
          </Button>
          <Button variant="primary" size="lg" onClick={onKeepMine}>
            {t("project_test_case.save.conflict_keep_mine")}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
}
