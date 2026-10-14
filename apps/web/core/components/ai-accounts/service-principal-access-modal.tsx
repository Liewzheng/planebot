/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { observer } from "mobx-react";
import useSWR, { mutate } from "swr";
// plane imports
import { useTranslation } from "@plane/i18n";
import { ChevronDownIcon, ChevronRightIcon } from "@plane/propel/icons";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { TProjectGrant, TServicePrincipal, TServicePrincipalRoleCap } from "@plane/types";
import { EModalPosition, EModalWidth, ModalCore, AlertModalCore } from "@plane/ui";
// hooks
import { useProject } from "@/hooks/store/use-project";
import { servicePrincipalService } from "@/services/ai-account.service";
// local imports
import {
  createGrantRow,
  createScopeRow,
  SERVICE_PRINCIPALS_LIST,
  SERVICE_PRINCIPAL_GRANTS,
  SERVICE_PRINCIPAL_SCOPES,
} from "./constants";
import type { TGrantRow, TScopeRow } from "./constants";
import { GrantRowsEditor } from "./grant-rows-editor";
import { buildScopeRowsForRole, expandRoleToCellScopes, ROLE_MEMBER } from "./role-capabilities";
import { ProjectRoleTable } from "./project-role-table";
import { RoleCapabilityTable } from "./role-capability-table";
import { ScopeRowsEditor } from "./scope-rows-editor";

const KEY_PREFIX = "workspace_settings.settings.service_principals";

type Props = {
  principal: TServicePrincipal;
  isOpen: boolean;
  onClose: () => void;
  workspaceSlug: string;
};

/**
 * Single surface for a principal's access: a project × role grid on top, the raw
 * grant and scope rows collapsed underneath as "advanced", and the read-only role
 * capability table for reference.
 *
 * Picking a role for a project writes both halves of what the engine needs — a
 * grant with that role cap and the scope rows for the role (see
 * `buildScopeRowsForRole`) — because a scope without a grant, or a grant without
 * a scope, still denies everything. The advanced editors exist so nothing the
 * previous grants-only / scopes-only modals could express became unreachable.
 */
