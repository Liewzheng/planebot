/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import useSWR, { mutate } from "swr";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { Avatar } from "@makeplane/propel/components/avatar";
import { getFileURL } from "@plane/utils";
import type { TProjectGrant, TProjectGrantInput, TServicePrincipal, TServicePrincipalRoleCap } from "@plane/types";
// components
import { SettingsBoxedControlItem } from "@/components/settings/boxed-control-item";
import { CustomSelect } from "@plane/ui";
import { Switch } from "@makeplane/propel/components/switch";
import { ChevronDownIcon } from "@plane/propel/icons";
import { Button } from "@plane/propel/button";
// hooks
import { useUserPermissions } from "@/hooks/store/user";
// services
import { servicePrincipalService } from "@/services/ai-account.service";
// local imports
import { SERVICE_PRINCIPALS_LIST, SERVICE_PRINCIPAL_GRANTS, SERVICE_PRINCIPAL_GRANT_ROLE_CAPS } from "./constants";

type Props = {
  workspaceSlug: string;
  projectId: string;
};

/**
 * Renders the "Service principals that can access this project" surface
 * inside a project's settings page. Lists every principal in the workspace
 * and shows whether the current project is granted.
 *
 * Service principals are NOT members of the project — there is no
 * `ProjectMember` row — so the toggle below only manages the *grant* on
 * `/api/workspaces/<slug>/service-principals/<pk>/grants/`. PUT replaces
 * the entire grant set, so all grant-state mutations go through
 * `applyServicePrincipalGrantMutation` which reads the full set first.
 *
 * **Permission gate**: the endpoints behind this section require
 * `level="WORKSPACE"` admin (`@allow_permission([ROLE.ADMIN], level="WORKSPACE")`
 * on `apps/api/plane/service_principals/views.py`), so we gate the visible
 * rows on `EUserPermissionsLevel.WORKSPACE` rather than `PROJECT`. A project
 * admin who is not also a workspace admin cannot drive these endpoints and
 * would otherwise see a fully actionable-looking list that 403s on fetch.
 */
