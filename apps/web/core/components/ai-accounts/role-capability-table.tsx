/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import { useTranslation } from "@plane/i18n";
import type { TServiceScopeAction, TServiceScopeResourceType } from "@plane/types";
// local imports
import { getScopeValueLabel } from "./constants";
import {
  CAPABILITY_ACTIONS,
  PROJECT_SCOPED_RESOURCES,
  WORKSPACE_LEVEL_RESOURCES,
  isOwnershipGated,
  isWorkspaceLevelResource,
  requiredRole,
} from "./role-capabilities";

const KEY_PREFIX = "workspace_settings.settings.service_principals";

const RULE_KEYS = [
  "scope_grant",
  "owner_floor",
  "guest",
  "member",
  "member_ownership",
  "admin",
  "unknown_owner",
  "coming_soon",
  "workspace_level",
  "read_covers_list",
  "default_deny",
  "invite",
] as const;

type Props = {
  className?: string;
};

/**
 * Read-only role × resource × action table, generated from the same matrix the
 * backend engine evaluates (see role-capabilities.ts). A cell shows the minimum
 * role a service principal needs for that action on that resource — i.e. the
 * grant role cap it must carry — or "—" when the pair is unregistered and
 * therefore default-denied.
 */
export function RoleCapabilityTable(props: Props) {
  const { className } = props;
  const { t } = useTranslation();

  const renderRow = (resource: TServiceScopeResourceType) => (
    <tr key={resource} className="border-t-[0.5px] border-subtle">
      <td className="px-3 py-2 text-secondary">
        {getScopeValueLabel(`${KEY_PREFIX}.scopes.resources.${resource}`, t)}
      </td>
      {CAPABILITY_ACTIONS.map((action) => (
        <td key={action} className="px-3 py-2">
          <CapabilityCell action={action} resource={resource} t={t} />
        </td>
      ))}
    </tr>
  );

  return (
    <div className={className}>
      <p className="text-12 text-placeholder">{t(`${KEY_PREFIX}.capabilities.description`)}</p>
      <div className="mt-3 overflow-x-auto rounded-md border-[0.5px] border-subtle">
        <table className="w-full min-w-[34rem] border-collapse text-13">
          <thead>
            <tr className="bg-layer-2 text-left text-11 text-tertiary">
              <th className="font-normal px-3 py-2">{t(`${KEY_PREFIX}.scopes.resource_type`)}</th>
              {CAPABILITY_ACTIONS.map((action) => (
                <th key={action} className="font-normal px-3 py-2">
                  {getScopeValueLabel(`${KEY_PREFIX}.scopes.actions.${action}`, t)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <GroupRow label={t(`${KEY_PREFIX}.capabilities.group_project_resources`)} />
            {PROJECT_SCOPED_RESOURCES.map(renderRow)}
            <GroupRow
              label={t(`${KEY_PREFIX}.capabilities.group_workspace_resources`)}
              note={t(`${KEY_PREFIX}.capabilities.group_workspace_note`)}
            />
            {WORKSPACE_LEVEL_RESOURCES.map(renderRow)}
          </tbody>
        </table>
      </div>
      <div className="mt-2 flex flex-col gap-1 text-11 text-tertiary">
        <p>{t(`${KEY_PREFIX}.capabilities.legend_required_role`)}</p>
        <p>{t(`${KEY_PREFIX}.capabilities.legend_not_registered`)}</p>
        <p>{t(`${KEY_PREFIX}.capabilities.legend_ownership`)}</p>
      </div>
      <div className="mt-4 space-y-1.5">
        <h4 className="text-12 font-medium text-secondary">{t(`${KEY_PREFIX}.capabilities.rules_title`)}</h4>
        <ul className="list-disc space-y-1 pl-4 text-11 text-placeholder">
          {RULE_KEYS.map((ruleKey) => (
            <li key={ruleKey}>{t(`${KEY_PREFIX}.capabilities.rules.${ruleKey}`)}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function GroupRow(props: { label: string; note?: string }) {
  const { label, note } = props;
  return (
    <tr className="border-t-[0.5px] border-subtle bg-layer-2/60">
      <td colSpan={1 + CAPABILITY_ACTIONS.length} className="px-3 py-1.5">
        <span className="text-11 font-medium text-secondary">{label}</span>
        {note && <span className="ml-2 text-11 text-placeholder">{note}</span>}
      </td>
    </tr>
  );
}

function CapabilityCell(props: {
  action: TServiceScopeAction;
  resource: TServiceScopeResourceType;
  t: (key: string) => string;
}) {
  const { action, resource, t } = props;

  // Workspace-level writes are refused by the engine's Q4 guard no matter which
  // role carries them, so quoting the matrix floor here would be a lie. The
  // group note above the block states the rule once.
  if (isWorkspaceLevelResource(resource) && action !== "read") {
    return <span className="text-placeholder">—</span>;
  }

  const required = requiredRole(action, resource);
  if (required === null) return <span className="text-placeholder">—</span>;

  return (
    <span className="text-secondary">
      {getScopeValueLabel(`${KEY_PREFIX}.grants.role_caps.${required}`, t)}
      {isOwnershipGated(action, resource) && <span className="text-accent-primary"> *</span>}
    </span>
  );
}
