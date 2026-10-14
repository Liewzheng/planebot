/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { EModalPosition, EModalWidth, ModalCore } from "@plane/ui";
// local imports
import { RoleCapabilityTable } from "./role-capability-table";

type Props = {
  isOpen: boolean;
  onClose: () => void;
};

/** Read-only "what can each role do" reference, opened from the principal row. */
export function RoleCapabilityModal(props: Props) {
  const { isOpen, onClose } = props;
  const { t } = useTranslation();

  return (
    <ModalCore isOpen={isOpen} handleClose={onClose} position={EModalPosition.TOP} width={EModalWidth.XXL}>
      <div className="flex max-h-[70vh] flex-col">
        <div className="space-y-3 overflow-y-auto p-5">
          <h3 className="text-18 font-medium text-secondary">
            {t("workspace_settings.settings.service_principals.capabilities.title")}
          </h3>
          <RoleCapabilityTable />
        </div>
        <div className="flex items-center justify-end gap-2 border-t-[0.5px] border-subtle px-5 py-4">
          <Button variant="secondary" onClick={onClose}>
            {t("close")}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
}
