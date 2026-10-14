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
import {
  SERVICE_PRINCIPALS_LIST,
  SERVICE_PRINCIPAL_GRANTS,
  SERVICE_PRINCIPAL_GRANT_ROLE_CAPS,
  SERVICE_PRINCIPAL_SCOPES,
} from "./constants";
import { buildProjectGrantRows, buildProjectScopeRows, DEFAULT_PROJECT_ROLE_CAP } from "./project-access-payloads";

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
 * `ProjectMember` row — so the toggle below drives the project's *grant* on
 * `/api/workspaces/<slug>/service-principals/<pk>/grants/` **and** the scope
 * rows on `/api/workspaces/<slug>/service-principals/<pk>/scopes/`. The engine
 * needs both halves: a grant without a scope denies with `DENY_SCOPE_MISS`, a
 * scope without a grant denies with `DENY_GRANT_MISS`. Toggling on therefore
 * writes both (the scope rows come from the same role -> scope mapping the
 * access modal uses — see `project-access-payloads.ts`), and toggling off
 * removes only the grant, leaving the scope rows so the deny is attributed to
 * the missing grant rather than a misleading scope miss.
 *
 * Both PUTs replace their whole set, so every mutation reads the full set
 * first: grants through `applyServicePrincipalGrantMutation` (the toggle
 * unwraps it because the scope half needs the role cap the grant write just
 * resolved), scopes through an explicit read-modify-write — the scope half is
 * only ever written on the grant path.
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
    // Names the failing half in the toast: a grants-only failure leaves the
    // principal untouched, a scopes-only failure leaves it denied (the engine
    // needs both halves).
    let step: "grants" | "scopes" = "grants";
    try {
      // `PUT .../grants/` replaces the whole set, so read it first — the same
      // read-modify-write contract `applyServicePrincipalGrantMutation`
      // enforces. It is unwrapped here only because the scope half below needs
      // the role cap the grant write just resolved.
      const currentGrants = await servicePrincipalService.fetchServicePrincipalGrants(workspaceSlug, principal.id);
      const nextGrants = buildProjectGrantRows(currentGrants, projectId, nextIsGranted);
      const roleCap = nextGrants.find((grant) => grant.project === projectId)?.role_cap ?? DEFAULT_PROJECT_ROLE_CAP;
      await servicePrincipalService.updateServicePrincipalGrants(workspaceSlug, principal.id, nextGrants);
      if (nextIsGranted) {
        // Writing the grant alone is the M28 bug: the switch read "Granted"
        // while the engine denied every call with DENY_SCOPE_MISS. Rebuild this
        // project's scope rows from the role cap through the same mapping the
        // access modal uses, so both halves of the engine's gate are filled.
        step = "scopes";
        const currentScopes = await servicePrincipalService.fetchServicePrincipalScopes(workspaceSlug, principal.id);
        await servicePrincipalService.updateServicePrincipalScopes(
          workspaceSlug,
          principal.id,
          buildProjectScopeRows(currentScopes, projectId, roleCap)
        );
      }
      // Refresh the per-SP caches so coverage panels elsewhere see the change,
      // plus the principal list (which embeds `grants`).
      mutate(SERVICE_PRINCIPAL_GRANTS(workspaceSlug, principal.id));
      if (nextIsGranted) mutate(SERVICE_PRINCIPAL_SCOPES(workspaceSlug, principal.id));
      mutate(SERVICE_PRINCIPALS_LIST(workspaceSlug));
    } catch (err) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("error"),
        message:
          (err as { message?: string })?.message ??
          (err as { detail?: string })?.detail ??
          t(
            step === "grants"
              ? "workspace_settings.settings.service_principals.access.error.step_grants"
              : "workspace_settings.settings.service_principals.access.error.step_scopes"
          ),
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
          (err as { detail?: string })?.detail ??
          t("workspace_settings.settings.service_principals.project_access.errors.update_role_cap_failed"),
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
          (err as { detail?: string })?.detail ??
          t("workspace_settings.settings.service_principals.project_access.errors.update_status_failed"),
      });
    } finally {
      setPendingPrincipalId(null);
    }
  };

  return (
    <div className="mt-10 flex flex-col gap-4">
      <SettingsBoxedControlItem
        className="flex-col items-stretch"
        title={t("workspace_settings.settings.service_principals.project_access.section.title")}
        description={t("workspace_settings.settings.service_principals.project_access.section.description")}
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
          message={t("workspace_settings.settings.service_principals.project_access.list.error_loading")}
          onRetry={() => mutate(SERVICE_PRINCIPALS_LIST(workspaceSlug))}
          t={t}
        />
      ) : !principals || principals.length === 0 ? (
        <p className="rounded-md border border-subtle bg-layer-2 p-4 text-13 text-tertiary">
          {t("workspace_settings.settings.service_principals.project_access.list.empty_state_before_path")}{" "}
          <strong>{t("workspace_settings.settings.service_principals.project_access.list.settings_path")}</strong>
          {t("workspace_settings.settings.service_principals.project_access.list.empty_state_after_path")}
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
                      <p className="text-warning truncate text-11">
                        {t(
                          "workspace_settings.settings.service_principals.project_access.list.inactive_cannot_authenticate"
                        )}
                      </p>
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
                            <span className="truncate">{renderRoleCapLabel(grantForThisProject.role_cap, t)}</span>
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
                            {renderRoleCapLabel(roleCap, t)}
                          </CustomSelect.Option>
                        ))}
                      </CustomSelect>
                      <span className="flex items-center gap-1.5 text-12 text-tertiary">
                        <span
                          className={grantForThisProject.is_active ? "text-success-primary" : "text-warning"}
                          aria-hidden
                        >
                          {t(
                            grantForThisProject.is_active
                              ? "workspace_settings.settings.service_principals.project_access.list.active"
                              : "workspace_settings.settings.service_principals.project_access.list.inactive"
                          )}
                        </span>
                        <Switch
                          size="sm"
                          checked={grantForThisProject.is_active}
                          onCheckedChange={(next: boolean) => void handleActiveChange(principal, projectId, next)}
                          disabled={isAnyMutationPending}
                          aria-label={t(
                            "workspace_settings.settings.service_principals.project_access.list.aria_active_grant_for",
                            { name: principal.name }
                          )}
                        />
                      </span>
                    </>
                  ) : null}
                  <span className="flex items-center gap-2 text-12 text-tertiary">
                    <span>
                      {t(
                        isGranted
                          ? "workspace_settings.settings.service_principals.project_access.list.granted"
                          : "workspace_settings.settings.service_principals.project_access.list.not_granted"
                      )}
                    </span>
                    <Switch
                      size="sm"
                      checked={isGranted}
                      onCheckedChange={(next: boolean) => void handleToggleGrant(principal, next)}
                      disabled={isAnyMutationPending || !principal.is_active}
                      aria-label={t(
                        "workspace_settings.settings.service_principals.project_access.list.aria_grant_access_to",
                        { name: principal.name }
                      )}
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

function ErrorState(props: { message: string; onRetry: () => void; t: (key: string) => string }) {
  const { message, onRetry, t } = props;
  return (
    <div className="flex items-start justify-between gap-4 rounded-md border border-subtle bg-layer-2 p-4">
      <div className="flex flex-col gap-1">
        <p className="text-13 font-medium text-secondary">{t("error")}</p>
        <p className="text-13 text-tertiary">{message}</p>
      </div>
      <Button variant="secondary" size="sm" onClick={onRetry}>
        {t("common.retry")}
      </Button>
    </div>
  );
}

function renderRoleCapLabel(roleCap: TServicePrincipalRoleCap, t: (key: string) => string): string {
  switch (roleCap) {
    case 20:
      return t("workspace_settings.settings.service_principals.grants.role_caps.20");
    case 15:
      return t("workspace_settings.settings.service_principals.grants.role_caps.15");
    case 5:
      return t("workspace_settings.settings.service_principals.grants.role_caps.5");
    default:
      return String(roleCap);
  }
}
