/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import { useTranslation } from "@plane/i18n";
import { ChevronDownIcon, PlusIcon, TrashIcon } from "@plane/propel/icons";
import { Button } from "@plane/propel/button";
import type { TProjectGrantInput, TServicePrincipalRoleCap } from "@plane/types";
import { CustomSelect } from "@plane/ui";
// local imports
import { SERVICE_PRINCIPAL_GRANT_ROLE_CAPS, getScopeValueLabel } from "./constants";
import type { TGrantRow } from "./constants";

const KEY_PREFIX = "workspace_settings.settings.service_principals";

type Props = {
  rows: TGrantRow[];
  workspaceProjects: { id: string; name: string }[];
  projectNameById: Map<string, string>;
  disabled?: boolean;
  onUpdate: (rowKey: string, patch: Partial<TProjectGrantInput>) => void;
  onRemove: (rowKey: string) => void;
  onAdd: () => void;
};

/**
 * Raw grant rows: project, role cap and active flag per row. This is the escape
 * hatch behind the project × role grid — it keeps a row's status editable (the
 * grid only ever writes "role or no access") and is the only place a grant can
 * be parked at "inactive" without losing its role cap.
 */
export function GrantRowsEditor(props: Props) {
  const { rows, workspaceProjects, projectNameById, disabled, onUpdate, onRemove, onAdd } = props;
  const { t } = useTranslation();

  // Per-row filter: each row's project dropdown must not list a project another
  // row has already picked. The backend's PUT replaces the grant set but does
  // NOT deduplicate rows on (project), so two grants for the same project would
  // coexist with conflicting role_caps. The current row's own project stays in
  // the list so it still shows as selected.
  const getAvailableProjectsForRow = (rowKey: string) => {
    const takenByOtherRows = new Set(rows.filter((row) => row.key !== rowKey).map((row) => row.project));
    return workspaceProjects.filter((project) => !takenByOtherRows.has(project.id));
  };

  return (
    <div className="space-y-1.5">
      <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_2rem] gap-2 text-11 text-tertiary">
        <div>{t(`${KEY_PREFIX}.grants.project`)}</div>
        <div>{t(`${KEY_PREFIX}.grants.role_cap`)}</div>
        <div>{t(`${KEY_PREFIX}.grants.status`)}</div>
      </div>
      {rows.map((row) => (
        <div
          key={row.key}
          className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_2rem] items-center gap-2"
        >
          <div className="min-w-0">
            <CustomSelect
              className="w-full"
              customButtonClassName="w-full"
              customButton={
                <div className="flex h-8 w-full items-center justify-between gap-2 rounded-md border-[0.5px] border-subtle px-2 text-13">
                  <span className="truncate">
                    {row.project ? (projectNameById.get(row.project) ?? row.project) : "—"}
                  </span>
                  <ChevronDownIcon className="size-3 flex-shrink-0 text-tertiary" aria-hidden="true" />
                </div>
              }
              optionsClassName="max-h-60 overflow-y-auto"
              value={row.project ?? ""}
              disabled={disabled}
              onChange={(val: string) => onUpdate(row.key, { project: val })}
            >
              {getAvailableProjectsForRow(row.key).map((project) => (
                <CustomSelect.Option key={project.id} value={project.id}>
                  {project.name}
                </CustomSelect.Option>
              ))}
            </CustomSelect>
          </div>
          <div className="min-w-0">
            <CustomSelect
              className="w-full"
              customButtonClassName="w-full"
              customButton={
                <div className="flex h-8 w-full items-center justify-between gap-2 rounded-md border-[0.5px] border-subtle px-2 text-13">
                  <span className="truncate">
                    {getScopeValueLabel(`${KEY_PREFIX}.grants.role_caps.${row.role_cap}`, t)}
                  </span>
                  <ChevronDownIcon className="size-3 flex-shrink-0 text-tertiary" aria-hidden="true" />
                </div>
              }
              optionsClassName="max-h-60 overflow-y-auto"
              value={row.role_cap}
              disabled={disabled}
              onChange={(val: TServicePrincipalRoleCap) => onUpdate(row.key, { role_cap: val })}
            >
              {SERVICE_PRINCIPAL_GRANT_ROLE_CAPS.map((roleCap) => (
                <CustomSelect.Option key={roleCap} value={roleCap}>
                  {getScopeValueLabel(`${KEY_PREFIX}.grants.role_caps.${roleCap}`, t)}
                </CustomSelect.Option>
              ))}
            </CustomSelect>
          </div>
          <div className="min-w-0">
            <CustomSelect
              className="w-full"
              customButtonClassName="w-full"
              customButton={
                <div className="flex h-8 w-full items-center justify-between gap-2 rounded-md border-[0.5px] border-subtle px-2 text-13">
                  <span className="truncate">
                    {getScopeValueLabel(`${KEY_PREFIX}.grants.statuses.${row.is_active ? "active" : "inactive"}`, t)}
                  </span>
                  <ChevronDownIcon className="size-3 flex-shrink-0 text-tertiary" aria-hidden="true" />
                </div>
              }
              optionsClassName="max-h-60 overflow-y-auto"
              value={row.is_active ? "active" : "inactive"}
              disabled={disabled}
              onChange={(val: "active" | "inactive") => onUpdate(row.key, { is_active: val === "active" })}
            >
              <CustomSelect.Option value="active">{t(`${KEY_PREFIX}.grants.statuses.active`)}</CustomSelect.Option>
              <CustomSelect.Option value="inactive">{t(`${KEY_PREFIX}.grants.statuses.inactive`)}</CustomSelect.Option>
            </CustomSelect>
          </div>
          <div className="flex justify-end">
            <button
              type="button"
              disabled={disabled}
              onClick={() => onRemove(row.key)}
              className="rounded p-1 text-tertiary hover:text-danger-primary disabled:cursor-not-allowed disabled:opacity-50"
            >
              <TrashIcon className="size-3.5" />
            </button>
          </div>
        </div>
      ))}
      <Button variant="secondary" size="sm" disabled={disabled} onClick={onAdd}>
        <PlusIcon className="size-3" />
        {t(`${KEY_PREFIX}.grants.add_grant`)}
      </Button>
    </div>
  );
}
