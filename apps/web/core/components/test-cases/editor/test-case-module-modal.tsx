/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { EModalPosition, EModalWidth, ModalCore } from "@plane/ui";
import type { ITestCaseModule } from "@plane/types";
// components
import { TestCaseFormField } from "./test-case-form-field";
// constants
import { isValidModuleCode } from "../constants";
// services
import { testCaseService } from "@/services/test-cases/test-case.service";

type Props = {
  isOpen: boolean;
  workspaceSlug: string;
  projectId: string;
  onClose: () => void;
  onCreated: (module: ITestCaseModule) => void;
};

/**
 * The registration of a module.
 *
 * A case may only use a module code that the project registered, so a
 * project that wants to write cases has to register a module first. The
 * code rules of the server are checked here as well — the server refuses
 * the same code.
 */
export function TestCaseModuleModal(props: Props) {
  const { isOpen, workspaceSlug, projectId, onClose, onCreated } = props;
  const { t } = useTranslation();
  // states
  const [code, setCode] = useState("");
  const [description, setDescription] = useState("");
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const [isSubmitting, setIsSubmitting] = useState(false);

  const reset = () => {
    setCode("");
    setDescription("");
    setErrors({});
    setIsSubmitting(false);
  };

  const handleClose = () => {
    reset();
    onClose();
  };

  const handleSubmit = async () => {
    const normalizedCode = code.trim().toUpperCase();
    const trimmedDescription = description.trim();

    const localErrors: Record<string, string[]> = {};
    if (!normalizedCode) localErrors.code = [t("project_test_case.module.required_code")];
    else if (!isValidModuleCode(normalizedCode)) localErrors.code = [t("project_test_case.module.invalid_code")];
    if (!trimmedDescription) localErrors.description = [t("project_test_case.module.required_description")];

    if (Object.keys(localErrors).length > 0) {
      setErrors(localErrors);
      return;
    }

    setIsSubmitting(true);
    setErrors({});

    try {
      const created = await testCaseService.createModule(workspaceSlug, projectId, {
        code: normalizedCode,
        description: trimmedDescription,
      });
      onCreated(created);
      reset();
      onClose();
    } catch (error) {
      const envelope = error as { errors?: { field?: string; message?: string }[] } | undefined;
      const serverErrors: Record<string, string[]> = {};

      for (const entry of envelope?.errors ?? []) {
        const field = entry.field ?? "";
        const message = entry.message ?? t("project_test_case.module.create_error");
        serverErrors[field] = [...(serverErrors[field] ?? []), message];
      }

      setErrors(
        Object.keys(serverErrors).length > 0 ? serverErrors : { code: [t("project_test_case.module.create_error")] }
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={handleClose} position={EModalPosition.CENTER} width={EModalWidth.MD}>
      <div className="flex flex-col gap-4 p-5">
        <div className="flex flex-col gap-1">
          <h2 className="text-16 font-semibold text-primary">{t("project_test_case.module.create_title")}</h2>
          <p className="text-13 text-secondary">{t("project_test_case.module.create_description")}</p>
        </div>

        <TestCaseFormField
          label={t("project_test_case.module.code")}
          htmlFor="test-case-module-code"
          required
          errors={errors.code}
          hint={t("project_test_case.module.code_hint")}
        >
          <input
            id="test-case-module-code"
            value={code}
            onChange={(event) => setCode(event.target.value)}
            placeholder={t("project_test_case.module.code_placeholder")}
            maxLength={6}
            className="placeholder-tertiary w-full rounded-md border border-subtle-1 bg-layer-2 px-3 py-2 text-13 text-primary uppercase placeholder:normal-case focus:ring-1 focus:ring-accent-strong focus:outline-none"
          />
        </TestCaseFormField>

        <TestCaseFormField
          label={t("project_test_case.module.description")}
          htmlFor="test-case-module-description"
          required
          errors={errors.description}
        >
          <input
            id="test-case-module-description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder={t("project_test_case.module.description_placeholder")}
            className="placeholder-tertiary w-full rounded-md border border-subtle-1 bg-layer-2 px-3 py-2 text-13 text-primary focus:ring-1 focus:ring-accent-strong focus:outline-none"
          />
        </TestCaseFormField>

        <div className="flex items-center justify-end gap-2">
          <Button variant="tertiary" size="lg" onClick={handleClose}>
            {t("common.cancel")}
          </Button>
          <Button variant="primary" size="lg" onClick={handleSubmit} loading={isSubmitting}>
            {isSubmitting ? t("project_test_case.module.creating") : t("project_test_case.module.create_button")}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
}
