/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useRef, useState } from "react";
import { observer } from "mobx-react";
import useSWR, { mutate } from "swr";
// plane imports
import { useTranslation } from "@plane/i18n";
import { ChevronDownIcon, PlusIcon, TrashIcon } from "@plane/propel/icons";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type {
  TServicePrincipal,
  TServiceScopeAction,
  TServiceScopeInput,
  TServiceScopeResourceType,
} from "@plane/types";
import { CustomSelect, EModalPosition, EModalWidth, ModalCore, AlertModalCore } from "@plane/ui";
// hooks
import { useProject } from "@/hooks/store/use-project";
import { servicePrincipalService } from "@/services/ai-account.service";
// local imports
import {
  SERVICE_PRINCIPALS_LIST,
  SERVICE_PRINCIPAL_SCOPES,
  SERVICE_PRINCIPAL_SCOPE_ACTIONS,
  SERVICE_PRINCIPAL_SCOPE_RESOURCE_TYPES,
} from "./constants";

type Props = {
  principal: TServicePrincipal;
  isOpen: boolean;
  onClose: () => void;
  workspaceSlug: string;
};

type TScopeRow = TServiceScopeInput & { key: string };

const getScopeRowKey = () => `scope-row-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

const createScopeRow = (data: TServiceScopeInput): TScopeRow => ({
  ...data,
  key: getScopeRowKey(),
});

export const ServicePrincipalScopesModal = observer(function ServicePrincipalScopesModal(props: Props) {
  const { principal, isOpen, onClose, workspaceSlug } = props;
  // states
  const [scopeRows, setScopeRows] = useState<TScopeRow[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isClearConfirmOpen, setIsClearConfirmOpen] = useState(false);
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
  // fetching principal scopes
  const { data: scopes, isLoading } = useSWR(
    isOpen ? SERVICE_PRINCIPAL_SCOPES(workspaceSlug, principal.id) : null,
    () => servicePrincipalService.fetchServicePrincipalScopes(workspaceSlug, principal.id)
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

  const workspaceProjects = (workspaceProjectIds ?? [])
    .map((projectId) => projectMap?.[projectId])
    .filter((project) => project !== undefined);

  const updateScopeRow = (rowKey: string, data: Partial<TServiceScopeInput>) =>
    setScopeRows((prevRows) => prevRows.map((row) => (row.key === rowKey ? { ...row, ...data } : row)));

  const removeScopeRow = (rowKey: string) => setScopeRows((prevRows) => prevRows.filter((row) => row.key !== rowKey));

  // Reopening before the delayed reset fires must cancel the timer and clear
  // the submitting lock — but NOT the rows: closing nulls the SWR key, so the
  // hydration effect above always repopulates scopeRows on reopen (and it runs
  // before this effect), wiping them here could send an empty save that
  // deletes all existing policies
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
      setScopeRows([]);
      setIsSubmitting(false);
      resetTimerRef.current = null;
    }, 350);
  };

  const handleUpdateScopes = async () => {
    const generation = ++requestGenerationRef.current;
    setIsSubmitting(true);
    try {
      await servicePrincipalService.updateServicePrincipalScopes(
        workspaceSlug,
        principal.id,
        scopeRows.map(({ project, resource_type, action }) => ({ project, resource_type, action }))
      );
      // Stale completion: the modal was closed (and maybe reopened) while the
      // request was in flight — never touch the new session's state
      if (generation !== requestGenerationRef.current) return;
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("workspace_settings.settings.service_principals.scopes.success.title"),
        message: t("workspace_settings.settings.service_principals.scopes.success.message"),
      });
      mutate(SERVICE_PRINCIPALS_LIST(workspaceSlug));
      mutate(SERVICE_PRINCIPAL_SCOPES(workspaceSlug, principal.id));
      handleClose();
    } catch (err) {
      if (generation !== requestGenerationRef.current) return;
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("workspace_settings.settings.service_principals.scopes.error.title"),
        message:
          (err as { message?: string })?.message ??
          t("workspace_settings.settings.service_principals.scopes.error.message"),
      });
    } finally {
      if (generation === requestGenerationRef.current) setIsSubmitting(false);
    }
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={handleClose} position={EModalPosition.TOP} width={EModalWidth.XXL}>
      <div className="flex max-h-[70vh] flex-col">
        <div className="space-y-3 overflow-y-auto p-5">
          <h3 className="text-18 font-medium text-secondary">
            {t("workspace_settings.settings.service_principals.scopes.title")}
          </h3>
          <p className="text-13 text-placeholder">
            {t("workspace_settings.settings.service_principals.scopes.description")}
          </p>
          <div className="space-y-1.5">
            <div className="grid grid-cols-[1fr_1fr_1fr_2rem] gap-2 text-11 text-tertiary">
              <div>{t("workspace_settings.settings.service_principals.scopes.project")}</div>
              <div>{t("workspace_settings.settings.service_principals.scopes.resource_type")}</div>
              <div>{t("workspace_settings.settings.service_principals.scopes.action")}</div>
            </div>
            {isLoading ? (
              <div className="py-4 text-13 text-placeholder">{t("loading")}</div>
            ) : (
              scopeRows.map((row) => (
                <div key={row.key} className="grid grid-cols-[1fr_1fr_1fr_2rem] items-center gap-2">
                  <div>
                    <CustomSelect
                      className="w-full"
                      customButtonClassName="w-full"
                      customButton={
                        <div className="flex h-8 w-full items-center justify-between gap-2 rounded-md border-[0.5px] border-subtle px-2 text-13">
                          <span className="truncate">
                            {row.project
                              ? (projectMap?.[row.project]?.name ?? row.project)
                              : t("workspace_settings.settings.service_principals.scopes.all_projects")}
                          </span>
                          <ChevronDownIcon className="size-3 flex-shrink-0 text-tertiary" aria-hidden="true" />
                        </div>
                      }
                      optionsClassName="max-h-60 overflow-y-auto"
                      value={row.project ?? "all"}
                      onChange={(val: string) => updateScopeRow(row.key, { project: val === "all" ? null : val })}
                    >
                      <CustomSelect.Option value="all">
                        {t("workspace_settings.settings.service_principals.scopes.all_projects")}
                      </CustomSelect.Option>
                      {workspaceProjects.map((project) => (
                        <CustomSelect.Option key={project.id} value={project.id}>
                          {project.name}
                        </CustomSelect.Option>
                      ))}
                    </CustomSelect>
                  </div>
                  <div>
                    <CustomSelect
                      className="w-full"
                      customButtonClassName="w-full"
                      customButton={
                        <div className="flex h-8 w-full items-center justify-between gap-2 rounded-md border-[0.5px] border-subtle px-2 text-13">
                          <span className="truncate">
                            {t(`workspace_settings.settings.service_principals.scopes.resources.${row.resource_type}`)}
                          </span>
                          <ChevronDownIcon className="size-3 flex-shrink-0 text-tertiary" aria-hidden="true" />
                        </div>
                      }
                      optionsClassName="max-h-60 overflow-y-auto"
                      value={row.resource_type}
                      onChange={(val: TServiceScopeResourceType) => updateScopeRow(row.key, { resource_type: val })}
                    >
                      {SERVICE_PRINCIPAL_SCOPE_RESOURCE_TYPES.map((resourceType) => (
                        <CustomSelect.Option key={resourceType} value={resourceType}>
                          {t(`workspace_settings.settings.service_principals.scopes.resources.${resourceType}`)}
                        </CustomSelect.Option>
                      ))}
                    </CustomSelect>
                  </div>
                  <div>
                    <CustomSelect
                      className="w-full"
                      customButtonClassName="w-full"
                      customButton={
                        <div className="flex h-8 w-full items-center justify-between gap-2 rounded-md border-[0.5px] border-subtle px-2 text-13">
                          <span className="truncate">
                            {t(`workspace_settings.settings.service_principals.scopes.actions.${row.action}`)}
                          </span>
                          <ChevronDownIcon className="size-3 flex-shrink-0 text-tertiary" aria-hidden="true" />
                        </div>
                      }
                      optionsClassName="max-h-60 overflow-y-auto"
                      value={row.action}
                      onChange={(val: TServiceScopeAction) => updateScopeRow(row.key, { action: val })}
                    >
                      {SERVICE_PRINCIPAL_SCOPE_ACTIONS.map((action) => (
                        <CustomSelect.Option key={action} value={action}>
                          {t(`workspace_settings.settings.service_principals.scopes.actions.${action}`)}
                        </CustomSelect.Option>
                      ))}
                    </CustomSelect>
                  </div>
                  <div className="flex justify-end">
                    <button
                      type="button"
                      onClick={() => removeScopeRow(row.key)}
                      className="rounded p-1 text-tertiary hover:text-danger-primary"
                    >
                      <TrashIcon className="size-3.5" />
                    </button>
                  </div>
                </div>
              ))
            )}
            <Button
              variant="secondary"
              size="sm"
              onClick={() =>
                setScopeRows((prevRows) => [
                  ...prevRows,
                  createScopeRow({ project: null, resource_type: "all", action: "all" }),
                ])
              }
            >
              <PlusIcon className="size-3" />
              {t("workspace_settings.settings.service_principals.scopes.add_scope")}
            </Button>
          </div>
        </div>
        <div className="flex items-center justify-end gap-2 border-t-[0.5px] border-subtle px-5 py-4">
          <Button variant="secondary" onClick={handleClose}>
            {t("cancel")}
          </Button>
          <Button
            variant="primary"
            // Saving an empty list wipes every scope this principal has, and
            // the principal then denies everything by default: ask first.
            onClick={() => (scopeRows.length === 0 ? setIsClearConfirmOpen(true) : handleUpdateScopes())}
            loading={isSubmitting}
            disabled={isSubmitting}
          >
            {isSubmitting
              ? t("workspace_settings.settings.service_principals.scopes.saving")
              : t("workspace_settings.settings.service_principals.scopes.save")}
          </Button>
        </div>
      </div>
      <AlertModalCore
        isOpen={isClearConfirmOpen}
        handleClose={() => setIsClearConfirmOpen(false)}
        handleSubmit={() => {
          setIsClearConfirmOpen(false);
          void handleUpdateScopes();
        }}
        isSubmitting={isSubmitting}
        primaryButtonText={{
          loading: t("workspace_settings.settings.service_principals.scopes.saving"),
          default: t("workspace_settings.settings.service_principals.scopes.clear_confirm.confirm"),
        }}
        secondaryButtonText={t("cancel")}
        title={t("workspace_settings.settings.service_principals.scopes.clear_confirm.title")}
        content={t("workspace_settings.settings.service_principals.scopes.clear_confirm.message")}
      />
    </ModalCore>
  );
});
