/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useTranslation } from "@plane/i18n";
import { CustomSelect, Input, TextArea } from "@plane/ui";
import type { ITestCaseModule } from "@plane/types";
// components
import { TestCaseFormField } from "./test-case-form-field";
// constants
import { TEST_CASE_PRIORITIES, TEST_CASE_STATUSES } from "../constants";
// types
import type { TTestCaseDraft } from "../types";

type Props = {
  draft: TTestCaseDraft;
  modules: ITestCaseModule[];
  errors: Record<string, string[]>;
  disabled: boolean;
  onChange: (patch: Partial<TTestCaseDraft>) => void;
  onBlur: () => void;
  onCreateModule: () => void;
};

/**
 * The eight items of the case header.
 *
 * The server refuses a case that misses one of the mandatory items, so
 * the editor marks them and shows the reason the server sent back. The
 * reasons are never replaced by a wording of the front end — the sentence
 * of the error envelope is what the tester has to read.
 */
export function TestCaseHeaderForm(props: Props) {
  const { draft, modules, errors, disabled, onChange, onBlur, onCreateModule } = props;
  const { t } = useTranslation();

  const selectedModule = modules.find((module) => module.id === draft.module);
  const priorityLabel = draft.priority || t("project_test_case.editor.priority_placeholder");
  const statusLabel = draft.status ? t(`project_test_case.status.${draft.status}`) : "";

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <TestCaseFormField
          label={t("project_test_case.editor.field_module")}
          required
          errors={errors.module}
          hint={selectedModule?.code ? undefined : t("project_test_case.module.code_hint")}
        >
          <div className="flex flex-col gap-1.5">
            {modules.length === 0 ? (
              <p className="text-11 text-tertiary">{t("project_test_case.module.empty")}</p>
            ) : (
              <CustomSelect
                value={draft.module}
                onChange={(value: string) => onChange({ module: value })}
                disabled={disabled}
                input
                label={
                  <span className={draft.module ? "text-primary" : "text-tertiary"}>
                    {selectedModule?.code ?? t("project_test_case.module.picker_placeholder")}
                  </span>
                }
                className="w-full"
              >
                {modules.map((module) => (
                  <CustomSelect.Option key={module.id} value={module.id}>
                    {module.code}
                  </CustomSelect.Option>
                ))}
              </CustomSelect>
            )}
            <button
              type="button"
              onClick={onCreateModule}
              disabled={disabled}
              className="self-start text-11 text-accent-primary hover:underline disabled:cursor-not-allowed disabled:text-tertiary"
            >
              + {t("project_test_case.module.create_button")}
            </button>
          </div>
        </TestCaseFormField>

        <TestCaseFormField label={t("project_test_case.editor.field_priority")} required errors={errors.priority}>
          <CustomSelect
            value={draft.priority}
            onChange={(value: string) => onChange({ priority: value })}
            disabled={disabled}
            input
            label={<span className={draft.priority ? "text-primary" : "text-tertiary"}>{priorityLabel}</span>}
            className="w-full"
          >
            {TEST_CASE_PRIORITIES.map((priority) => (
              <CustomSelect.Option key={priority} value={priority}>
                {priority}
              </CustomSelect.Option>
            ))}
          </CustomSelect>
        </TestCaseFormField>

        <TestCaseFormField label={t("project_test_case.editor.field_status")} errors={errors.status}>
          <CustomSelect
            value={draft.status}
            onChange={(value: string) => onChange({ status: value })}
            disabled={disabled}
            input
            label={<span className="text-primary">{statusLabel}</span>}
            className="w-full"
          >
            {TEST_CASE_STATUSES.map((status) => (
              <CustomSelect.Option key={status} value={status}>
                {t(`project_test_case.status.${status}`)}
              </CustomSelect.Option>
            ))}
          </CustomSelect>
        </TestCaseFormField>
      </div>

      <TestCaseFormField
        label={t("project_test_case.editor.field_title")}
        htmlFor="test-case-title"
        required
        errors={errors.title}
      >
        <Input
          id="test-case-title"
          value={draft.title}
          onChange={(event) => onChange({ title: event.target.value })}
          onBlur={onBlur}
          disabled={disabled}
          placeholder={t("project_test_case.editor.placeholder_title")}
          hasError={Boolean(errors.title?.length)}
          className="w-full"
        />
      </TestCaseFormField>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <TestCaseFormField
          label={t("project_test_case.editor.field_purpose")}
          htmlFor="test-case-purpose"
          required
          errors={errors.purpose}
        >
          <TextArea
            id="test-case-purpose"
            value={draft.purpose}
            onChange={(event) => onChange({ purpose: event.target.value })}
            onBlur={onBlur}
            disabled={disabled}
            placeholder={t("project_test_case.editor.placeholder_purpose")}
            hasError={Boolean(errors.purpose?.length)}
            className="w-full"
          />
        </TestCaseFormField>

        <TestCaseFormField
          label={t("project_test_case.editor.field_object_overview")}
          htmlFor="test-case-object-overview"
          required
          errors={errors.object_overview}
        >
          <TextArea
            id="test-case-object-overview"
            value={draft.object_overview}
            onChange={(event) => onChange({ object_overview: event.target.value })}
            onBlur={onBlur}
            disabled={disabled}
            placeholder={t("project_test_case.editor.placeholder_object_overview")}
            hasError={Boolean(errors.object_overview?.length)}
            className="w-full"
          />
        </TestCaseFormField>

        <TestCaseFormField
          label={t("project_test_case.editor.field_preconditions")}
          htmlFor="test-case-preconditions"
          required
          errors={errors.preconditions}
        >
          <TextArea
            id="test-case-preconditions"
            value={draft.preconditions}
            onChange={(event) => onChange({ preconditions: event.target.value })}
            onBlur={onBlur}
            disabled={disabled}
            placeholder={t("project_test_case.editor.placeholder_preconditions")}
            hasError={Boolean(errors.preconditions?.length)}
            className="w-full"
          />
        </TestCaseFormField>

        <TestCaseFormField
          label={t("project_test_case.editor.field_environment")}
          htmlFor="test-case-environment"
          required
          errors={errors.environment}
        >
          <TextArea
            id="test-case-environment"
            value={draft.environment}
            onChange={(event) => onChange({ environment: event.target.value })}
            onBlur={onBlur}
            disabled={disabled}
            placeholder={t("project_test_case.editor.placeholder_environment")}
            hasError={Boolean(errors.environment?.length)}
            className="w-full"
          />
        </TestCaseFormField>
      </div>
    </div>
  );
}
