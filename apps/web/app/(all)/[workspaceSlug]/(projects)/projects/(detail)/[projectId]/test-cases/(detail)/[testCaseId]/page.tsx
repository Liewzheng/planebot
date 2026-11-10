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

function ProjectTestCaseDetailPage({ params }: Route.ComponentProps) {
  const { workspaceSlug, projectId, testCaseId } = params;
  // router
  const router = useAppRouter();
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { getProjectById } = useProject();
  const { allowPermissions } = useUserPermissions();
  // derived values
  const project = getProjectById(projectId);
  const pageTitle = project?.name ? `${project.name} - ${t("project_test_case.editor.title")}` : undefined;
  const canEdit = allowPermissions(
    [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
    EUserPermissionsLevel.PROJECT,
    workspaceSlug,
    projectId
  );
  // The delete endpoint is admin-only — `TestCaseEndpoint.destroy`
  // (apps/api/plane/app/views/test_case/base.py:351) is
  // `@allow_permission([ROLE.ADMIN], ..., action=AuthzAction.DELETE)`, and a
  // member who owns the case still gets 403, so this is ADMIN alone rather
  // than `owner || admin`. The list page gates its row delete on the same
  // floor (`test-cases/(list)/page.tsx`, `canDelete`): change one, change
  // both.
  const canDelete = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.PROJECT, workspaceSlug, projectId);

  return (
    <>
      <PageHead title={pageTitle} />
      <TestCaseEditor
        workspaceSlug={workspaceSlug}
        projectId={projectId}
        testCaseId={testCaseId}
        canEdit={canEdit}
        canDelete={canDelete}
        onDeleted={() => router.push(`/${workspaceSlug}/projects/${projectId}/test-cases`)}
      />
    </>
  );
}

export default observer(ProjectTestCaseDetailPage);
