/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useTranslation } from "@plane/i18n";
import { Input, TextArea } from "@plane/ui";
// components
import { TestCaseFormField } from "./test-case-form-field";
// types
import type { TTestCaseDraft } from "../types";

type Props = {
  draft: TTestCaseDraft;
  errors: Record<string, string[]>;
  disabled: boolean;
  onChange: (patch: Partial<TTestCaseDraft>) => void;
  onBlur: () => void;
};

/**
 * What the reading column carries: the title, then the four prose sections of
 * the case, one under the other.
 *
 * The properties of the case — module, priority, state and the linked work
 * items — sit in the rail next to this column, the same split the work-item
 * detail makes. The server refuses a case that misses one of the mandatory
 * items, so each field marks itself and shows the reason the server sent back;
 * the reasons are never replaced by a wording of the front end.
 */
export function TestCaseHeaderForm(props: Props) {
  const { draft, errors, disabled, onChange, onBlur } = props;
  const { t } = useTranslation();

  return (
    <div className="flex flex-col gap-6">
      <TestCaseFormField
        label={t("project_test_case.editor.field_title")}
        htmlFor="test-case-title"
        required
        errors={errors.title}
      >
        <Input
          id="test-case-title"
          mode="true-transparent"
          inputSize="md"
          value={draft.title}
          onChange={(event) => onChange({ title: event.target.value })}
          onBlur={onBlur}
          disabled={disabled}
          placeholder={t("project_test_case.editor.placeholder_title")}
          hasError={Boolean(errors.title?.length)}
          className="w-full px-0 py-1 text-18 font-semibold"
        />
      </TestCaseFormField>

      <div className="flex flex-col gap-5">
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
            className="min-h-20 w-full"
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
            className="min-h-20 w-full"
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
            className="min-h-20 w-full"
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
            className="min-h-20 w-full"
          />
        </TestCaseFormField>
      </div>
    </div>
  );
}