export const ServicePrincipalAccessModal = observer(function ServicePrincipalAccessModal(props: Props) {
  const { principal, isOpen, onClose, workspaceSlug } = props;
  // states
  const [grantRows, setGrantRows] = useState<TGrantRow[]>([]);
  const [scopeRows, setScopeRows] = useState<TScopeRow[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isReplaceConfirmOpen, setIsReplaceConfirmOpen] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showCapabilities, setShowCapabilities] = useState(false);
  // hooks
  const { t } = useTranslation();
  const { projectMap, workspaceProjectIds, fetchProjects } = useProject();
  // refs
  const resetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Bumped on close so a save response that lands after the modal was closed
  // cannot close or reset the state of a reopened modal
  const requestGenerationRef = useRef(0);
  // fetching workspace projects
  useSWR(
    workspaceSlug ? `WORKSPACE_PROJECTS_${workspaceSlug}` : null,
    workspaceSlug ? () => fetchProjects(workspaceSlug) : null,
    { revalidateIfStale: false, revalidateOnFocus: false }
  );
  // fetching principal scopes + grants. Focus revalidation is off so a tab
  // switch cannot refill the rows mid-edit.
  const { data: scopes, isLoading: isScopesLoading } = useSWR(
    isOpen ? SERVICE_PRINCIPAL_SCOPES(workspaceSlug, principal.id) : null,
    () => servicePrincipalService.fetchServicePrincipalScopes(workspaceSlug, principal.id),
    { revalidateIfStale: false, revalidateOnFocus: false }
  );
  const { data: grants, isLoading: isGrantsLoading } = useSWR(
    isOpen ? SERVICE_PRINCIPAL_GRANTS(workspaceSlug, principal.id) : null,
    () => servicePrincipalService.fetchServicePrincipalGrants(workspaceSlug, principal.id),
    { revalidateIfStale: false, revalidateOnFocus: false }
  );

  useEffect(() => {
    if (!scopes) return;
    setScopeRows(
      scopes.map((scope) =>
        createScopeRow({
          project: scope.project,
          resource_type: scope.resource_type,
          action: scope.action,
        })
      )
    );
  }, [scopes]);

  useEffect(() => {
    if (!grants) return;
    setGrantRows(
      grants.map((grant: TProjectGrant) =>
        createGrantRow({
          project: grant.project,
          role_cap: grant.role_cap,
          is_active: grant.is_active,
        })
      )
    );
  }, [grants]);

  const workspaceProjects = useMemo(
    () =>
      (workspaceProjectIds ?? [])
        .map((projectId) => projectMap?.[projectId])
        .filter((project) => project !== undefined)
        .map((project) => ({ id: project.id, name: project.name })),
    [projectMap, workspaceProjectIds]
  );

  // Display-only lookup over every project the store knows about, so a grant
  // whose project was archived still renders its name instead of a raw UUID.
  const projectNameById = useMemo(() => {
    const names = new Map<string, string>();
    Object.values(projectMap ?? {}).forEach((project) => {
      if (project) names.set(project.id, project.name);
    });
    return names;
  }, [projectMap]);

  const grantByProject = useMemo(() => {
    const byProject = new Map<string, TGrantRow>();
    grantRows.forEach((row) => {
      if (row.project && !byProject.has(row.project)) byProject.set(row.project, row);
    });
    return byProject;
  }, [grantRows]);

  const expandableScopeCount = useMemo(() => {
    let count = 0;
    grantRows.forEach((row) => {
      if (row.project && row.is_active) count += expandRoleToCellScopes(row.role_cap, row.project).length;
    });
    return count;
  }, [grantRows]);

  // Reopening before the delayed reset fires must cancel the timer and clear the
  // submitting lock — but NOT the rows: closing nulls the SWR keys, so the
  // hydration effects above always repopulate them on reopen.
  useEffect(() => {
    if (isOpen && resetTimerRef.current) {
      clearTimeout(resetTimerRef.current);
      resetTimerRef.current = null;
      setIsSubmitting(false);
    }
  }, [isOpen]);

  useEffect(
    () => () => {
      if (resetTimerRef.current) clearTimeout(resetTimerRef.current);
    },
    []
  );

  const handleClose = () => {
    onClose();
    requestGenerationRef.current += 1;
    resetTimerRef.current = setTimeout(() => {
      setGrantRows([]);
      setScopeRows([]);
      setIsSubmitting(false);
      setIsReplaceConfirmOpen(false);
      setShowAdvanced(false);
      setShowCapabilities(false);
      resetTimerRef.current = null;
    }, 350);
  };

  const updateGrantRow = (rowKey: string, data: Partial<TGrantRow>) =>
    setGrantRows((prevRows) => prevRows.map((row) => (row.key === rowKey ? { ...row, ...data } : row)));

  const updateScopeRow = (rowKey: string, data: Partial<TScopeRow>) =>
    setScopeRows((prevRows) => prevRows.map((row) => (row.key === rowKey ? { ...row, ...data } : row)));

  /**
   * Role picked in the grid: rewrite that project's grant, then rebuild its
   * scope rows from the role so the two halves can never disagree. Scope rows
   * for the project that the operator hand-tuned in the advanced editor are
   * replaced — the hint above the grid says so.
   */
  const handleRoleChange = (projectId: string, role: TServicePrincipalRoleCap | null) => {
    const nextRole = role;
    setGrantRows((prevRows) => {
      if (nextRole === null) return prevRows.filter((row) => row.project !== projectId);
      const index = prevRows.findIndex((row) => row.project === projectId);
      if (index === -1)
        return [...prevRows, createGrantRow({ project: projectId, role_cap: nextRole, is_active: true })];
      return prevRows.map((row, rowIndex) =>
        rowIndex === index ? { ...row, role_cap: nextRole, is_active: true } : row
      );
    });
    setScopeRows((prevRows) => {
      const withoutProject = prevRows.filter((row) => row.project !== projectId);
      if (nextRole === null) return withoutProject;
      return [...withoutProject, ...buildScopeRowsForRole(nextRole, projectId).map(createScopeRow)];
    });
  };

  const handleApplyToAll = (role: TServicePrincipalRoleCap | null) => {
    const workspaceProjectIdsSet = new Set(workspaceProjects.map((project) => project.id));
    setGrantRows((prevRows) => {
      const untouched = prevRows.filter((row) => !workspaceProjectIdsSet.has(row.project));
      if (role === null) return untouched;
      return [
        ...untouched,
        ...workspaceProjects.map((project) => createGrantRow({ project: project.id, role_cap: role, is_active: true })),
      ];
    });
    setScopeRows((prevRows) => {
      // Workspace-wide rows (`project === null`) are left alone: this control
      // only speaks about the projects listed in the grid.
      const untouched = prevRows.filter((row) => row.project === null || !workspaceProjectIdsSet.has(row.project));
      if (role === null) return untouched;
      return [
        ...untouched,
        ...workspaceProjects.flatMap((project) =>
          buildScopeRowsForRole(role, project.id).map((scopeRow) => createScopeRow(scopeRow))
        ),
      ];
    });
  };

  /** Swap every granted project's wildcard scope row for its explicit cells. */
  const handleExpandToCells = () => {
    setScopeRows((prevRows) => {
      const workspaceWide = prevRows.filter((row) => row.project === null);
      const expanded = grantRows
        .filter((row) => row.project && row.is_active)
        .flatMap((row) =>
          expandRoleToCellScopes(row.role_cap, row.project).map((scopeRow) => createScopeRow(scopeRow))
        );
      return [...workspaceWide, ...expanded];
    });
  };

  const handleSave = async () => {
    // The backend requires a project on every grant row; refuse incomplete rows
    // rather than silently sending bad payloads.
    if (grantRows.some((row) => !row.project)) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${KEY_PREFIX}.grants.error.title`),
        message: t(`${KEY_PREFIX}.grants.error.missing_project`),
      });
      return;
    }
    const generation = ++requestGenerationRef.current;
    setIsSubmitting(true);
    let step: "grants" | "scopes" = "grants";
    try {
      await servicePrincipalService.updateServicePrincipalGrants(
        workspaceSlug,
        principal.id,
        grantRows.map(({ project, role_cap, is_active }) => ({ project, role_cap, is_active }))
      );
      step = "scopes";
      await servicePrincipalService.updateServicePrincipalScopes(
        workspaceSlug,
        principal.id,
        scopeRows.map(({ project, resource_type, action }) => ({ project, resource_type, action }))
      );
      if (generation !== requestGenerationRef.current) return;
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(`${KEY_PREFIX}.access.success.title`),
        message: t(`${KEY_PREFIX}.access.success.message`),
      });
      mutate(SERVICE_PRINCIPALS_LIST(workspaceSlug));
      mutate(SERVICE_PRINCIPAL_GRANTS(workspaceSlug, principal.id));
      mutate(SERVICE_PRINCIPAL_SCOPES(workspaceSlug, principal.id));
      handleClose();
    } catch (err) {
      if (generation !== requestGenerationRef.current) return;
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${KEY_PREFIX}.access.error.title`),
        message:
          (err as { message?: string })?.message ??
          t(step === "grants" ? `${KEY_PREFIX}.access.error.step_grants` : `${KEY_PREFIX}.access.error.step_scopes`),
      });
    } finally {
      if (generation === requestGenerationRef.current) setIsSubmitting(false);
    }
  };

  const isLoading = isScopesLoading || isGrantsLoading;
  // Both PUTs replace their whole set, so a save that carries fewer rows than
  // the server holds deletes the difference. Ask before doing that; an ordinary
  // "add a project" save grows both sets and skips the prompt.
  const willRemoveRows = grantRows.length < (grants?.length ?? 0) || scopeRows.length < (scopes?.length ?? 0);

  return (
    <ModalCore isOpen={isOpen} handleClose={handleClose} position={EModalPosition.TOP} width={EModalWidth.XXL}>
      <div className="flex max-h-[80vh] flex-col">
        <div className="space-y-4 overflow-y-auto p-5">
          <div className="space-y-1">
            <h3 className="text-18 font-medium text-secondary">{t(`${KEY_PREFIX}.access.title`)}</h3>
            <p className="text-13 text-placeholder">{t(`${KEY_PREFIX}.access.description`)}</p>
          </div>
          {isLoading ? (
            <div className="py-4 text-13 text-placeholder">{t("loading")}</div>
          ) : (
            <>
              <ProjectRoleTable
                projects={workspaceProjects}
                grantByProject={grantByProject}
                disabled={isSubmitting}
                onRoleChange={handleRoleChange}
                onApplyToAll={handleApplyToAll}
              />
              <p className="rounded-md border-[0.5px] border-subtle bg-layer-2 px-3 py-2 text-11 text-placeholder">
                {t(`${KEY_PREFIX}.access.table_hint`)}
              </p>
              <SectionToggle
                isOpen={showAdvanced}
                onToggle={() => setShowAdvanced((prev) => !prev)}
                label={t(`${KEY_PREFIX}.access.advanced_title`)}
              >
                {showAdvanced && (
                  <div className="space-y-4 rounded-md border-[0.5px] border-subtle p-3">
                    <div className="space-y-2">
                      <h4 className="text-12 font-medium text-secondary">{t(`${KEY_PREFIX}.grants.title`)}</h4>
                      <GrantRowsEditor
                        rows={grantRows}
                        workspaceProjects={workspaceProjects}
                        projectNameById={projectNameById}
                        disabled={isSubmitting}
                        onUpdate={updateGrantRow}
                        onRemove={(rowKey) => setGrantRows((prevRows) => prevRows.filter((row) => row.key !== rowKey))}
                        onAdd={() =>
                          setGrantRows((prevRows) => [
                            ...prevRows,
                            createGrantRow({ project: "", role_cap: ROLE_MEMBER, is_active: true }),
                          ])
                        }
                      />
                    </div>
                    <div className="space-y-2">
                      <h4 className="text-12 font-medium text-secondary">{t(`${KEY_PREFIX}.scopes.title`)}</h4>
                      <ScopeRowsEditor
                        rows={scopeRows}
                        workspaceProjects={workspaceProjects}
                        disabled={isSubmitting}
                        onUpdate={updateScopeRow}
                        onRemove={(rowKey) => setScopeRows((prevRows) => prevRows.filter((row) => row.key !== rowKey))}
                        onAdd={() =>
                          setScopeRows((prevRows) => [
                            ...prevRows,
                            createScopeRow({ project: null, resource_type: "all", action: "all" }),
                          ])
                        }
                      />
                      <div className="flex flex-wrap items-center gap-2">
                        <Button
                          variant="secondary"
                          size="sm"
                          disabled={isSubmitting || expandableScopeCount === 0}
                          onClick={handleExpandToCells}
                        >
                          {t(`${KEY_PREFIX}.access.expand_cells_button`)}
                        </Button>
                        <span className="text-11 text-placeholder">
                          {t(`${KEY_PREFIX}.access.expand_cells_hint`, { count: expandableScopeCount })}
                        </span>
                      </div>
                    </div>
                  </div>
                )}
              </SectionToggle>
              <SectionToggle
                isOpen={showCapabilities}
                onToggle={() => setShowCapabilities((prev) => !prev)}
                label={t(`${KEY_PREFIX}.capabilities.title`)}
              >
                {showCapabilities && <RoleCapabilityTable />}
              </SectionToggle>
            </>
          )}
          <p className="text-warning text-11">{t(`${KEY_PREFIX}.access.replace_notice`)}</p>
        </div>
        <div className="flex items-center justify-end gap-2 border-t-[0.5px] border-subtle px-5 py-4">
          <Button variant="secondary" onClick={handleClose}>
            {t("cancel")}
          </Button>
          <Button
            variant="primary"
            onClick={() => {
              if (willRemoveRows) setIsReplaceConfirmOpen(true);
              else void handleSave();
            }}
            loading={isSubmitting}
            disabled={isSubmitting || isLoading}
          >
            {isSubmitting ? t(`${KEY_PREFIX}.access.saving`) : t(`${KEY_PREFIX}.access.save`)}
          </Button>
        </div>
      </div>
      <AlertModalCore
        isOpen={isReplaceConfirmOpen}
        handleClose={() => setIsReplaceConfirmOpen(false)}
        handleSubmit={() => {
          setIsReplaceConfirmOpen(false);
          void handleSave();
        }}
        isSubmitting={isSubmitting}
        primaryButtonText={{
          loading: t(`${KEY_PREFIX}.access.saving`),
          default: t(`${KEY_PREFIX}.access.replace_confirm.confirm`),
        }}
        secondaryButtonText={t("cancel")}
        title={t(`${KEY_PREFIX}.access.replace_confirm.title`)}
        content={t(`${KEY_PREFIX}.access.replace_confirm.message`, {
          grants_before: grants?.length ?? 0,
          grants_after: grantRows.length,
          scopes_before: scopes?.length ?? 0,
          scopes_after: scopeRows.length,
        })}
      />
    </ModalCore>
  );
});

function SectionToggle(props: { isOpen: boolean; onToggle: () => void; label: string; children: React.ReactNode }) {
  const { isOpen, onToggle, label, children } = props;
  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={isOpen}
        className="flex w-full items-center gap-1.5 text-left text-13 text-secondary hover:text-primary"
      >
        {isOpen ? <ChevronDownIcon className="size-3" /> : <ChevronRightIcon className="size-3" />}
        {label}
      </button>
      {children}
    </div>
  );
}