export const ProjectServicePrincipalAccessSection = observer(function ProjectServicePrincipalAccessSection(
  props: Props
) {
  const { workspaceSlug, projectId } = props;
  const { t } = useTranslation();
  const { allowPermissions } = useUserPermissions();
  // The endpoints this section calls require WORKSPACE admin. A project
  // admin who is not a workspace admin would 403 on the list fetch, and
  // we explicitly want to hide them from the section rather than show
  // them an actionable-looking list that breaks on every action.
  const isWorkspaceAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE, workspaceSlug);

  // Tracks any in-flight mutation for this section. We deliberately do
  // not track per-row: while one grant mutation is pending we disable
  // every row's toggles, so a double-tap cannot race a parallel
  // read-modify-write for the same principal and silently drop the
  // first change (the lost-update failure the wrapper exists to prevent).
  const [pendingPrincipalId, setPendingPrincipalId] = useState<string | null>(null);
  const isAnyMutationPending = pendingPrincipalId !== null;

  // The list endpoint requires WORKSPACE admin. We deliberately keep the
  // SWR key null for non-admins (so no request is issued) and let the
  // downstream `data`/`error`/`isLoading` distinguish the four states:
  // (1) not yet fetched (key null)            → render nothing,
  // (2) loading                               → spinner,
  // (3) backend rejected (403 / 401 / etc.)   → explicit error / retry,
  // (4) fetch succeeded, list is empty        → "no SPs yet" empty state.
  const swrKey = isWorkspaceAdmin ? SERVICE_PRINCIPALS_LIST(workspaceSlug) : null;
  const {
    data: principals,
    error: principalsError,
    isLoading: principalsLoading,
  } = useSWR<TServicePrincipal[]>(swrKey, () => servicePrincipalService.fetchServicePrincipalsList(workspaceSlug), {
    revalidateIfStale: false,
    revalidateOnFocus: false,
  });

  // Hide from non-admins entirely — same UX as the rest of project
  // settings surfaces that require workspace admin.
  if (!isWorkspaceAdmin) return null;

  const handleToggleGrant = async (principal: TServicePrincipal, nextIsGranted: boolean) => {
    if (isAnyMutationPending) return;
    setPendingPrincipalId(principal.id);
    try {
      await servicePrincipalService.applyServicePrincipalGrantMutation(workspaceSlug, principal.id, (current) => {
        const existingForThisProject = current.find((grant) => grant.project === projectId);
        const preserved: TProjectGrantInput[] = current
          .filter((grant) => grant.project !== projectId)
          .map(({ project, role_cap, is_active }) => ({ project, role_cap, is_active }));
        if (nextIsGranted) {
          // First-time grant for this project: preserve any prior
          // role_cap / is_active from a previous grant, otherwise
          // default to the Member role cap (15) so we never grant Admin
          // implicitly. Revoke simply drops the row — the filter above
          // already excludes it.
          preserved.push({
            project: projectId,
            role_cap: existingForThisProject?.role_cap ?? 15,
            is_active: existingForThisProject?.is_active ?? true,
          });
        }
        return preserved;
      });
      // Refresh the per-SP grants cache so coverage panels elsewhere see
      // the change, plus the principal list (which embeds `grants`).
      mutate(SERVICE_PRINCIPAL_GRANTS(workspaceSlug, principal.id));
      mutate(SERVICE_PRINCIPALS_LIST(workspaceSlug));
    } catch (err) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("error"),
        message:
          (err as { message?: string })?.message ??
          "Could not update grant for this project. The principal's other grants were not changed.",
      });
    } finally {
      setPendingPrincipalId(null);
    }
  };

  const handleRoleCapChange = async (
    principal: TServicePrincipal,
    targetProjectId: string,
    nextRoleCap: TServicePrincipalRoleCap
  ) => {
    if (isAnyMutationPending) return;
    setPendingPrincipalId(principal.id);
    try {
      await servicePrincipalService.applyServicePrincipalGrantMutation(workspaceSlug, principal.id, (current) => {
        return current.map((grant) =>
          grant.project === targetProjectId ? { ...grant, role_cap: nextRoleCap } : grant
        ) as TProjectGrantInput[];
      });
      mutate(SERVICE_PRINCIPAL_GRANTS(workspaceSlug, principal.id));
      mutate(SERVICE_PRINCIPALS_LIST(workspaceSlug));
    } catch (err) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("error"),
        message:
          (err as { message?: string })?.message ??
          "Could not update the role cap. The principal's other grants were not changed.",
      });
    } finally {
      setPendingPrincipalId(null);
    }
  };

  const handleActiveChange = async (principal: TServicePrincipal, targetProjectId: string, nextActive: boolean) => {
    if (isAnyMutationPending) return;
    setPendingPrincipalId(principal.id);
    try {
      await servicePrincipalService.applyServicePrincipalGrantMutation(workspaceSlug, principal.id, (current) => {
        return current.map((grant) =>
          grant.project === targetProjectId ? { ...grant, is_active: nextActive } : grant
        ) as TProjectGrantInput[];
      });
      mutate(SERVICE_PRINCIPAL_GRANTS(workspaceSlug, principal.id));
      mutate(SERVICE_PRINCIPALS_LIST(workspaceSlug));
    } catch (err) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("error"),
        message:
          (err as { message?: string })?.message ??
          "Could not update the grant status. The principal's other grants were not changed.",
      });
    } finally {
      setPendingPrincipalId(null);
    }
  };

  return (
    <div className="mt-10 flex flex-col gap-4">
      <SettingsBoxedControlItem
        className="flex-col items-stretch"
        title="Service principal access"
        description="Service principals (machine accounts for integrations and automation) are intentionally NOT members of this project. A workspace admin can grant any principal access to this project below — toggling on adds a grant, which the principal needs in addition to its scopes. Only workspace admins can perform this action."
      />
      {principalsLoading ? (
        <p className="text-13 text-placeholder">{t("loading")}…</p>
      ) : principalsError ? (
        // The list endpoint requires WORKSPACE admin, so a 403 here is
        // the most likely failure mode for an admin whose permissions
        // were reduced. Surface it plainly instead of pretending the
        // workspace has no principals — that misleads admins into
        // creating duplicates.
        <ErrorState
          message="Could not load service principals for this workspace. You may not have permission, or the workspace list endpoint is unavailable."
          onRetry={() => mutate(SERVICE_PRINCIPALS_LIST(workspaceSlug))}
          isWorkspaceAdmin={isWorkspaceAdmin}
          t={t}
        />
      ) : !principals || principals.length === 0 ? (
        <p className="rounded-md border border-subtle bg-layer-2 p-4 text-13 text-tertiary">
          No service principals exist in this workspace yet. A workspace admin can create one under
          <strong> Workspace settings → Developer → Service principals</strong>, then come back here to grant it access
          to this project.
        </p>
      ) : (
        <ul className="divide-y divide-subtle rounded-md border border-subtle bg-layer-2">
          {principals.map((principal) => {
            const grantForThisProject = (principal.grants ?? []).find((g: TProjectGrant) => g.project === projectId);
            const isGranted = Boolean(grantForThisProject);
            return (
              <li
                key={principal.id}
                className="flex flex-col gap-2 px-4 py-3 md:flex-row md:items-center md:justify-between"
              >
                <div className="flex min-w-0 items-center gap-3">
                  <Avatar
                    src={getFileURL(principal.avatar)}
                    alt={principal.name}
                    fallback={principal.name.charAt(0)}
                    size="sm"
                    tooltip
                  />
                  <div className="min-w-0">
                    <p className="truncate text-13 font-medium text-primary">{principal.name}</p>
                    {!principal.is_active && (
                      <p className="text-warning truncate text-11">Inactive — token cannot authenticate</p>
                    )}
                  </div>
                </div>
                <div className="flex flex-wrap items-center justify-end gap-3">
                  {isGranted && grantForThisProject ? (
                    <>
                      <CustomSelect
                        className="w-32"
                        customButtonClassName="w-full"
                        customButton={
                          <div className="flex h-8 w-full items-center justify-between gap-2 rounded-md border-[0.5px] border-subtle px-2 text-13">
                            <span className="truncate">{renderRoleCapLabel(grantForThisProject.role_cap)}</span>
                            <ChevronDownIcon className="size-3 flex-shrink-0 text-tertiary" aria-hidden="true" />
                          </div>
                        }
                        optionsClassName="max-h-60 overflow-y-auto"
                        value={String(grantForThisProject.role_cap)}
                        onChange={(val: string) =>
                          void handleRoleCapChange(principal, projectId, Number(val) as TServicePrincipalRoleCap)
                        }
                        disabled={isAnyMutationPending}
                      >
                        {SERVICE_PRINCIPAL_GRANT_ROLE_CAPS.map((roleCap) => (
                          <CustomSelect.Option key={roleCap} value={String(roleCap)}>
                            {renderRoleCapLabel(roleCap)}
                          </CustomSelect.Option>
                        ))}
                      </CustomSelect>
                      <span className="flex items-center gap-1.5 text-12 text-tertiary">
                        <span
                          className={grantForThisProject.is_active ? "text-success-primary" : "text-warning"}
                          aria-hidden
                        >
                          {grantForThisProject.is_active ? "Active" : "Inactive"}
                        </span>
                        <Switch
                          size="sm"
                          checked={grantForThisProject.is_active}
                          onCheckedChange={(next: boolean) => void handleActiveChange(principal, projectId, next)}
                          disabled={isAnyMutationPending}
                          aria-label={`Active grant for ${principal.name}`}
                        />
                      </span>
                    </>
                  ) : null}
                  <span className="flex items-center gap-2 text-12 text-tertiary">
                    <span>{isGranted ? "Granted" : "Not granted"}</span>
                    <Switch
                      size="sm"
                      checked={isGranted}
                      onCheckedChange={(next: boolean) => void handleToggleGrant(principal, next)}
                      disabled={isAnyMutationPending || !principal.is_active}
                      aria-label={`Grant access to ${principal.name}`}
                    />
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
});

function ErrorState(props: {
  message: string;
  onRetry: () => void;
  isWorkspaceAdmin: boolean;
  t: (key: string) => string;
}) {
  const { message, onRetry, isWorkspaceAdmin, t } = props;
  return (
    <div className="flex items-start justify-between gap-4 rounded-md border border-subtle bg-layer-2 p-4">
      <div className="flex flex-col gap-1">
        <p className="text-13 font-medium text-secondary">{t("error")}</p>
        <p className="text-13 text-tertiary">
          {message}
          {!isWorkspaceAdmin && (
            <span> Only a workspace admin can manage service principal access for this project.</span>
          )}
        </p>
      </div>
      <Button variant="secondary" size="sm" onClick={onRetry}>
        {t("common.retry")}
      </Button>
    </div>
  );
}

function renderRoleCapLabel(roleCap: TServicePrincipalRoleCap): string {
  switch (roleCap) {
    case 20:
      return "Admin";
    case 15:
      return "Member";
    case 5:
      return "Guest";
    default:
      return String(roleCap);
  }
}
