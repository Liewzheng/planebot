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
import { NotAuthorizedView } from "@/components/auth-screens/not-authorized-view";
import { PageHead } from "@/components/core/page-title";
import { ProjectMemberList } from "@/components/project/member-list";
import { ProjectSettingsMemberDefaults } from "@/components/project/project-settings-member-defaults";
import { SettingsContentWrapper } from "@/components/settings/content-wrapper";
import { SettingsHeading } from "@/components/settings/heading";
import { ProjectServicePrincipalAccessSection } from "@/components/ai-accounts";
// hooks
import { useProject } from "@/hooks/store/use-project";
import { useUserPermissions } from "@/hooks/store/user";
// local imports
import type { Route } from "./+types/page";
import { MembersProjectSettingsHeader } from "./header";

function MembersSettingsPage({ params }: Route.ComponentProps) {
  // router
  const { workspaceSlug, projectId } = params;
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { currentProjectDetails } = useProject();
  const { workspaceUserInfo, allowPermissions } = useUserPermissions();
  // derived values
  const pageTitle = currentProjectDetails?.name ? `${currentProjectDetails?.name} - Members` : undefined;
  const isProjectMemberOrAdmin = allowPermissions(
    [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
    EUserPermissionsLevel.PROJECT
  );
  const isWorkspaceAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);
  const canPerformProjectMemberActions = isProjectMemberOrAdmin || isWorkspaceAdmin;

  if (workspaceUserInfo && !canPerformProjectMemberActions) {
    return <NotAuthorizedView section="settings" isProjectView className="h-auto" />;
  }

  return (
    <SettingsContentWrapper header={<MembersProjectSettingsHeader />} hugging>
      <PageHead title={pageTitle} />
      <SettingsHeading title={t("common.members")} />
      <ProjectServicePrincipalAccessSection workspaceSlug={workspaceSlug} projectId={projectId} />
      <div className="my-6 rounded-md border border-subtle bg-surface-1 p-3 text-12 text-tertiary">
        The list below shows <strong className="text-secondary">human members only</strong>. Service principals (AI /
        machine accounts that integrations and automation sign in as) are intentionally absent — they have no{" "}
        <code>ProjectMember</code> row. Their access to <em>this</em> project is managed in the section above; the
        workspace-wide list lives under <strong>Workspace settings → Developer → Service principals</strong>.
      </div>
      <ProjectSettingsMemberDefaults projectId={projectId} workspaceSlug={workspaceSlug} />
      <ProjectMemberList projectId={projectId} workspaceSlug={workspaceSlug} />
    </SettingsContentWrapper>
  );
}

export default observer(MembersSettingsPage);
