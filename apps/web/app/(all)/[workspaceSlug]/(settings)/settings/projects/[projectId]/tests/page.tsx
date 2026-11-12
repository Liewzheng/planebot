/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import Link from "next/link";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { getButtonStyling } from "@plane/propel/button";
// components
import { NotAuthorizedView } from "@/components/auth-screens/not-authorized-view";
import { PageHead } from "@/components/core/page-title";
import { SettingsBoxedControlItem } from "@/components/settings/boxed-control-item";
import { SettingsContentWrapper } from "@/components/settings/content-wrapper";
import { SettingsHeading } from "@/components/settings/heading";
import { ProjectSettingsFeatureControlItem } from "@/components/settings/project/content/feature-control-item";
// hooks
import { useProject } from "@/hooks/store/use-project";
import { useUserPermissions } from "@/hooks/store/user";
// local imports
import type { Route } from "./+types/page";
import { TestsProjectSettingsHeader } from "./header";

function TestsSettingsPage({ params }: Route.ComponentProps) {
  const { workspaceSlug, projectId } = params;
  // store hooks
  const { workspaceUserInfo, allowPermissions } = useUserPermissions();
  const { currentProjectDetails } = useProject();
  // translation
  const { t } = useTranslation();
  // derived values
  const pageTitle = currentProjectDetails?.name
    ? `${currentProjectDetails?.name} settings - ${t("project_settings.features.tests.short_title")}`
    : undefined;
  const canPerformProjectAdminActions = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.PROJECT);

  if (workspaceUserInfo && !canPerformProjectAdminActions) {
    return <NotAuthorizedView section="settings" isProjectView className="h-auto" />;
  }

  return (
    <SettingsContentWrapper header={<TestsProjectSettingsHeader />}>
      <PageHead title={pageTitle} />
      <section className="w-full">
        <SettingsHeading
          title={t("project_settings.features.tests.title")}
          description={t("project_settings.features.tests.description")}
        />
        <div className="mt-7 flex flex-col gap-4">
          <ProjectSettingsFeatureControlItem
            title={t("project_settings.features.tests.toggle_title")}
            description={t("project_settings.features.tests.toggle_description")}
            featureProperty="test_view"
            projectId={projectId}
            value={!!currentProjectDetails?.test_view}
            workspaceSlug={workspaceSlug}
          />
          <SettingsBoxedControlItem
            title={t("project_settings.features.tests.open_title")}
            description={t("project_settings.features.tests.open_description")}
            control={
              <Link
                href={`/${workspaceSlug}/projects/${projectId}/test-cases`}
                className={getButtonStyling("primary", "base")}
              >
                {t("project_settings.features.tests.open_action")}
              </Link>
            }
          />
        </div>
      </section>
    </SettingsContentWrapper>
  );
}

export default observer(TestsSettingsPage);
