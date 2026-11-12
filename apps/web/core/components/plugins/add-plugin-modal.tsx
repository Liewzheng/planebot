/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo, useState } from "react";
import { mutate } from "swr";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { EModalPosition, EModalWidth, ModalCore, TextArea } from "@plane/ui";
// services
import { pluginService } from "@/services/plugins";
import type { TPluginManifest } from "@/services/plugins";
// local imports
import { EXAMPLE_PLUGIN_MANIFEST, PLUGINS_LIST, PLUGIN_AUDIT_LIST } from "./constants";
import { PluginKindBadge } from "./plugin-badges";
import { getPluginErrorSentence } from "./helper";

type TPluginSource = "install" | "import";

type Props = {
  isOpen: boolean;
  onClose: () => void;
  workspaceSlug: string;
};

const parseManifestText = (text: string): TPluginManifest | null => {
  if (!text.trim()) return null;
  try {
    const value: unknown = JSON.parse(text);
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    return value as TPluginManifest;
  } catch {
    return null;
  }
};

/**
 * Install or import one plugin from a manifest.
 *
 * The source is a real choice, not cosmetics: `install` and `import` are two
 * endpoints that write two different audit actions, so the trail can tell "this
 * workspace added a plugin" from "someone brought a manifest in". Everything the
 * kernel refuses — kind, contract range, scopes, key shape — is left to the
 * server and surfaced verbatim; the only thing checked here is what the form
 * cannot submit without (a JSON object) and the one case the UI is asked to
 * explain up front: a `package` manifest has no runtime to run on.
 */
