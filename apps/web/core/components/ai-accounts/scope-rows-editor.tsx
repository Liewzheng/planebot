/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import { useTranslation } from "@plane/i18n";
import { ChevronDownIcon, PlusIcon, TrashIcon } from "@plane/propel/icons";
import { Button } from "@plane/propel/button";
import type { TServiceScopeAction, TServiceScopeInput, TServiceScopeResourceType } from "@plane/types";
import { CustomSelect } from "@plane/ui";
// local imports
import {
  getScopeValueLabel,
  SERVICE_PRINCIPAL_SCOPE_ACTIONS,
  SERVICE_PRINCIPAL_SCOPE_RESOURCE_TYPES,
} from "./constants";
import type { TScopeRow } from "./constants";

const KEY_PREFIX = "workspace_settings.settings.service_principals";

type Props = {
  rows: TScopeRow[];
  workspaceProjects: { id: string; name: string }[];
  disabled?: boolean;
  onUpdate: (rowKey: string, patch: Partial<TServiceScopeInput>) => void;
  onRemove: (rowKey: string) => void;
  onAdd: () => void;
};

/**
 * Per-cell scope rows: project × resource × action. Demoted from its own modal
 * to the advanced section of the access dialog, because a role assignment in the
 * grid is the normal way to write these — the rows here are what it generated.
 */
export function ScopeRowsEditor(props: Props) {
  const { rows, workspaceProjects, disabled, onUpdate, onRemove, onAdd } = props;
  const { t } = useTranslation();

  return (
    <div className="space-y-1.5">
      <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_2rem] gap-2 text-11 text-tertiary">
        <div>{t(`${KEY_PREFIX}.scopes.project`)}</div>
        <div>{t(`${KEY_PREFIX}.scopes.resource_type`)}</div>
        <div>{t(`${KEY_PREFIX}.scopes.action`)}</div>
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
                    {row.project
                      ? (workspaceProjects.find((project) => project.id === row.project)?.name ?? row.project)
                      : getScopeValueLabel(`${KEY_PREFIX}.scopes.all_projects`, t)}
                  </span>
                  <ChevronDownIcon className="size-3 flex-shrink-0 text-tertiary" aria-hidden="true" />
                </div>
              }
              optionsClassName="max-h-60 overflow-y-auto"
              value={row.project ?? "all"}
              disabled={disabled}
              onChange={(val: string) => onUpdate(row.key, { project: val === "all" ? null : val })}
            >
              <CustomSelect.Option value="all">
                {getScopeValueLabel(`${KEY_PREFIX}.scopes.all_projects`, t)}
              </CustomSelect.Option>
              {workspaceProjects.map((project) => (
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
                    {getScopeValueLabel(`${KEY_PREFIX}.scopes.resources.${row.resource_type}`, t)}
                  </span>
                  <ChevronDownIcon className="size-3 flex-shrink-0 text-tertiary" aria-hidden="true" />
                </div>
              }
              optionsClassName="max-h-60 overflow-y-auto"
              value={row.resource_type}
              disabled={disabled}
              onChange={(val: TServiceScopeResourceType) => onUpdate(row.key, { resource_type: val })}
            >
              {SERVICE_PRINCIPAL_SCOPE_RESOURCE_TYPES.map((resourceType) => (
                <CustomSelect.Option key={resourceType} value={resourceType}>
                  {getScopeValueLabel(`${KEY_PREFIX}.scopes.resources.${resourceType}`, t)}
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
                    {getScopeValueLabel(`${KEY_PREFIX}.scopes.actions.${row.action}`, t)}
                  </span>
                  <ChevronDownIcon className="size-3 flex-shrink-0 text-tertiary" aria-hidden="true" />
                </div>
              }
              optionsClassName="max-h-60 overflow-y-auto"
              value={row.action}
              disabled={disabled}
              onChange={(val: TServiceScopeAction) => onUpdate(row.key, { action: val })}
            >
              {SERVICE_PRINCIPAL_SCOPE_ACTIONS.map((action) => (
                <CustomSelect.Option key={action} value={action}>
                  {getScopeValueLabel(`${KEY_PREFIX}.scopes.actions.${action}`, t)}
                </CustomSelect.Option>
              ))}
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
        {t(`${KEY_PREFIX}.scopes.add_scope`)}
      </Button>
    </div>
  );
}
