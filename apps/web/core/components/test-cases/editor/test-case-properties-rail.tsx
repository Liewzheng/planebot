/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useTranslation } from "@plane/i18n";
import { CustomSelect } from "@plane/ui";
import {
  HashOutline,
  LinkOutline,
  ModuleOutline,
  PriorityOutline,
  StateOutline,
  UpdatedAtOutline,
  UserOutline,
} from "@makeplane/propel/icons";
import { cn, renderFormattedDate, renderFormattedTime } from "@plane/utils";
import type { ITestCase, ITestCaseIssueLink, ITestCaseModule } from "@plane/types";
// components
import { SidebarPropertyListItem } from "@/components/common/layout/sidebar/property-list-item";
import { TestCaseLinksForm } from "./test-case-links-form";
// constants
import { TEST_CASE_PRIORITIES, TEST_CASE_STATUSES } from "../constants";
// types
import type { TTestCaseDraft } from "../types";

type Props = {
  workspaceSlug: string;
  projectId: string;
  /** The case that is edited — absent while a case is created. */
  testCase?: ITestCase;
  draft: TTestCaseDraft;
  modules: ITestCaseModule[];
  links: ITestCaseIssueLink[];
  /** The reasons of a refused write, addressed by the field they belong to. */
  errors: Record<string, string[]>;
  /** The reader may write the case. */
  disabled: boolean;
  onChange: (patch: Partial<TTestCaseDraft>) => void;
  onCreateModule: () => void;
  onLinksChange: (links: ITestCaseIssueLink[]) => void;
  onCollapse: () => void;
};

/**
 * The properties of the case, in the rail on the right of the reading column.
 *
 * The rail is the counterpart of the work-item sidebar: one compact
 * "label + value" row per property, the values written through the handlers
 * of the editor (`onChange` for the fields of the record, the link form for
 * the work items). Nothing is written anywhere else, and no rule of the
 * server is repeated here — a refused write shows its reasons under the row
 * of the field it belongs to.
 */
