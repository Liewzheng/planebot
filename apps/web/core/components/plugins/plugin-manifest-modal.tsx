/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import useSWR from "swr";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { EModalPosition, EModalWidth, ModalCore } from "@plane/ui";
// services
import { pluginService } from "@/services/plugins";
import type { TPlugin } from "@/services/plugins";
// local imports
import { PLUGIN_MANIFEST } from "./constants";
import { getPluginErrorSentence } from "./helper";

type Props = {
  plugin: TPlugin;
  isOpen: boolean;
  onClose: () => void;
  workspaceSlug: string;
};

const HashRow = ({ label, value }: { label: string; value: string }) => (
  <div className="flex flex-col gap-0.5">
    <span className="text-11 text-tertiary">{label}</span>
    <span className="font-mono text-11 break-all text-secondary">{value}</span>
  </div>
);

/**
 * The stored manifest, read-only. This is evidence of what was accepted at
 * install time together with the hashes that identify it, so nothing here is an
 * input — the dialog has exactly one button and it closes.
 */
export function PluginManifestModal({ plugin, isOpen, onClose, workspaceSlug }: Props) {
  // hooks
  const { t } = useTranslation();
  const { data, isLoading, error } = useSWR(isOpen ? PLUGIN_MANIFEST(workspaceSlug, plugin.key) : null, () =>
    pluginService.fetchPluginManifest(workspaceSlug, plugin.key)
  );

  return (
    <ModalCore isOpen={isOpen} handleClose={onClose} position={EModalPosition.TOP} width={EModalWidth.XXL}>
      <div className="space-y-4 p-5">
        <div>
          <h3 className="text-18 font-medium text-primary">
            {t("workspace_settings.settings.plugins.manifest_modal.title", { name: plugin.name })}
          </h3>
          <p className="mt-1 text-13 text-tertiary">
            {t("workspace_settings.settings.plugins.manifest_modal.description")}
          </p>
        </div>

        {error ? (
          <p className="rounded-md bg-danger-subtle px-3 py-2 text-11 text-danger-primary">
            {getPluginErrorSentence(error) ?? t("workspace_settings.settings.plugins.manifest_modal.load_failed")}
          </p>
        ) : isLoading || !data ? (
          <p className="text-11 text-tertiary">{t("workspace_settings.settings.plugins.manifest_modal.loading")}</p>
        ) : (
          <>
            {data.contract_drifted && (
              <p className="rounded-md bg-danger-subtle px-3 py-2 text-11 text-danger-primary">
                {t("workspace_settings.settings.plugins.manifest_modal.drifted")}
              </p>
            )}
            <pre className="font-mono max-h-80 overflow-auto rounded-md border border-subtle bg-surface-2 p-3 text-12 break-words whitespace-pre-wrap">
              {JSON.stringify(data.manifest, null, 2)}
            </pre>
            <div className="flex flex-col gap-2 rounded-md border border-subtle bg-surface-2 p-3">
              <HashRow
                label={t("workspace_settings.settings.plugins.manifest_modal.manifest_hash")}
                value={data.manifest_hash}
              />
              <HashRow
                label={t("workspace_settings.settings.plugins.manifest_modal.contract_hash")}
                value={data.contract_hash}
              />
              <HashRow
                label={t("workspace_settings.settings.plugins.manifest_modal.kernel_contract_hash")}
                value={data.kernel_contract_hash}
              />
            </div>
          </>
        )}
      </div>

      <div className="flex items-center justify-end gap-2 border-t-[0.5px] border-subtle px-5 py-4">
        <Button variant="secondary" onClick={onClose}>
          {t("workspace_settings.settings.plugins.manifest_modal.close")}
        </Button>
      </div>
    </ModalCore>
  );
}
