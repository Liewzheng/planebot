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
import { TestCaseListView } from "@/components/test-cases";
// hooks
import { useProject } from "@/hooks/store/use-project";
import { useUserPermissions } from "@/hooks/store/user";
import type { Route } from "./+types/page";

function ProjectTestCasesPage({ params }: Route.ComponentProps) {
  const { workspaceSlug, projectId } = params;
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { getProjectById } = useProject();
  const { allowPermissions } = useUserPermissions();
  // derived values
  const project = getProjectById(projectId);
  const pageTitle = project?.name ? `${project.name} - ${t("project_test_case.list.title")}` : undefined;
  const canCreate = allowPermissions(
    [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
    EUserPermissionsLevel.PROJECT,
    workspaceSlug,
    projectId
  );
  // The delete endpoint is admin-only, so the row must not offer the action
  // to a member — the guard here is the one the server applies.
  const canDelete = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.PROJECT, workspaceSlug, projectId);

  return (
    <>
      <PageHead title={pageTitle} />
      <TestCaseListView
        workspaceSlug={workspaceSlug}
        projectId={projectId}
        canCreate={canCreate}
        canDelete={canDelete}
      />
    </>
  );
}

export default observer(ProjectTestCasesPage);
