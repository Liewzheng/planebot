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
import { Button } from "@plane/propel/button";
// hooks
import { useProject } from "@/hooks/store/use-project";

type Props = {
  principal: TServicePrincipal;
  workspaceSlug: string;
  /**
   * Optional callback to open the principal's grants modal (where an
   * admin sees the full missing-project list and can grant them in bulk).
   * When provided and the principal is missing more than a handful of
   * projects, the panel renders a "Manage grants →" CTA so the gap is
   * fixable without first hunting for the row's Manage button.
   */
  onManageGrants?: () => void;
};

/**
 * Compact "which projects is this principal NOT yet granted to" view for
 * the workspace-level service principal list. Computes the difference
 * between the workspace project list and the principal's grant set
 * entirely client-side — both already come back from
 * `GET /api/workspaces/<slug>/service-principals/`. The principal list
 * itself is invalidated by `grants-modal` after every save, so this
 * panel stays in sync without an extra fetch.
 *
 * Renders four distinct states (so a failed fetch cannot masquerade as
 * an empty workspace):
 *   - loading : first fetch in flight
 *   - error   : fetch rejected, with a retry button
 *   - empty   : workspace has zero projects (rare — Plane always seeds one)
 *   - ready   : the normal "Granted to N of M" summary, plus a CTA into
 *               the grants modal when there is a meaningful gap to fix
 */
export const ServicePrincipalGrantsCoverage = observer(function ServicePrincipalGrantsCoverage(props: Props) {
  const { principal, workspaceSlug, onManageGrants } = props;
  const { t } = useTranslation();
  const { projectMap, workspaceProjectIds, fetchProjects } = useProject();

  // Triggers the workspace project fetch and shares its SWR cache with
  // grants-modal / scopes-modal so every surface sees the same data.
  const swrKey = workspaceSlug ? `WORKSPACE_PROJECTS_${workspaceSlug}` : null;
  const {
    isLoading,
    error,
    mutate: revalidateWorkspaceProjects,
  } = useSWR(swrKey, () => fetchProjects(workspaceSlug), { revalidateIfStale: false, revalidateOnFocus: false });

  if (isLoading) {
    return <p className="text-11 text-placeholder">{t("loading")}…</p>;
  }
  if (error) {
    return (
      <div className="flex items-start justify-between gap-3">
        <p className="text-warning text-11">
          {t("error")}:{" "}
          {t("workspace_settings.settings.service_principals.project_access.coverage.load_failed_message_before")}
        </p>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => {
            // Retry the failed workspace-project fetch in place. SWR's
            // `mutate()` re-runs the fetcher for the same key without a
            // route remount, which is the right tool for a settings-page
            // error pane (the sibling service-principals page already
            // does the same thing).
            void revalidateWorkspaceProjects();
          }}
        >
          {t("common.retry")}
        </Button>
      </div>
    );
  }

  const workspaceProjects = (workspaceProjectIds ?? [])
    .map((projectId) => projectMap?.[projectId])
    .filter((project) => project !== undefined);

  // Genuinely-empty workspace — the fetch succeeded but returned zero
  // projects (e.g. a freshly created workspace before any seeding). It
  // is distinct from the loading state above so a stuck fetch cannot
  // look identical to a real empty workspace.
  if (workspaceProjects.length === 0) {
    return (
      <p className="text-11 text-placeholder">
        {t("workspace_settings.settings.service_principals.project_access.coverage.empty_workspace")}
      </p>
    );
  }

  // Build the granted-ids set keyed on the non-archived workspace project
  // list only. Grants whose project was archived — and therefore dropped
  // from `workspaceProjectIds` by the project store — are excluded from
  // the coverage count, so archiving a granted project can no longer
  // render 'Granted to 4 of 3 workspace projects.'; they also can't
  // surface as 'missing' below (archived projects are not grantable).
  const workspaceProjectIdSet = new Set(workspaceProjects.map((p) => p.id));
  const visibleGrantedProjectIds = new Set(
    (principal.grants ?? []).filter((g: TProjectGrant) => workspaceProjectIdSet.has(g.project)).map((g) => g.project)
  );

  const missingProjects = workspaceProjects.filter((project) => !visibleGrantedProjectIds.has(project.id));
  const missingCount = missingProjects.length;
  const showOverflowCta = onManageGrants !== undefined && missingCount > 3;

  return (
    <div className="flex flex-col gap-1">
      <p className="text-11 text-tertiary">
        {t("workspace_settings.settings.service_principals.project_access.coverage.granted_to_prefix")}{" "}
        <span className="font-medium text-secondary">{visibleGrantedProjectIds.size}</span>{" "}
        {t("workspace_settings.settings.service_principals.project_access.coverage.granted_to_middle")}{" "}
        <span className="font-medium text-secondary">{workspaceProjects.length}</span>{" "}
        {t(
          workspaceProjects.length === 1
            ? "workspace_settings.settings.service_principals.project_access.coverage.granted_to_suffix_one"
            : "workspace_settings.settings.service_principals.project_access.coverage.granted_to_suffix_other"
        )}
      </p>
      {missingCount > 0 ? (
        missingCount <= 3 ? (
          <p className="truncate text-11 text-placeholder">
            {t("workspace_settings.settings.service_principals.project_access.coverage.not_granted_prefix")}
            {missingProjects.map((p) => p.name).join(", ")}
          </p>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate text-11 text-placeholder">
              {t("workspace_settings.settings.service_principals.project_access.coverage.not_granted_prefix")}
              {missingProjects
                .slice(0, 3)
                .map((p) => p.name)
                .join(", ")}
              {t("workspace_settings.settings.service_principals.project_access.coverage.not_granted_more", {
                more: missingCount - 3,
              })}
            </p>
            {showOverflowCta && onManageGrants && (
              <button
                type="button"
                onClick={onManageGrants}
                className="cursor-pointer text-11 text-accent-primary underline-offset-2 hover:text-accent-secondary hover:underline"
              >
                {t("workspace_settings.settings.service_principals.project_access.coverage.manage_grants_cta")}
              </button>
            )}
          </div>
        )
      ) : (
        <p className="text-11 text-success-primary">
          {t("workspace_settings.settings.service_principals.project_access.coverage.all_covered")}
        </p>
      )}
    </div>
  );
});
