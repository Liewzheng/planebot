/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { ChevronDownIcon } from "@plane/propel/icons";
import { Button } from "@plane/propel/button";
import type { TProjectGrantInput, TServicePrincipalRoleCap } from "@plane/types";
import { CustomSelect } from "@plane/ui";
// local imports
import { SERVICE_PRINCIPAL_GRANT_ROLE_CAPS, getScopeValueLabel } from "./constants";
import { ROLE_MEMBER } from "./role-capabilities";

const KEY_PREFIX = "workspace_settings.settings.service_principals";

type Props = {
  projects: { id: string; name: string }[];
  grantByProject: Map<string, TProjectGrantInput>;
  disabled?: boolean;
  onRoleChange: (projectId: string, role: TServicePrincipalRoleCap | null) => void;
  onApplyToAll: (role: TServicePrincipalRoleCap | null) => void;
};

/**
 * Project × role grid — the primary surface for assigning access. Each row picks
 * the role the principal carries in one project (or "no access", which drops the
 * grant); the row's scope rows are rebuilt from that role by the caller.
 *
 * A grant that exists but is switched off is shown with its role and a disabled
 * badge rather than as "no access", so opening this table and saving never
 * silently discards a grant the operator parked in the advanced editor.
 */
export function ProjectRoleTable(props: Props) {
  const { projects, grantByProject, disabled, onRoleChange, onApplyToAll } = props;
  const { t } = useTranslation();
  // Member is the default bulk role: it is the useful everyday role and never
  // escalates to Admin implicitly.
  const [bulkRole, setBulkRole] = useState<TServicePrincipalRoleCap | null>(ROLE_MEMBER);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 rounded-md border-[0.5px] border-subtle bg-layer-2 px-3 py-2">
        <span className="text-12 text-secondary">{t(`${KEY_PREFIX}.access.apply_all_label`)}</span>
        <div className="w-32">
          <RoleSelect value={bulkRole} disabled={disabled} onChange={setBulkRole} />
        </div>
        <Button variant="secondary" size="sm" disabled={disabled} onClick={() => onApplyToAll(bulkRole)}>
          {t(`${KEY_PREFIX}.access.apply_all_button`)}
        </Button>
        <span className="text-11 text-placeholder">
          {t(`${KEY_PREFIX}.access.apply_all_hint`, { count: projects.length })}
        </span>
      </div>
      <div className="rounded-md border-[0.5px] border-subtle">
        <div className="grid grid-cols-[minmax(0,1fr)_10rem] gap-2 border-b-[0.5px] border-subtle bg-layer-2 px-3 py-2 text-11 text-tertiary">
          <div>{t(`${KEY_PREFIX}.scopes.project`)}</div>
          <div>{t(`${KEY_PREFIX}.access.role`)}</div>
        </div>
        {projects.length === 0 ? (
          <p className="px-3 py-3 text-13 text-placeholder">{t(`${KEY_PREFIX}.access.no_projects`)}</p>
        ) : (
          projects.map((project) => {
            const grant = grantByProject.get(project.id);
            return (
              <div
                key={project.id}
                className="grid grid-cols-[minmax(0,1fr)_10rem] items-center gap-2 border-b-[0.5px] border-subtle px-3 py-2 last:border-b-0"
              >
                <div className="flex min-w-0 items-center gap-2">
                  <span className="truncate text-13 text-secondary">{project.name}</span>
                  {grant && !grant.is_active && (
                    <span className="text-warning flex-shrink-0 rounded bg-layer-2 px-1.5 py-0.5 text-11">
                      {t(`${KEY_PREFIX}.access.role_disabled`)}
                    </span>
                  )}
                </div>
                <RoleSelect
                  value={grant ? grant.role_cap : null}
                  disabled={disabled}
                  onChange={(role) => onRoleChange(project.id, role)}
                />
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

function RoleSelect(props: {
  value: TServicePrincipalRoleCap | null;
  disabled?: boolean;
  onChange: (role: TServicePrincipalRoleCap | null) => void;
}) {
  const { value, disabled, onChange } = props;
  const { t } = useTranslation();

  const renderLabel = (role: TServicePrincipalRoleCap | null) =>
    role === null
      ? t(`${KEY_PREFIX}.access.role_none`)
      : getScopeValueLabel(`${KEY_PREFIX}.grants.role_caps.${role}`, t);

  return (
    <CustomSelect
      className="w-full"
      customButtonClassName="w-full"
      customButton={
        <div className="flex h-8 w-full items-center justify-between gap-2 rounded-md border-[0.5px] border-subtle px-2 text-13">
          <span className="truncate">{renderLabel(value)}</span>
          <ChevronDownIcon className="size-3 flex-shrink-0 text-tertiary" aria-hidden="true" />
        </div>
      }
      value={value === null ? "none" : String(value)}
      disabled={disabled}
      onChange={(val: string) => onChange(val === "none" ? null : (Number(val) as TServicePrincipalRoleCap))}
    >
      <CustomSelect.Option value="none">{renderLabel(null)}</CustomSelect.Option>
      {SERVICE_PRINCIPAL_GRANT_ROLE_CAPS.map((roleCap) => (
        <CustomSelect.Option key={roleCap} value={String(roleCap)}>
          {renderLabel(roleCap)}
        </CustomSelect.Option>
      ))}
    </CustomSelect>
  );
}
