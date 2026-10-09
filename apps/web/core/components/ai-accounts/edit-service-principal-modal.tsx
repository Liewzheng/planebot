/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { mutate } from "swr";
// plane imports
import { Avatar } from "@makeplane/propel/components/avatar";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { EFileAssetType, type TServicePrincipal } from "@plane/types";
import { EModalPosition, EModalWidth, ModalCore } from "@plane/ui";
import { getFileURL } from "@plane/utils";
// components
import { UserImageUploadModal } from "@/components/core/modals/user-image-upload-modal";
// hooks
import { servicePrincipalService } from "@/services/ai-account.service";
import { FileService } from "@/services/file.service";
// local imports
import { ServicePrincipalForm, type TServicePrincipalFormValues } from "./service-principal-form";
import { SERVICE_PRINCIPALS_LIST } from "./constants";

const fileService = new FileService();

type Props = {
  principal: TServicePrincipal;
  isOpen: boolean;
  onClose: () => void;
  workspaceSlug: string;
};

export function EditServicePrincipalModal(props: Props) {
  const { principal, isOpen, onClose, workspaceSlug } = props;
  // states
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isAvatarUploadModalOpen, setIsAvatarUploadModalOpen] = useState(false);
  const [isAvatarUpdating, setIsAvatarUpdating] = useState(false);
  // hooks
  const { t } = useTranslation();

  const handleClose = () => {
    onClose();
    setTimeout(() => setIsSubmitting(false), 350);
  };

  const handleAvatarChange = async (avatar: string) => {
    setIsAvatarUpdating(true);
    try {
      await servicePrincipalService.updateServicePrincipal(workspaceSlug, principal.id, { avatar });
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("workspace_settings.settings.service_principals.toasts.updated.title"),
        message: t("workspace_settings.settings.service_principals.toasts.updated.message"),
      });
      mutate<TServicePrincipal[]>(SERVICE_PRINCIPALS_LIST(workspaceSlug));
      setIsAvatarUploadModalOpen(false);
    } catch (err) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("workspace_settings.settings.service_principals.toasts.not_updated.title"),
        message:
          (err as { message?: string })?.message ??
          t("workspace_settings.settings.service_principals.toasts.not_updated.message"),
      });
      // propagate so the upload modal can roll back the freshly uploaded asset
      throw err;
    } finally {
      setIsAvatarUpdating(false);
    }
  };

  const handleUpdate = async (data: TServicePrincipalFormValues) => {
    setIsSubmitting(true);
    try {
      await servicePrincipalService.updateServicePrincipal(workspaceSlug, principal.id, {
        name: data.name,
        description: data.description,
      });
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("workspace_settings.settings.service_principals.toasts.updated.title"),
        message: t("workspace_settings.settings.service_principals.toasts.updated.message"),
      });
      mutate<TServicePrincipal[]>(SERVICE_PRINCIPALS_LIST(workspaceSlug));
      handleClose();
    } catch (err) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("workspace_settings.settings.service_principals.toasts.not_updated.title"),
        message:
          (err as { message?: string })?.message ??
          t("workspace_settings.settings.service_principals.toasts.not_updated.message"),
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={handleClose} position={EModalPosition.TOP} width={EModalWidth.XXL}>
      <UserImageUploadModal
        handleRemove={() => handleAvatarChange("")}
        isOpen={isAvatarUploadModalOpen}
        onClose={() => setIsAvatarUploadModalOpen(false)}
        onSuccess={handleAvatarChange}
        value={principal.avatar || null}
        uploadAsset={async (image) => {
          const { asset_url } = await fileService.uploadWorkspaceAsset(
            workspaceSlug,
            {
              entity_type: EFileAssetType.USER_AVATAR,
              // SPs are not Users; tag the asset against the SP id so the
              // current user's avatar is never clobbered
              entity_identifier: principal.id,
            },
            image
          );
          return asset_url;
        }}
        removeAsset={async (_value) => {
          // The avatar string is just a URL/path on the SP; no separate
          // asset cleanup pipeline to invoke here.
        }}
      />
      <div className="flex items-center gap-4 border-b-[0.5px] border-subtle px-5 py-4">
        <Avatar
          src={getFileURL(principal.avatar)}
          alt={principal.name}
          fallback={principal.name.charAt(0)}
          size="lg"
          tooltip
        />
        <div className="flex flex-col gap-2">
          <span className="text-13 font-medium text-secondary">
            {t("workspace_settings.settings.service_principals.avatar.label")}
          </span>
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              loading={isAvatarUpdating}
              onClick={() => setIsAvatarUploadModalOpen(true)}
            >
              {t("workspace_settings.settings.service_principals.avatar.upload")}
            </Button>
            {principal.avatar && (
              <Button
                variant="error-outline"
                size="sm"
                loading={isAvatarUpdating}
                onClick={() => handleAvatarChange("")}
              >
                {t("workspace_settings.settings.service_principals.avatar.remove")}
              </Button>
            )}
          </div>
        </div>
      </div>
      <ServicePrincipalForm
        defaultValues={{ name: principal.name, description: principal.description }}
        handleClose={handleClose}
        isSubmitting={isSubmitting}
        loadingLabel={t("workspace_settings.settings.service_principals.modal.updating")}
        submitLabel={t("workspace_settings.settings.service_principals.modal.update")}
        title={t("workspace_settings.settings.service_principals.modal.edit_title")}
        onSubmit={handleUpdate}
      />
    </ModalCore>
  );
}