export function TestCasePropertiesRail(props: Props) {
  const {
    workspaceSlug,
    projectId,
    testCase,
    draft,
    modules,
    links,
    errors,
    disabled,
    onChange,
    onCreateModule,
    onLinksChange,
    onCollapse,
  } = props;
  const { t } = useTranslation();

  const isCreateMode = !testCase;
  const number = testCase?.number ?? "";
  const selectedModule = modules.find((module) => module.id === draft.module);
  // The number carries the code of the module, so it can be shown before the
  // case exists — the last three digits are the ones the server assigns.
  const numberPreview = selectedModule ? `TC-${selectedModule.code}-NNN` : "";

  /** The reasons the server sent back for one field, under its row. */
  const reasonsOf = (field: string) => {
    const reasons = errors[field];
    if (!reasons || reasons.length === 0) return null;

    return (
      <ul className="flex w-full flex-col gap-0.5">
        {reasons.map((reason) => (
          <li key={reason} className="text-11 text-danger-primary">
            {reason}
          </li>
        ))}
      </ul>
    );
  };

  return (
    <aside
      id="test-case-properties"
      aria-label={t("common.properties")}
      className="fixed right-0 z-[5] flex h-full w-full min-w-[300px] flex-col border-l border-subtle bg-surface-1 sm:w-1/2 md:relative md:w-1/4 lg:min-w-80 xl:min-w-96"
    >
      <div className="flex h-full w-full flex-col items-center divide-y-2 divide-subtle-1 overflow-hidden">
        <div className="flex w-full shrink-0 items-center justify-between gap-2 px-6 py-3">
          <h2 className="text-body-xs-medium text-primary">{t("common.properties")}</h2>
          <button
            type="button"
            onClick={onCollapse}
            aria-controls="test-case-properties"
            aria-expanded
            className="rounded-sm px-1 py-0.5 text-11 text-tertiary hover:bg-layer-transparent-hover hover:text-secondary"
          >
            {t("project_test_case.editor.sidebar_hide")}
          </button>
        </div>

        <div className="h-full w-full overflow-y-auto px-6">
          <div className="mt-4 mb-6 space-y-2.5">
            <SidebarPropertyListItem icon={HashOutline} label={t("project_test_case.editor.number")}>
              {isCreateMode ? (
                numberPreview ? (
                  <span className="font-mono text-body-xs-regular text-tertiary">{numberPreview}</span>
                ) : (
                  <span className="text-11 text-tertiary">{t("project_test_case.editor.number_pending")}</span>
                )
              ) : (
                <span className="font-mono text-body-xs-regular text-secondary">{number}</span>
              )}
            </SidebarPropertyListItem>
          </div>

          <div className={cn("mt-2 mb-6 space-y-2.5", disabled && "opacity-60")}>
            <SidebarPropertyListItem icon={ModuleOutline} label={t("project_test_case.editor.field_module")}>
              <div className="flex w-full min-w-0 flex-col gap-1">
                {modules.length === 0 ? (
                  <p className="text-11 text-tertiary">{t("project_test_case.module.empty")}</p>
                ) : (
                  <CustomSelect
                    value={draft.module}
                    onChange={(value: string) => onChange({ module: value })}
                    disabled={disabled}
                    label={
                      <span className={cn("truncate", draft.module ? "text-primary" : "text-placeholder")}>
                        {selectedModule?.code ?? t("project_test_case.module.picker_placeholder")}
                      </span>
                    }
                    className="w-full min-w-0"
                  >
                    {modules.map((module) => (
                      <CustomSelect.Option key={module.id} value={module.id}>
                        {module.code}
                      </CustomSelect.Option>
                    ))}
                  </CustomSelect>
                )}
                <button
                  type="button"
                  onClick={onCreateModule}
                  disabled={disabled}
                  className="self-start text-11 text-accent-primary hover:underline disabled:cursor-not-allowed disabled:text-tertiary"
                >
                  + {t("project_test_case.module.create_button")}
                </button>
                {reasonsOf("module")}
              </div>
            </SidebarPropertyListItem>

            <SidebarPropertyListItem icon={PriorityOutline} label={t("project_test_case.editor.field_priority")}>
              <div className="flex w-full min-w-0 flex-col gap-1">
                <CustomSelect
                  value={draft.priority}
                  onChange={(value: string) => onChange({ priority: value })}
                  disabled={disabled}
                  label={
                    <span className={cn("truncate", draft.priority ? "text-primary" : "text-placeholder")}>
                      {draft.priority || t("project_test_case.editor.priority_placeholder")}
                    </span>
                  }
                  className="w-full min-w-0"
                >
                  {TEST_CASE_PRIORITIES.map((priority) => (
                    <CustomSelect.Option key={priority} value={priority}>
                      {priority}
                    </CustomSelect.Option>
                  ))}
                </CustomSelect>
                {reasonsOf("priority")}
              </div>
            </SidebarPropertyListItem>

            <SidebarPropertyListItem icon={StateOutline} label={t("project_test_case.editor.field_status")}>
              <div className="flex w-full min-w-0 flex-col gap-1">
                <CustomSelect
                  value={draft.status}
                  onChange={(value: string) => onChange({ status: value })}
                  disabled={disabled}
                  label={
                    <span className="truncate text-primary">
                      {draft.status ? t(`project_test_case.status.${draft.status}`) : ""}
                    </span>
                  }
                  className="w-full min-w-0"
                >
                  {TEST_CASE_STATUSES.map((status) => (
                    <CustomSelect.Option key={status} value={status}>
                      {t(`project_test_case.status.${status}`)}
                    </CustomSelect.Option>
                  ))}
                </CustomSelect>
                {reasonsOf("status")}
              </div>
            </SidebarPropertyListItem>
          </div>

          {/* The links are a list, not a single value: the row stacks, and keeps
              the label styling of the rows above it. */}
          {!isCreateMode && testCase && (
            <div className="mt-2 mb-6 flex flex-col gap-2">
              <div className="flex items-center gap-1.5 text-body-xs-regular text-tertiary">
                <LinkOutline className="size-4 shrink-0" />
                <span>{t("project_test_case.editor.section_work_items")}</span>
              </div>
              <TestCaseLinksForm
                workspaceSlug={workspaceSlug}
                projectId={projectId}
                testCaseId={testCase.id}
                links={links}
                disabled={disabled}
                onLinksChange={onLinksChange}
              />
            </div>
          )}

          <div className="mt-2 mb-6 space-y-2.5">
            {testCase?.created_by && (
              <SidebarPropertyListItem icon={UserOutline} label={t("common.created_by")}>
                <span className="min-w-0 truncate text-body-xs-regular text-secondary">
                  {testCase.created_by.display_name}
                </span>
              </SidebarPropertyListItem>
            )}

            {testCase?.updated_at && (
              <SidebarPropertyListItem icon={UpdatedAtOutline} label={t("common.updated_at")}>
                <span className="min-w-0 truncate text-body-xs-regular text-secondary">
                  {renderFormattedDate(testCase.updated_at)} · {renderFormattedTime(testCase.updated_at)}
                </span>
              </SidebarPropertyListItem>
            )}
          </div>
        </div>
      </div>
    </aside>
  );
}
