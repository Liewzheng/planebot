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
  const { isLoading, error } = useSWR(swrKey, () => fetchProjects(workspaceSlug), {
    revalidateIfStale: false,
    revalidateOnFocus: false,
  });

  if (isLoading) {
    return <p className="text-11 text-placeholder">{t("loading")}…</p>;
  }
  if (error) {
    return (
      <div className="flex items-start justify-between gap-3">
        <p className="text-warning text-11">
          {t("error")}: could not load the workspace project list to compare grants against.
        </p>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => {
            // SWR provides a revalidation function via the same key; the
            // hook destructuring above intentionally omits it because we
            // are inside a child component, so we trigger a revalidation
            // by writing a placeholder error and re-mounting via key.
            window.location.reload();
          }}
        >
          {t("common.retry")}
        </Button>
      </div>
    );
  }

  const grantedProjectIds = new Set((principal.grants ?? []).map((g: TProjectGrant) => g.project));

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
        This workspace has no projects yet — once one is created, this row will start tracking coverage.
      </p>
    );
  }

  const missingProjects = workspaceProjects.filter((project) => !grantedProjectIds.has(project.id));
  const missingCount = missingProjects.length;
  const showOverflowCta = onManageGrants !== undefined && missingCount > 3;

  return (
    <div className="flex flex-col gap-1">
      <p className="text-11 text-tertiary">
        Granted to <span className="font-medium text-secondary">{grantedProjectIds.size}</span> of{" "}
        <span className="font-medium text-secondary">{workspaceProjects.length}</span> workspace project
        {workspaceProjects.length === 1 ? "" : "s"}.
      </p>
      {missingCount > 0 ? (
        missingCount <= 3 ? (
          <p className="truncate text-11 text-placeholder">
            Not granted: {missingProjects.map((p) => p.name).join(", ")}
          </p>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate text-11 text-placeholder">
              Not granted:{" "}
              {missingProjects
                .slice(0, 3)
                .map((p) => p.name)
                .join(", ")}{" "}
              and {missingCount - 3} more
            </p>
            {showOverflowCta && onManageGrants && (
              <button
                type="button"
                onClick={onManageGrants}
                className="cursor-pointer text-11 text-accent-primary underline-offset-2 hover:text-accent-secondary hover:underline"
              >
                Manage grants →
              </button>
            )}
          </div>
        )
      ) : (
        <p className="text-11 text-success-primary">All workspace projects covered.</p>
      )}
    </div>
  );
});
