/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
// components
import { PageHead } from "@/components/core/page-title";
// hooks
import { useProject } from "@/hooks/store/use-project";
import type { Route } from "./+types/page";

function ProjectTestRunsPage({ params }: Route.ComponentProps) {
  const { projectId } = params;
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { getProjectById } = useProject();
  // derived values
  const project = getProjectById(projectId);
  const pageTitle = project?.name ? `${project.name} - ${t("project_test_case.batches.title")}` : undefined;

  return (
    <>
      <PageHead title={pageTitle} />
      <div className="flex h-full w-full flex-col items-start justify-center gap-2 px-6 py-8">
        <h1 className="text-18 font-semibold text-primary">{t("project_test_case.batches.title")}</h1>
        <p className="text-13 text-secondary">{t("project_test_case.batches.description")}</p>
        <p className="text-13 text-tertiary">{t("project_test_case.batches.empty_title")}</p>
        <p className="text-13 text-tertiary">{t("project_test_case.batches.empty_description")}</p>
      </div>
    </>
  );
}

export default observer(ProjectTestRunsPage);
