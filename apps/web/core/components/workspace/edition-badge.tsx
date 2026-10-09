/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
// ui
import { useTranslation } from "@plane/i18n";
import { Tooltip } from "@makeplane/propel/components/tooltip";
// hooks
import { useInstance } from "@/hooks/store/use-instance";
import { usePlatformOS } from "@/hooks/use-platform-os";
import packageJson from "package.json";
// local components
import { Button } from "@plane/propel/button";
import { PaidPlanUpgradeModal } from "@/components/license/modal/upgrade-modal";

export const WorkspaceEditionBadge = observer(function WorkspaceEditionBadge() {
  // states
  const [isPaidPlanPurchaseModalOpen, setIsPaidPlanPurchaseModalOpen] = useState(false);
  // store hooks
  const { config: instanceConfig } = useInstance();
  // translation
  const { t } = useTranslation();
  // platform
  const { isMobile } = usePlatformOS();
  // This fork has no paid tier of its own: on a self-managed instance the badge is
  // a version label only and must never lead to a purchase flow.
  const canUpgradePlan = !instanceConfig?.is_self_managed;

  return (
    <>
      {canUpgradePlan && (
        <PaidPlanUpgradeModal
          isOpen={isPaidPlanPurchaseModalOpen}
          handleClose={() => setIsPaidPlanPurchaseModalOpen(false)}
        />
      )}
      <Tooltip label={`Version: v${packageJson.version}`} disabled={isMobile}>
        <Button
          variant="tertiary"
          size="lg"
          onClick={canUpgradePlan ? () => setIsPaidPlanPurchaseModalOpen(true) : undefined}
          aria-haspopup={canUpgradePlan ? "dialog" : undefined}
          aria-label={t("aria_labels.projects_sidebar.edition_badge")}
        >
          Community
        </Button>
      </Tooltip>
    </>
  );
});
