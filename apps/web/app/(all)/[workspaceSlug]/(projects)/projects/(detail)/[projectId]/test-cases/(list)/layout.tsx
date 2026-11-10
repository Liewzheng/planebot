/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { Outlet, useParams } from "react-router";
// components
import { AppHeader } from "@/components/core/app-header";
import { ContentWrapper } from "@/components/core/content-wrapper";
import { TestCaseListFiltersProvider } from "@/components/test-cases";
import { TestCasesListHeader } from "./header";

export default function ProjectTestCasesListLayout() {
  const { workspaceSlug, projectId } = useParams();

  return (
    <TestCaseListFiltersProvider workspaceSlug={workspaceSlug ?? ""} projectId={projectId ?? ""}>
      <AppHeader header={<TestCasesListHeader />} />
      <ContentWrapper>
        <Outlet />
      </ContentWrapper>
    </TestCaseListFiltersProvider>
  );
}
