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
 */
export const ProjectServicePrincipalAccessSection = observer(function ProjectServicePrincipalAccessSection(
  props: Props
) {
  const { workspaceSlug, projectId } = props;
  const { t } = useTranslation();
  const { allowPermissions } = useUserPermissions();
  const isProjectAdmin = allowPermissions(
    [EUserPermissions.ADMIN],
    EUserPermissionsLevel.PROJECT,
    workspaceSlug,
    projectId
  );
  // Tracks which principal currently has a grant mutation in flight, so
  // each row's switch can disable itself without us needing a per-row
  // loading flag.
  const [pendingPrincipalId, setPendingPrincipalId] = useState<string | null>(null);

  // List of all workspace SPs. Each item already carries the `grants`
  // array, but we treat that as advisory and always re-read via
  // `fetchServicePrincipalGrants` before mutating, otherwise concurrent
  // grants modifications from other tabs could be silently overwritten.
  const { data: principals, isLoading } = useSWR<TServicePrincipal[]>(
    isProjectAdmin ? SERVICE_PRINCIPALS_LIST(workspaceSlug) : null,
    isProjectAdmin ? () => servicePrincipalService.fetchServicePrincipalsList(workspaceSlug) : null,
    { revalidateIfStale: false, revalidateOnFocus: false }
  );

  // Hide the whole panel from non-admins — matches how the rest of project
  // settings behaves. Workspace admins can still see the section because
  // they're effectively project admins too.
  if (!isProjectAdmin) return null;

  const handleToggleGrant = async (principal: TServicePrincipal, nextIsGranted: boolean) => {
    setPendingPrincipalId(principal.id);
    try {
      await servicePrincipalService.applyServicePrincipalGrantMutation(workspaceSlug, principal.id, (current) => {
        const existingForThisProject = current.find((grant) => grant.project === projectId);
        const preserved = current.filter((grant) => grant.project !== projectId);
        if (nextIsGranted) {
          // Re-granting a project that was previously disabled: keep the
          // previous role_cap / is_active state if it existed, otherwise
          // pick the lowest non-admin cap so we don't accidentally elevate
          // the principal to admin on a first-time grant. Revokes simply
          // drop the row — the filter above already excludes it.
          preserved.push({
            project: projectId,
            role_cap: existingForThisProject?.role_cap ?? 15,
            is_active: existingForThisProject?.is_active ?? true,
          });
        }
        return preserved as TProjectGrantInput[];
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
        description="Service principals (machine accounts for integrations and automation) are intentionally NOT members of this project. To give one access to this project, toggle on a principal below — that adds a grant, which the principal needs in addition to its scopes."
      />
      {isLoading ? (
        <p className="text-13 text-placeholder">Loading service principals…</p>
      ) : !principals || principals.length === 0 ? (
        <p className="rounded-md border border-subtle bg-layer-2 p-4 text-13 text-tertiary">
          No service principals exist in this workspace yet. Create one under
          <strong> Workspace settings → Developer → Service principals</strong>, then come back here to grant it access
          to this project.
        </p>
      ) : (
        <ul className="divide-y divide-subtle rounded-md border border-subtle bg-layer-2">
          {principals.map((principal) => {
            const grantForThisProject = (principal.grants ?? []).find((g: TProjectGrant) => g.project === projectId);
            const isGranted = Boolean(grantForThisProject);
            const isPending = pendingPrincipalId === principal.id;
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
                        disabled={isPending || !isProjectAdmin}
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
                          disabled={isPending || !isProjectAdmin}
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
                      disabled={isPending || !isProjectAdmin || !principal.is_active}
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
