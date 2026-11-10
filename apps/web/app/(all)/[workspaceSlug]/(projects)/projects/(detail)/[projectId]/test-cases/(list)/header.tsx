/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useParams } from "react-router";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { CheckSquareOutline } from "@makeplane/propel/icons";
import { Breadcrumbs, Header } from "@plane/ui";
// components
import { BreadcrumbLink } from "@/components/common/breadcrumb-link";
import { CommonProjectBreadcrumbs } from "@/components/breadcrumbs/common";
import { TestCaseListHeader, TestCaseTabs } from "@/components/test-cases";
// hooks
import { useProject } from "@/hooks/store/use-project";
import { useUserPermissions } from "@/hooks/store/user";
import { useAppRouter } from "@/hooks/use-app-router";

export const TestCasesListHeader = function TestCasesListHeader() {
  // router
  const router = useAppRouter();
  const { workspaceSlug, projectId } = useParams();
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { loader } = useProject();
  const { allowPermissions } = useUserPermissions();
  // derived values
  const canUserCreateTestCase = allowPermissions(
    [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
    EUserPermissionsLevel.PROJECT
  );

  return (
    <Header>
      <Header.LeftItem>
        <div className="flex items-center gap-3">
          <Breadcrumbs onBack={router.back} isLoading={loader === "init-loader"}>
            <CommonProjectBreadcrumbs
              workspaceSlug={workspaceSlug?.toString() ?? ""}
              projectId={projectId?.toString() ?? ""}
            />
            <Breadcrumbs.Item
              component={
                <BreadcrumbLink
                  label={t("project_test_case.list.title")}
                  href={`/${workspaceSlug}/projects/${projectId}/test-cases/`}
                  icon={<CheckSquareOutline className="h-4 w-4 text-tertiary" />}
                  isLast
                />
              }
              isLast
            />
          </Breadcrumbs>
          <TestCaseTabs workspaceSlug={workspaceSlug?.toString() ?? ""} projectId={projectId?.toString() ?? ""} />
        </div>
      </Header.LeftItem>
      <Header.RightItem>
        <TestCaseListHeader />
        {canUserCreateTestCase && (
          <Button
            variant="primary"
            size="lg"
            onClick={() => router.push(`/${workspaceSlug}/projects/${projectId}/test-cases/new`)}
          >
            <div className="block sm:hidden">{t("add")}</div>
            <div className="hidden sm:block">{t("project_test_case.list.create_button")}</div>
          </Button>
        )}
      </Header.RightItem>
    </Header>
  );
};
