/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import useSWR from "swr";
// plane imports
import { useTranslation } from "@plane/i18n";
import type { TProjectGrant, TServicePrincipal } from "@plane/types";
// hooks
import { useProject } from "@/hooks/store/use-project";

type Props = {
  principal: TServicePrincipal;
  workspaceSlug: string;
};

/**
 * Compact "which projects is this principal NOT yet granted to" view for
 * the workspace-level service principal list. Computes the difference
 * between the workspace project list and the principal's grant set
 * entirely client-side — both already come back from
 * `GET /api/workspaces/<slug>/service-principals/`. The principal list
 * itself is invalidated by `grants-modal` after every save, so this
 * panel stays in sync without an extra fetch.
 */
export const ServicePrincipalGrantsCoverage = observer(function ServicePrincipalGrantsCoverage(props: Props) {
  const { principal, workspaceSlug } = props;
  const { t } = useTranslation();
  const { projectMap, workspaceProjectIds, fetchProjects } = useProject();

  // Triggers the workspace project fetch and shares its SWR cache with
  // grants-modal / scopes-modal so every surface sees the same data.
  useSWR(
    workspaceSlug ? `WORKSPACE_PROJECTS_${workspaceSlug}` : null,
    workspaceSlug ? () => fetchProjects(workspaceSlug) : null,
    { revalidateIfStale: false, revalidateOnFocus: false }
  );

  const grantedProjectIds = new Set((principal.grants ?? []).map((g: TProjectGrant) => g.project));

  const workspaceProjects = (workspaceProjectIds ?? [])
    .map((projectId) => projectMap?.[projectId])
    .filter((project) => project !== undefined);

  const missingProjects = workspaceProjects.filter((project) => !grantedProjectIds.has(project.id));

  if (workspaceProjects.length === 0) {
    return <p className="text-11 text-placeholder">{t("loading")}…</p>;
  }

  return (
    <div className="flex flex-col gap-1">
      <p className="text-11 text-tertiary">
        Granted to <span className="font-medium text-secondary">{grantedProjectIds.size}</span> of{" "}
        <span className="font-medium text-secondary">{workspaceProjects.length}</span> workspace project
        {workspaceProjects.length === 1 ? "" : "s"}.
      </p>
      {missingProjects.length > 0 ? (
        missingProjects.length <= 3 ? (
          <p className="truncate text-11 text-placeholder">
            Not granted: {missingProjects.map((p) => p.name).join(", ")}
          </p>
        ) : (
          <p className="truncate text-11 text-placeholder">
            Not granted:{" "}
            {missingProjects
              .slice(0, 3)
              .map((p) => p.name)
              .join(", ")}{" "}
            and {missingProjects.length - 3} more
          </p>
        )
      ) : (
        <p className="text-11 text-success-primary">All workspace projects covered.</p>
      )}
    </div>
  );
});
