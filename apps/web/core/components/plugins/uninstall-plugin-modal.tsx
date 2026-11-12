/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { mutate } from "swr";
// plane imports
import { Input, InputGroup } from "@makeplane/propel/components/input";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { EModalPosition, EModalWidth, ModalCore, TextArea } from "@plane/ui";
// services
import { pluginService } from "@/services/plugins";
import type { TPlugin } from "@/services/plugins";
// local imports
import { PLUGINS_LIST, PLUGIN_AUDIT, PLUGIN_AUDIT_LIST } from "./constants";
import { getPluginErrorSentence } from "./helper";

type Props = {
  plugin: TPlugin;
  isOpen: boolean;
  onClose: () => void;
  workspaceSlug: string;
};

/**
 * Uninstall confirmation. The server requires two separate things — `confirm`
 * equal to the plugin key and a non-empty reason — so the form asks for both and
 * only gates on those two; anything else (state, drift, key reservation) is the
 * server's call and its sentence is shown as-is.
 *
 * The dialog also says what survives: the audit trail stays readable and the key
 * stays reserved, so nobody expects a clean slate afterwards.
 */
export function UninstallPluginModal({ plugin, isOpen, onClose, workspaceSlug }: Props) {
  // states
  const [confirmText, setConfirmText] = useState("");
  const [reason, setReason] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  // hooks
  const { t } = useTranslation();
  // derived values
  const isConfirmTyped = confirmText === plugin.key;
  const hasReason = reason.trim().length > 0;
  const canSubmit = isConfirmTyped && hasReason && !isSubmitting;

  const handleClose = () => {
    onClose();
    setConfirmText("");
    setReason("");
    setServerError(null);
    setIsSubmitting(false);
  };

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setIsSubmitting(true);
    setServerError(null);
    try {
      await pluginService.uninstallPlugin(workspaceSlug, plugin.key, {
        confirm: confirmText,
        reason: reason.trim(),
      });
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("workspace_settings.settings.plugins.toasts.uninstalled.title"),
        message: t("workspace_settings.settings.plugins.toasts.uninstalled.message"),
      });
      void mutate(PLUGINS_LIST(workspaceSlug));
      void mutate(PLUGIN_AUDIT_LIST(workspaceSlug));
      void mutate(PLUGIN_AUDIT(workspaceSlug, plugin.key));
      handleClose();
    } catch (error) {
      setServerError(
        getPluginErrorSentence(error) ?? t("workspace_settings.settings.plugins.toasts.not_uninstalled.message")
      );
      setIsSubmitting(false);
    }
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={handleClose} position={EModalPosition.TOP} width={EModalWidth.XL}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void handleSubmit();
        }}
      >
        <div className="space-y-4 p-5">
          <div>
            <h3 className="text-18 font-medium text-primary">
              {t("workspace_settings.settings.plugins.uninstall_modal.title", { name: plugin.name })}
            </h3>
            <p className="mt-1 text-13 text-tertiary">
              {t("workspace_settings.settings.plugins.uninstall_modal.description")}
            </p>
            {plugin.state === "enabled" && (
              <p className="mt-2 text-11 text-secondary">
                {t("workspace_settings.settings.plugins.uninstall_modal.enabled_hint")}
              </p>
            )}
          </div>

          <div className="space-y-1">
            <label className="text-11 text-tertiary" htmlFor="plugin-uninstall-confirm">
              {t("workspace_settings.settings.plugins.uninstall_modal.confirm_label", { key: plugin.key })}
            </label>
            <InputGroup size="2xl">
              <Input
                id="plugin-uninstall-confirm"
                size="2xl"
                type="text"
                value={confirmText}
                onChange={(event) => setConfirmText(event.target.value)}
                placeholder={t("workspace_settings.settings.plugins.uninstall_modal.confirm_placeholder", {
                  key: plugin.key,
                })}
                autoComplete="off"
              />
            </InputGroup>
          </div>

          <div className="space-y-1">
            <label className="text-11 text-tertiary" htmlFor="plugin-uninstall-reason">
              {t("workspace_settings.settings.plugins.uninstall_modal.reason_label")}
            </label>
            <TextArea
              id="plugin-uninstall-reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder={t("workspace_settings.settings.plugins.uninstall_modal.reason_placeholder")}
              className="min-h-24 w-full resize-none text-14"
            />
            <p className="text-11 text-placeholder">
              {t("workspace_settings.settings.plugins.uninstall_modal.reason_hint")}
            </p>
          </div>

          {serverError && (
            <p className="rounded-md bg-danger-subtle px-3 py-2 text-11 text-danger-primary">{serverError}</p>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t-[0.5px] border-subtle px-5 py-4">
          <Button variant="secondary" type="button" onClick={handleClose}>
            {t("cancel")}
          </Button>
          <Button variant="error-fill" type="submit" disabled={!canSubmit} loading={isSubmitting}>
            {t("workspace_settings.settings.plugins.uninstall_modal.submit")}
          </Button>
        </div>
      </form>
    </ModalCore>
  );
}
