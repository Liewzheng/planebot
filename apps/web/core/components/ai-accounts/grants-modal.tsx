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
import type { TProjectGrant, TProjectGrantInput, TServicePrincipal, TServicePrincipalRoleCap } from "@plane/types";
import { CustomSelect, EModalPosition, EModalWidth, ModalCore, AlertModalCore } from "@plane/ui";
// hooks
import { useProject } from "@/hooks/store/use-project";
import { servicePrincipalService } from "@/services/ai-account.service";
// local imports
import {
  SERVICE_PRINCIPALS_LIST,
  SERVICE_PRINCIPAL_GRANTS,
  SERVICE_PRINCIPAL_GRANT_ROLE_CAPS,
} from "./constants";

type Props = {
  principal: TServicePrincipal;
  isOpen: boolean;
  onClose: () => void;
  workspaceSlug: string;
};

type TGrantRow = TProjectGrantInput & { key: string };

const getGrantRowKey = () => `grant-row-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

const createGrantRow = (data: TProjectGrantInput): TGrantRow => ({
  ...data,
  key: getGrantRowKey(),
});

export const ServicePrincipalGrantsModal = observer(function ServicePrincipalGrantsModal(props: Props) {
  const { principal, isOpen, onClose, workspaceSlug } = props;
  // states
  const [grantRows, setGrantRows] = useState<TGrantRow[]>([]);
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
  // fetching principal grants
  const { data: grants, isLoading } = useSWR(
    isOpen ? SERVICE_PRINCIPAL_GRANTS(workspaceSlug, principal.id) : null,
    () => servicePrincipalService.fetchServicePrincipalGrants(workspaceSlug, principal.id)
  );

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

  const workspaceProjects = (workspaceProjectIds ?? [])
    .map((projectId) => projectMap?.[projectId])
    .filter((project) => project !== undefined);

  const updateGrantRow = (rowKey: string, data: Partial<TProjectGrantInput>) =>
    setGrantRows((prevRows) => prevRows.map((row) => (row.key === rowKey ? { ...row, ...data } : row)));

  const removeGrantRow = (rowKey: string) => setGrantRows((prevRows) => prevRows.filter((row) => row.key !== rowKey));

  // Reopening before the delayed reset fires must cancel the timer and clear
  // the submitting lock — but NOT the rows: closing nulls the SWR key, so the
  // hydration effect above always repopulates grantRows on reopen
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
      setIsSubmitting(false);
      resetTimerRef.current = null;
    }, 350);
  };

  const handleUpdateGrants = async () => {
    // Backend requires project on every grant row; refuse incomplete rows
    // rather than silently sending bad payloads.
    if (grantRows.some((row) => !row.project)) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("workspace_settings.settings.service_principals.grants.error.title"),
        message: t("workspace_settings.settings.service_principals.grants.error.missing_project"),
      });
      return;
    }
    const generation = ++requestGenerationRef.current;
    setIsSubmitting(true);
    try {
      await servicePrincipalService.updateServicePrincipalGrants(
        workspaceSlug,
        principal.id,
        grantRows.map(({ project, role_cap, is_active }) => ({ project, role_cap, is_active }))
      );
      if (generation !== requestGenerationRef.current) return;
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("workspace_settings.settings.service_principals.grants.success.title"),
        message: t("workspace_settings.settings.service_principals.grants.success.message"),
      });
      mutate(SERVICE_PRINCIPALS_LIST(workspaceSlug));
      mutate(SERVICE_PRINCIPAL_GRANTS(workspaceSlug, principal.id));
      handleClose();
    } catch (err) {
      if (generation !== requestGenerationRef.current) return;
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("workspace_settings.settings.service_principals.grants.error.title"),
        message:
          (err as { message?: string })?.message ??
          t("workspace_settings.settings.service_principals.grants.error.message"),
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
            {t("workspace_settings.settings.service_principals.grants.title")}
          </h3>
          <p className="text-13 text-placeholder">
            {t("workspace_settings.settings.service_principals.grants.description")}
          </p>
          <div className="space-y-1.5">
            <div className="grid grid-cols-[1fr_1fr_1fr_2rem] gap-2 text-11 text-tertiary">
              <div>{t("workspace_settings.settings.service_principals.grants.project")}</div>
              <div>{t("workspace_settings.settings.service_principals.grants.role_cap")}</div>
              <div>{t("workspace_settings.settings.service_principals.grants.status")}</div>
            </div>
            {isLoading ? (
              <div className="py-4 text-13 text-placeholder">{t("loading")}</div>
            ) : (
              grantRows.map((row) => (
                <div key={row.key} className="grid grid-cols-[1fr_1fr_1fr_2rem] items-center gap-2">
                  <div>
                    <CustomSelect
                      className="w-full"
                      customButtonClassName="w-full"
                      customButton={
                        <div className="flex h-8 w-full items-center justify-between gap-2 rounded-md border-[0.5px] border-subtle px-2 text-13">
                          <span className="truncate">
                            {row.project ? (projectMap?.[row.project]?.name ?? row.project) : "—"}
                          </span>
                          <ChevronDownIcon className="size-3 flex-shrink-0 text-tertiary" aria-hidden="true" />
                        </div>
                      }
                      optionsClassName="max-h-60 overflow-y-auto"
                      value={row.project ?? ""}
                      onChange={(val: string) => updateGrantRow(row.key, { project: val })}
                    >
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
                            {t(
                              `workspace_settings.settings.service_principals.grants.role_caps.${row.role_cap}`
                            )}
                          </span>
                          <ChevronDownIcon className="size-3 flex-shrink-0 text-tertiary" aria-hidden="true" />
                        </div>
                      }
                      optionsClassName="max-h-60 overflow-y-auto"
                      value={row.role_cap}
                      onChange={(val: TServicePrincipalRoleCap) => updateGrantRow(row.key, { role_cap: val })}
                    >
                      {SERVICE_PRINCIPAL_GRANT_ROLE_CAPS.map((roleCap) => (
                        <CustomSelect.Option key={roleCap} value={roleCap}>
                          {t(`workspace_settings.settings.service_principals.grants.role_caps.${roleCap}`)}
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
                            {t(
                              `workspace_settings.settings.service_principals.grants.statuses.${row.is_active ? "active" : "inactive"}`
                            )}
                          </span>
                          <ChevronDownIcon className="size-3 flex-shrink-0 text-tertiary" aria-hidden="true" />
                        </div>
                      }
                      optionsClassName="max-h-60 overflow-y-auto"
                      value={row.is_active ? "active" : "inactive"}
                      onChange={(val: "active" | "inactive") => updateGrantRow(row.key, { is_active: val === "active" })}
                    >
                      <CustomSelect.Option value="active">
                        {t("workspace_settings.settings.service_principals.grants.statuses.active")}
                      </CustomSelect.Option>
                      <CustomSelect.Option value="inactive">
                        {t("workspace_settings.settings.service_principals.grants.statuses.inactive")}
                      </CustomSelect.Option>
                    </CustomSelect>
                  </div>
                  <div className="flex justify-end">
                    <button
                      type="button"
                      onClick={() => removeGrantRow(row.key)}
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
                setGrantRows((prevRows) => [
                  ...prevRows,
                  createGrantRow({ project: "", role_cap: 15, is_active: true }),
                ])
              }
            >
              <PlusIcon className="size-3" />
              {t("workspace_settings.settings.service_principals.grants.add_grant")}
            </Button>
          </div>
        </div>
        <div className="flex items-center justify-end gap-2 border-t-[0.5px] border-subtle px-5 py-4">
          <Button variant="secondary" onClick={handleClose}>
            {t("cancel")}
          </Button>
          <Button
            variant="primary"
            // Saving an empty list removes the SP from every project, which
            // the operator may not have intended: ask first.
            onClick={() => (grantRows.length === 0 ? setIsClearConfirmOpen(true) : handleUpdateGrants())}
            loading={isSubmitting}
            disabled={isSubmitting}
          >
            {isSubmitting
              ? t("workspace_settings.settings.service_principals.grants.saving")
              : t("workspace_settings.settings.service_principals.grants.save")}
          </Button>
        </div>
      </div>
      <AlertModalCore
        isOpen={isClearConfirmOpen}
        handleClose={() => setIsClearConfirmOpen(false)}
        handleSubmit={() => {
          setIsClearConfirmOpen(false);
          void handleUpdateGrants();
        }}
        isSubmitting={isSubmitting}
        primaryButtonText={{
          loading: t("workspace_settings.settings.service_principals.grants.saving"),
          default: t("workspace_settings.settings.service_principals.grants.clear_confirm.confirm"),
        }}
        secondaryButtonText={t("cancel")}
        title={t("workspace_settings.settings.service_principals.grants.clear_confirm.title")}
        content={t("workspace_settings.settings.service_principals.grants.clear_confirm.message")}
      />
    </ModalCore>
  );
});