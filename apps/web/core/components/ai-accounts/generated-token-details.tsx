/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { CopyIcon } from "@plane/propel/icons";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { Tooltip } from "@makeplane/propel/components/tooltip";
import type { TServicePrincipal } from "@plane/types";
// helpers
import { copyTextToClipboard } from "@plane/utils";
// hooks
import { usePlatformOS } from "@/hooks/use-platform-os";

type TCreatedServicePrincipal = TServicePrincipal & { token: string };

type Props = {
  principal: TCreatedServicePrincipal;
  handleClose: () => void;
  title?: string;
};

export function GeneratedTokenDetails(props: Props) {
  const { principal, handleClose, title } = props;
  const { isMobile } = usePlatformOS();
  const { t } = useTranslation();

  const copyPrincipalToken = (token: string) => {
    copyTextToClipboard(token)
      .then(() =>
        setToast({
          type: TOAST_TYPE.SUCCESS,
          title: `${t("success")}!`,
          message: t("workspace_settings.settings.service_principals.token.copied"),
        })
      )
      .catch((error: unknown) => {
        // never log the token itself
        console.error(
          "Failed to copy service principal token to clipboard",
          error instanceof Error ? error.message : error
        );
        setToast({
          type: TOAST_TYPE.ERROR,
          title: `${t("error")}!`,
          message: t("workspace_settings.settings.service_principals.token.not_copied"),
        });
      });
  };

  return (
    <div className="w-full p-5">
      <div className="w-full space-y-3 text-wrap">
        <h3 className="text-16 leading-6 font-medium text-primary">
          {title ?? t("workspace_settings.settings.service_principals.token.title")}
        </h3>
        <p className="text-13 text-placeholder">
          {t("workspace_settings.settings.service_principals.token.copy_warning")}
        </p>
      </div>
      <button
        type="button"
        onClick={() => copyPrincipalToken(principal.token)}
        className="mt-4 flex w-full items-center justify-between truncate rounded-md border-[0.5px] border-subtle px-3 py-2 text-13 font-medium outline-none"
      >
        <span className="truncate pr-2">{principal.token}</span>
        <Tooltip label={t("workspace_settings.settings.service_principals.token.copied")} disabled={isMobile}>
          <CopyIcon className="h-4 w-4 flex-shrink-0 text-placeholder" />
        </Tooltip>
      </button>
      <div className="mt-6 flex items-center justify-end">
        <Button variant="secondary" onClick={handleClose}>
          {t("close")}
        </Button>
      </div>
    </div>
  );
}
