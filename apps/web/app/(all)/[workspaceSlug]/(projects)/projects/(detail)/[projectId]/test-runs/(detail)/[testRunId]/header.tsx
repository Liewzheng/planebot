/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useParams } from "react-router";
// plane imports
import { useTranslation } from "@plane/i18n";
import { PlayOutline } from "@makeplane/propel/icons";
import { Breadcrumbs, Header } from "@plane/ui";
import useSWR from "swr";
// components
import { BreadcrumbLink } from "@/components/common/breadcrumb-link";
import { CommonProjectBreadcrumbs } from "@/components/breadcrumbs/common";
import { TestCaseTabs } from "@/components/test-cases";
import { TEST_RUN_DETAIL } from "@/components/test-cases/runs";
// hooks
import { useProject } from "@/hooks/store/use-project";
import { useAppRouter } from "@/hooks/use-app-router";
// services
import { testCaseService } from "@/services/test-cases/test-case.service";

export const TestRunDetailHeader = function TestRunDetailHeader() {
  // router
  const router = useAppRouter();
  const { workspaceSlug, projectId, testRunId } = useParams();
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { loader } = useProject();
  // The page itself reads the batch under the same key: SWR dedupes, so the
  // number in the breadcrumb costs no second request.
  const { data: run } = useSWR(
    workspaceSlug && projectId && testRunId ? TEST_RUN_DETAIL(workspaceSlug, projectId, testRunId) : null,
    () => testCaseService.getRun(workspaceSlug ?? "", projectId ?? "", testRunId ?? "")
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
                  label={t("project_test_case.batches.title")}
                  href={`/${workspaceSlug}/projects/${projectId}/test-runs/`}
                  icon={<PlayOutline className="h-4 w-4 text-tertiary" />}
                />
              }
            />
            <Breadcrumbs.Item
              component={
                <span className="font-mono text-12 text-secondary">
                  {run?.number ?? t("project_test_case.execution.title")}
                </span>
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
