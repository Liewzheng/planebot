/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useParams } from "react-router";
// plane imports
import { useTranslation } from "@plane/i18n";
import { CheckSquareOutline } from "@makeplane/propel/icons";
import { Breadcrumbs, Header } from "@plane/ui";
// components
import { BreadcrumbLink } from "@/components/common/breadcrumb-link";
import { CommonProjectBreadcrumbs } from "@/components/breadcrumbs/common";
import { TestCaseTabs } from "@/components/test-cases";
// hooks
import { useProject } from "@/hooks/store/use-project";
import { useAppRouter } from "@/hooks/use-app-router";

export const TestCaseDetailHeader = function TestCaseDetailHeader() {
  // router
  const router = useAppRouter();
  const { workspaceSlug, projectId } = useParams();
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { loader } = useProject();

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
                />
              }
            />
            <Breadcrumbs.Item
              component={
                <BreadcrumbLink
                  label={t("project_test_case.editor.title")}
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
    </Header>
  );
};
