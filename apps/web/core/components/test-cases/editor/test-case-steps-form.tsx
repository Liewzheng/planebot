/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { AddOutline, ChevronDownOutline, ChevronUpOutline, DeleteOutline } from "@makeplane/propel/icons";
import { TextArea } from "@plane/ui";
// types
import { newStepKey } from "../types";
import type { TTestCaseDraft, TTestCaseStepErrors } from "../types";

type Props = {
  draft: TTestCaseDraft;
  errors: TTestCaseStepErrors;
  disabled: boolean;
  onChange: (patch: Partial<TTestCaseDraft>) => void;
  onBlur: () => void;
};

/**
 * The ordered step table of the case.
 *
 * The table is a list of cards: on a narrow screen the two fields of a
 * step stack inside the card, on a wide screen they sit next to each
 * other. There is no horizontal scroll on either — a step table that has
 * to be scrolled sideways cannot be filled in on a phone.
 */
export function TestCaseStepsForm(props: Props) {
  const { draft, errors, disabled, onChange, onBlur } = props;
  const { t } = useTranslation();

  const updateStep = (index: number, patch: { action?: string; expected_result?: string }) => {
    onChange({
      steps: draft.steps.map((step, stepIndex) => (stepIndex === index ? { ...step, ...patch } : step)),
    });
  };

  const removeStep = (index: number) => {
    onChange({ steps: draft.steps.filter((_, stepIndex) => stepIndex !== index) });
  };

  const moveStep = (index: number, offset: number) => {
    const target = index + offset;
    if (target < 0 || target >= draft.steps.length) return;
    const steps = [...draft.steps];
    const [moved] = steps.splice(index, 1);
    steps.splice(target, 0, moved);
    onChange({ steps });
  };

  const addStep = () => {
    onChange({
      steps: [...draft.steps, { key: newStepKey(), action: "", expected_result: "" }],
    });
  };

  return (
    <div className="flex flex-col gap-3">
      {draft.steps.length === 0 ? (
        <div className="flex flex-col items-start gap-2 rounded-md border border-dashed border-subtle-1 bg-layer-2/40 px-4 py-6">
          <p className="text-13 text-secondary">{t("project_test_case.editor.steps_empty")}</p>
          <p className="text-11 text-tertiary">{t("project_test_case.editor.steps_empty_hint")}</p>
        </div>
      ) : (
        <ul className="flex flex-col gap-3">
          {draft.steps.map((step, index) => {
            const stepErrors = errors[index] ?? {};

            return (
              <li key={step.key} className="flex flex-col gap-3 rounded-md border border-subtle-1 bg-layer-1 p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-11 font-medium text-secondary">
                    {t("project_test_case.editor.step_title", { number: index + 1 })}
                  </span>
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => moveStep(index, -1)}
                      disabled={disabled || index === 0}
                      title={t("project_test_case.editor.move_up")}
                      aria-label={t("project_test_case.editor.move_up")}
                      className="rounded-sm p-1 text-tertiary hover:bg-layer-2 hover:text-primary disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      <ChevronUpOutline className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => moveStep(index, 1)}
                      disabled={disabled || index === draft.steps.length - 1}
                      title={t("project_test_case.editor.move_down")}
                      aria-label={t("project_test_case.editor.move_down")}
                      className="rounded-sm p-1 text-tertiary hover:bg-layer-2 hover:text-primary disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      <ChevronDownOutline className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => removeStep(index)}
                      disabled={disabled}
                      title={t("project_test_case.editor.delete_step")}
                      aria-label={t("project_test_case.editor.delete_step")}
                      className="rounded-sm p-1 text-tertiary hover:bg-layer-2 hover:text-danger-primary disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      <DeleteOutline className="size-3.5" />
                    </button>
                  </div>
                </div>

                <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                  <div className="flex min-w-0 flex-col gap-1.5">
                    <label className="text-11 font-medium text-secondary" htmlFor={`step-action-${step.key}`}>
                      {t("project_test_case.editor.step_action")}
                    </label>
                    <TextArea
                      id={`step-action-${step.key}`}
                      value={step.action}
                      onChange={(event) => updateStep(index, { action: event.target.value })}
                      onBlur={onBlur}
                      disabled={disabled}
                      placeholder={t("project_test_case.editor.step_placeholder_action")}
                      hasError={Boolean(stepErrors.action)}
                      className="w-full"
                    />
                    {stepErrors.action && <p className="text-11 text-danger-primary">{stepErrors.action}</p>}
                  </div>

                  <div className="flex min-w-0 flex-col gap-1.5">
                    <label className="text-11 font-medium text-secondary" htmlFor={`step-expected-${step.key}`}>
                      {t("project_test_case.editor.step_expected")}
                    </label>
                    <TextArea
                      id={`step-expected-${step.key}`}
                      value={step.expected_result}
                      onChange={(event) => updateStep(index, { expected_result: event.target.value })}
                      onBlur={onBlur}
                      disabled={disabled}
                      placeholder={t("project_test_case.editor.step_placeholder_expected")}
                      hasError={Boolean(stepErrors.expected_result)}
                      className="w-full"
                    />
                    {stepErrors.expected_result && (
                      <p className="text-11 text-danger-primary">{stepErrors.expected_result}</p>
                    )}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <Button
        variant="secondary"
        size="lg"
        onClick={addStep}
        disabled={disabled}
        prependIcon={<AddOutline className="size-3.5" />}
        className="self-start"
      >
        {t("project_test_case.editor.add_step")}
      </Button>
    </div>
  );
}
