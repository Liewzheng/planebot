/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useTranslation } from "@plane/i18n";
import { Link, useLocation } from "react-router";
import { cn } from "@plane/utils";

type Props = {
  workspaceSlug: string;
  projectId: string;
};

/**
 * The two pages under the *Test* entry of the project navigation: the
 * cases and the batches.
 */
export function TestCaseTabs(props: Props) {
  const { workspaceSlug, projectId } = props;
  const { t } = useTranslation();
  const location = useLocation();

  const base = `/${workspaceSlug}/projects/${projectId}`;
  const tabs = [
    { key: "cases", label: t("project_test_case.tabs.cases"), href: `${base}/test-cases` },
    { key: "batches", label: t("project_test_case.tabs.batches"), href: `${base}/test-runs` },
  ];

  return (
    <div className="flex items-center gap-1">
      {tabs.map((tab) => {
        const isActive = location.pathname === tab.href || location.pathname.startsWith(`${tab.href}/`);

        return (
          <Link
            key={tab.key}
            to={tab.href}
            className={cn(
              "rounded-md px-2.5 py-1 text-12 transition-colors",
              isActive ? "bg-layer-2 text-primary" : "text-tertiary hover:bg-layer-2 hover:text-secondary"
            )}
          >
            {tab.label}
          </Link>
        );
      })}
    </div>
  );
}
