/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import { useTranslation } from "@plane/i18n";
import { Badge } from "@plane/propel/badge";
import type { TBadgeVariant } from "@plane/propel/badge";
// local imports
import type { TPluginKind, TPluginState } from "@/services/plugins";
import { PLUGIN_STATE_LABEL_KEY } from "./helper";

const STATE_BADGE_VARIANT: Record<TPluginState, TBadgeVariant> = {
  enabled: "success",
  installed: "brand",
  disabled: "neutral",
};

export function PluginStateBadge({ state }: { state: TPluginState }) {
  const { t } = useTranslation();

  return (
    <Badge variant={STATE_BADGE_VARIANT[state]} size="base">
      {t(PLUGIN_STATE_LABEL_KEY[state])}
    </Badge>
  );
}

export function PluginKindBadge({ kind }: { kind: TPluginKind }) {
  const { t } = useTranslation();

  return (
    <Badge variant={kind === "builtin" ? "brand" : "warning"} size="base">
      {t(
        kind === "builtin"
          ? "workspace_settings.settings.plugins.list.kind_builtin"
          : "workspace_settings.settings.plugins.list.kind_package"
      )}
    </Badge>
  );
}
