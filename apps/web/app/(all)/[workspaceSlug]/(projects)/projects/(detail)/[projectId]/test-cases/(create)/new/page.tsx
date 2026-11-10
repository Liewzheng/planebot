/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
// components
import { PageHead } from "@/components/core/page-title";
import { TestCaseEditor } from "@/components/test-cases";
// hooks
import { useProject } from "@/hooks/store/use-project";
import { useUserPermissions } from "@/hooks/store/user";
import { useAppRouter } from "@/hooks/use-app-router";
import type { Route } from "./+types/page";

function ProjectTestCaseCreatePage({ params }: Route.ComponentProps) {
  const { workspaceSlug, projectId } = params;
  // router
  const router = useAppRouter();
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { getProjectById } = useProject();
  const { allowPermissions } = useUserPermissions();
  // derived values
  const project = getProjectById(projectId);
  const pageTitle = project?.name ? `${project.name} - ${t("project_test_case.editor.create_title")}` : undefined;
  const canEdit = allowPermissions(
    [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
    EUserPermissionsLevel.PROJECT,
    workspaceSlug,
    projectId
  );

  return (
    <>
      <PageHead title={pageTitle} />
      <TestCaseEditor
        workspaceSlug={workspaceSlug}
        projectId={projectId}
        canEdit={canEdit}
        onCreated={(testCase) => router.push(`/${workspaceSlug}/projects/${projectId}/test-cases/${testCase.id}`)}
      />
    </>
  );
}

export default observer(ProjectTestCaseCreatePage);
