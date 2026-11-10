/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useTranslation } from "@plane/i18n";
import type { ITestRunSummaryReport } from "@plane/types";

type Props = {
  summary?: ITestRunSummaryReport;
  isLoading: boolean;
  hasError: boolean;
};

/**
 * The summary of the batch (STD-SW-003 §6.2 rule 3 / §6.3).
 *
 * The four counts and the deviation list come from
 * `GET .../test-runs/<id>/summary/` — the server computes them with the same
 * rules that judge the record, and the client renders them as they are. No
 * number on this panel is counted in the browser: the four numbers are not a
 * partition (a case may be `FAIL` and hold an unexecuted step, which is the
 * 「需人工」 of the sample), and a client that added them up would print a
 * different table than the standard.
 *
 * The deviation list prints one line per deviating case: its number and the
 * one-sentence phenomenon the tester wrote (the measured value of the first
 * ✘ step when there is no summary sentence).
 */
export function TestCaseRunSummaryPanel(props: Props) {
  const { summary, isLoading, hasError } = props;
  const { t } = useTranslation();

  return (
    <section className="flex flex-col gap-3 rounded-md border border-subtle-1 bg-layer-1 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-12 font-semibold text-primary">{t("project_test_case.execution.summary.title")}</h3>
        <span className="text-11 text-tertiary">{t("project_test_case.execution.summary.source_hint")}</span>
      </div>

      {isLoading && <p className="text-12 text-tertiary">{t("project_test_case.execution.summary.loading")}</p>}

      {!isLoading && hasError && (
        <p className="text-12 text-danger-primary">{t("project_test_case.execution.summary.load_error")}</p>
      )}

      {summary && (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
            {[
              {
                key: "pass",
                label: t("project_test_case.execution.summary.pass"),
                value: summary.pass,
                tone: "text-success-primary",
              },
              {
                key: "fail",
                label: t("project_test_case.execution.summary.fail"),
                value: summary.fail,
                tone: "text-danger-primary",
              },
              {
                key: "na",
                label: t("project_test_case.execution.summary.na"),
                value: summary.na,
                tone: "text-secondary",
              },
              {
                key: "manual",
                label: t("project_test_case.execution.summary.manual"),
                value: summary.manual,
                tone: "text-warning-primary",
              },
              {
                key: "total",
                label: t("project_test_case.execution.summary.total"),
                value: summary.total,
                tone: "text-primary",
              },
            ].map((item) => (
              <div
                key={item.key}
                className="flex flex-col gap-0.5 rounded-md border border-subtle-1 bg-layer-2 px-2.5 py-2"
              >
                <span className="text-11 text-tertiary">{item.label}</span>
                <span className={`text-16 font-semibold ${item.tone}`}>{item.value}</span>
              </div>
            ))}
          </div>

          <div className="flex flex-col gap-1.5">
            <h4 className="text-11 font-semibold text-secondary">
              {t("project_test_case.execution.summary.deviations")}
            </h4>
            {summary.deviations.length === 0 ? (
              <p className="text-12 text-tertiary">{t("project_test_case.execution.summary.no_deviation")}</p>
            ) : (
              <ul className="flex flex-col gap-1">
                {summary.deviations.map((deviation) => (
                  <li
                    // A case number is unique inside one batch — the server
                    // refuses a duplicate (duplicate_case_number).
                    key={deviation.case_number}
                    className="flex min-w-0 flex-col gap-0.5 rounded-md border border-subtle-1 bg-layer-2 px-2.5 py-1.5 sm:flex-row sm:items-baseline sm:gap-2"
                  >
                    <span className="font-mono shrink-0 text-11 text-tertiary">{deviation.case_number}</span>
                    <span className="min-w-0 text-12 break-words text-secondary">
                      {deviation.summary || t("project_test_case.execution.summary.no_phenomenon")}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </section>
  );
}