export function AddPluginModal({ isOpen, onClose, workspaceSlug }: Props) {
  // states
  const [source, setSource] = useState<TPluginSource>("install");
  const [manifestText, setManifestText] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  // hooks
  const { t } = useTranslation();
  // derived values
  const manifest = useMemo(() => parseManifestText(manifestText), [manifestText]);
  const hasText = manifestText.trim().length > 0;
  const isPackage = manifest?.kind === "package";
  const canSubmit = Boolean(manifest) && !isPackage && !isSubmitting;
  const submitLabel =
    source === "import"
      ? t("workspace_settings.settings.plugins.add_modal.submit_import")
      : t("workspace_settings.settings.plugins.add_modal.submit_install");

  const handleClose = () => {
    onClose();
    setSource("install");
    setManifestText("");
    setServerError(null);
    setIsSubmitting(false);
  };

  const handleSubmit = async () => {
    if (!manifest || isPackage || isSubmitting) return;
    setIsSubmitting(true);
    setServerError(null);
    try {
      if (source === "import") await pluginService.importPlugin(workspaceSlug, manifest);
      else await pluginService.installPlugin(workspaceSlug, manifest);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(
          source === "import"
            ? "workspace_settings.settings.plugins.toasts.imported.title"
            : "workspace_settings.settings.plugins.toasts.installed.title"
        ),
        message: t(
          source === "import"
            ? "workspace_settings.settings.plugins.toasts.imported.message"
            : "workspace_settings.settings.plugins.toasts.installed.message"
        ),
      });
      void mutate(PLUGINS_LIST(workspaceSlug));
      void mutate(PLUGIN_AUDIT_LIST(workspaceSlug));
      handleClose();
    } catch (error) {
      setServerError(
        getPluginErrorSentence(error) ??
          t(
            source === "import"
              ? "workspace_settings.settings.plugins.toasts.not_imported.message"
              : "workspace_settings.settings.plugins.toasts.not_installed.message"
          )
      );
      setIsSubmitting(false);
    }
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={handleClose} position={EModalPosition.TOP} width={EModalWidth.XXL}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void handleSubmit();
        }}
      >
        <div className="space-y-4 p-5">
          <div>
            <h3 className="text-18 font-medium text-primary">
              {t("workspace_settings.settings.plugins.add_modal.title")}
            </h3>
            <p className="mt-1 text-13 text-tertiary">
              {t("workspace_settings.settings.plugins.add_modal.description")}
            </p>
          </div>

          <fieldset className="space-y-2">
            <legend className="pb-1 text-11 text-tertiary">
              {t("workspace_settings.settings.plugins.add_modal.source_label")}
            </legend>
            <div className="rounded-md border border-subtle bg-surface-2 px-2 py-2">
              <div className="space-y-2">
                {/* oxlint-disable-next-line jsx_a11y/label-has-associated-control */}
                <label className="flex cursor-pointer gap-2 rounded-md px-2 py-1.5 hover:bg-surface-1">
                  <input
                    type="radio"
                    name="plugin-source"
                    value="install"
                    checked={source === "install"}
                    onChange={() => setSource("install")}
                    className="mt-1 size-4 text-accent-primary focus:ring-accent-strong"
                  />
                  <div className="flex-1">
                    <div className="text-13 text-primary">
                      {t("workspace_settings.settings.plugins.add_modal.source_install")}
                    </div>
                    <div className="text-11 text-secondary">
                      {t("workspace_settings.settings.plugins.add_modal.source_install_hint")}
                    </div>
                  </div>
                </label>
                {/* oxlint-disable-next-line jsx_a11y/label-has-associated-control */}
                <label className="flex cursor-pointer gap-2 rounded-md px-2 py-1.5 hover:bg-surface-1">
                  <input
                    type="radio"
                    name="plugin-source"
                    value="import"
                    checked={source === "import"}
                    onChange={() => setSource("import")}
                    className="mt-1 size-4 text-accent-primary focus:ring-accent-strong"
                  />
                  <div className="flex-1">
                    <div className="text-13 text-primary">
                      {t("workspace_settings.settings.plugins.add_modal.source_import")}
                    </div>
                    <div className="text-11 text-secondary">
                      {t("workspace_settings.settings.plugins.add_modal.source_import_hint")}
                    </div>
                  </div>
                </label>
              </div>
            </div>
          </fieldset>

          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <label className="text-11 text-tertiary" htmlFor="plugin-manifest-input">
                {t("workspace_settings.settings.plugins.add_modal.manifest_label")}
              </label>
              <Button
                variant="secondary"
                size="sm"
                type="button"
                onClick={() => setManifestText(JSON.stringify(EXAMPLE_PLUGIN_MANIFEST, null, 2))}
              >
                {t("workspace_settings.settings.plugins.add_modal.use_example")}
              </Button>
            </div>
            <TextArea
              id="plugin-manifest-input"
              value={manifestText}
              onChange={(event) => setManifestText(event.target.value)}
              placeholder={t("workspace_settings.settings.plugins.add_modal.manifest_placeholder")}
              className="font-mono min-h-56 w-full resize-y text-12"
              aria-label={t("workspace_settings.settings.plugins.add_modal.manifest_label")}
            />
          </div>

          {manifest && (
            <div className="flex flex-wrap items-center gap-2 rounded-md border border-subtle bg-surface-2 px-3 py-2">
              <span className="text-11 text-tertiary">
                {t("workspace_settings.settings.plugins.add_modal.preview_title")}
              </span>
              <span className="text-13 text-primary">{manifest.name ?? manifest.key ?? "—"}</span>
              {manifest.key && <span className="font-mono text-11 text-secondary">{manifest.key}</span>}
              {manifest.version && <span className="text-11 text-tertiary">v{manifest.version}</span>}
              {manifest.kind && <PluginKindBadge kind={manifest.kind} />}
            </div>
          )}

          {hasText && !manifest && (
            <p className="text-11 text-danger-primary">
              {t("workspace_settings.settings.plugins.add_modal.invalid_json")}
            </p>
          )}

          {isPackage && (
            <p className="rounded-md bg-danger-subtle px-3 py-2 text-11 text-danger-primary">
              {t("workspace_settings.settings.plugins.add_modal.package_unavailable")}
            </p>
          )}

          {serverError && (
            <p className="rounded-md bg-danger-subtle px-3 py-2 text-11 text-danger-primary">{serverError}</p>
          )}
        </div>

        <div className="flex flex-col gap-2 border-t-[0.5px] border-subtle px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-11 text-tertiary">
            {t(
              source === "import"
                ? "workspace_settings.settings.plugins.add_modal.audit_note_import"
                : "workspace_settings.settings.plugins.add_modal.audit_note_install"
            )}
          </p>
          <div className="flex items-center justify-end gap-2">
            <Button variant="secondary" type="button" onClick={handleClose}>
              {t("cancel")}
            </Button>
            <Button variant="primary" type="submit" disabled={!canSubmit} loading={isSubmitting}>
              {submitLabel}
            </Button>
          </div>
        </div>
      </form>
    </ModalCore>
  );
}
