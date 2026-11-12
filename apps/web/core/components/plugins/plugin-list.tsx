/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// local imports
import type { TPlugin } from "@/services/plugins";
import { PluginListItem } from "./plugin-list-item";

type Props = {
  plugins: TPlugin[];
  workspaceSlug: string;
};

export function PluginsList({ plugins, workspaceSlug }: Props) {
  return (
    <div className="flex flex-col gap-2">
      {plugins.map((plugin) => (
        <PluginListItem key={plugin.id} plugin={plugin} workspaceSlug={workspaceSlug} />
      ))}
    </div>
  );
}
