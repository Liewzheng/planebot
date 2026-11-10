/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type {
  ITestCaseErrorEntry,
  ITestCaseErrorResponse,
  ITestRunCase,
  ITestRunStepRecord,
  TTestRunCaseWrite,
} from "@plane/types";

/**
 * The text fields of a case record that the execution form writes while the
 * tester types. The conclusion is not one of them: it is a deliberate act and
 * goes out on its own.
 */
export const RUN_CASE_TEXT_FIELDS = [
  "evidence",
  "remarks",
  "actual_result_summary",
  "na_reason",
  "na_criteria_source",
  "manual_executor",
  "manual_condition",
] as const;

export type TRunCaseTextField = (typeof RUN_CASE_TEXT_FIELDS)[number];

/** The draft of the text fields of one case record. */
export type TRunCaseDraft = Record<TRunCaseTextField, string>;

/** The draft of a case record as the server holds it. */
export const draftFromRunCase = (runCase: ITestRunCase): TRunCaseDraft => ({
  evidence: runCase.evidence ?? "",
  remarks: runCase.remarks ?? "",
  actual_result_summary: runCase.actual_result_summary ?? "",
  na_reason: runCase.na_reason ?? "",
  na_criteria_source: runCase.na_criteria_source ?? "",
  manual_executor: runCase.manual_executor ?? "",
  manual_condition: runCase.manual_condition ?? "",
});

/** The fields of `draft` that differ from `saved` — the write of a pause. */
export const changedCaseFields = (saved: TRunCaseDraft, draft: TRunCaseDraft): TTestRunCaseWrite => {
  const changed: TTestRunCaseWrite = {};
  for (const field of RUN_CASE_TEXT_FIELDS) {
    if (draft[field] !== saved[field]) changed[field] = draft[field];
  }
  return changed;
};

/**
 * The Markdown source as the rules read it: the markup is not a measured
 * value, a picture is not a sentence, and a link contributes its label.
 *
 * This is a **hint** for the form — it decides whether the ✔/✘ guard has
 * something to record. The authority is the server: the finalize rules parse
 * the source themselves (`plane.db.testcase_validity`). The keystroke-level
 * check here must therefore never be stricter than the server in a way that
 * blocks a valid record; it only refuses an obviously empty one.
 */
export const markdownText = (markdown: string): string =>
  (markdown ?? "")
    // Images carry no text of their own — not even their alt text.
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/!\[[^\]]*\]\[[^\]]*\]/g, " ")
    // A link contributes its label.
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]*)\]\[[^\]]*\]/g, "$1")
    .replace(/<[^>]*>/g, " ")
    .replace(/&[a-z#0-9]+;/gi, " ")
    .replace(/^\s{0,3}(#{1,6}|>|[-*+]|\d+[.)])\s+/gm, " ")
    .replace(/[`*_~]/g, " ")
    .replace(/\s+/g, " ")
    .trim();

/** True when the step carries a measured value: text or an attached file. */
export const stepIsMeasured = (step: Pick<ITestRunStepRecord, "actual_value" | "attachments">): boolean =>
  markdownText(step.actual_value).length > 0 || (step.attachments ?? []).length > 0;

/** True when the case record carries evidence: text or an attached file. */
export const caseHasEvidence = (runCase: Pick<ITestRunCase, "evidence" | "attachments">): boolean =>
  markdownText(runCase.evidence).length > 0 || (runCase.attachments ?? []).length > 0;

/** One field-level reason: the key of the field and the sentences of it. */
export type TRunFieldErrors = Record<string, string[]>;

/** The reasons of one case record: its own fields and its steps. */
export type TRunCaseErrors = {
  fields: TRunFieldErrors;
  /** Keyed by the `sequence_id` of the step — the number on screen. */
  steps: Record<number, TRunFieldErrors>;
};

/** The reasons of a refused batch write, routed to where they belong. */
export type TParsedRunErrors = {
  /** The batch header, keyed by the field the server named. */
  run: TRunFieldErrors;
  /** The case records, keyed by the case number on screen. */
  cases: Record<string, TRunCaseErrors>;
  /** Reasons that name neither a field nor a case. */
  form: string[];
};

const emptyCaseErrors = (): TRunCaseErrors => ({ fields: {}, steps: {} });

const addTo = (target: TRunFieldErrors, field: string, message: string) => {
  target[field] = [...(target[field] ?? []), message];
};

/**
 * Route the entries of the error envelope to the batch header, the case
 * records and their steps.
 *
 * The server names the location in `path` (`cases[TC-PWR-001].steps[2].
 * actual_value`, `cases[TC-PWR-001].conclusion`, `version_number`), and
 * repeats it in `case_number` / `step_sequence`. Both are read: an entry
 * without a path still lands on the right case and step.
 */
export const parseRunErrors = (envelope?: ITestCaseErrorResponse | null): TParsedRunErrors => {
  const parsed: TParsedRunErrors = { run: {}, cases: {}, form: [] };
  const entries: ITestCaseErrorEntry[] = Array.isArray(envelope?.errors) ? envelope.errors : [];

  for (const entry of entries) {
    const message = (entry?.message || entry?.detail || envelope?.error || "").trim();
    if (!message) continue;

    const path = entry.path ?? "";
    const caseMatch = path.match(/^cases\[([^\]]+)\](?:\.(.*))?$/);
    const caseNumber = caseMatch?.[1] ?? entry.case_number ?? "";
    const rest = caseMatch?.[2] ?? "";

    const stepMatch = rest.match(/^steps\[(\d+)\](?:\.(\w+))?$/);
    const stepSequence = stepMatch ? Number(stepMatch[1]) : (entry.step_sequence ?? null);

    if (stepSequence !== null) {
      const field = stepMatch?.[2] || entry.field || "__step__";
      if (!caseNumber) {
        parsed.form.push(message);
        continue;
      }
      const caseErrors = (parsed.cases[caseNumber] ??= emptyCaseErrors());
      addTo((caseErrors.steps[stepSequence] ??= {}), field, message);
      continue;
    }

    // `""` is not a fallback for `??`, so the entry's own field is consulted
    // when the path names none — a run-level reason carries only `field`.
    const field = rest.split(".").pop() || entry.field || "";

    if (caseNumber) {
      const caseErrors = (parsed.cases[caseNumber] ??= emptyCaseErrors());
      addTo(caseErrors.fields, field || "__case__", message);
      continue;
    }

    if (field) addTo(parsed.run, field, message);
    else parsed.form.push(message);
  }

  return parsed;
};

/** The numbers of the steps that carry a reason — the cards they belong to. */
export const stepsWithErrors = (errors?: TRunCaseErrors): number[] =>
  errors ? Object.keys(errors.steps).map(Number) : [];

/** The sentence of a refusal, when the envelope carries one. */
const envelopeSentence = (envelope?: ITestCaseErrorResponse | null): string | undefined => {
  const sentence = (envelope?.error ?? "").trim();
  return sentence.length > 0 ? sentence : undefined;
};

/**
 * The reasons of a refused write that belong to one case record, as the
 * form shows them.
 *
 * The feature refuses in two shapes and both have to reach the screen:
 *
 * * the batch rules name their location — `cases[TC-…].conclusion`,
 *   `cases[TC-…].steps[2].actual_value` — and those entries land in
 *   `parsed.cases[…]`;
 * * a row of one case is refused by the model/serializer
 *   (`validation_error_response`), whose entry carries **only** the field
 *   name (`path: "conclusion"`) and no case number. Such an entry lands in
 *   `parsed.run` — and a form that read `parsed.cases[…]` alone dropped it
 *   silently (the conclusion dialog closed nothing and said nothing).
 *
 * Order: the reasons addressed to this case first, then the ones of the
 * batch header (a row-level refusal belongs here), then the entries that
 * name no place at all, and the envelope's own sentence only when none of the
 * three produced anything — so no refusal can end up nowhere.
 */
export const collectCaseErrors = (
  parsed: TParsedRunErrors,
  envelope?: ITestCaseErrorResponse | null,
  caseNumber?: string
): TRunFieldErrors => {
  const merged: TRunFieldErrors = {};

  const merge = (source: TRunFieldErrors) => {
    for (const [field, messages] of Object.entries(source)) {
      for (const message of messages) addTo(merged, field, message);
    }
  };

  merge((caseNumber ? parsed.cases[caseNumber]?.fields : undefined) ?? {});
  merge(parsed.run);

  // The unplaced reasons repeat per case; the same sentence twice says
  // nothing the first one did not.
  const unplaced = [...new Set(parsed.form)];
  for (const message of unplaced) addTo(merged, "__form__", message);

  if (Object.keys(merged).length === 0) {
    const sentence = envelopeSentence(envelope);
    if (sentence) addTo(merged, "__form__", sentence);
  }

  return merged;
};

/**
 * The first reason of a refused step write, as one sentence.
 *
 * A step PATCH is refused by the model (`_assert_verdict_is_final`, a
 * serializer detail), whose entry names the field but not the step — the
 * entry lands in `parsed.run`, not in `parsed.cases[…].steps[…]`. Reading
 * only the latter left the tester with the generic "the verdict could not be
 * written" while the server had said exactly why.
 */
export const firstStepReason = (
  parsed: TParsedRunErrors,
  envelope?: ITestCaseErrorResponse | null,
  caseNumber?: string,
  stepSequence?: number
): string | undefined => {
  const stepErrors =
    caseNumber && typeof stepSequence === "number" ? parsed.cases[caseNumber]?.steps[stepSequence] : undefined;
  const stepFirst = Object.values(stepErrors ?? {})[0]?.[0];
  if (stepFirst) return stepFirst;

  const runFirst = Object.values(parsed.run)[0]?.[0];
  if (runFirst) return runFirst;

  if (parsed.form[0]) return parsed.form[0];

  return envelopeSentence(envelope);
};

/**
 * Give a refusal that names no location at least one visible sentence.
 *
 * A 403/404/409 body of this feature carries `error` and `code` but often no
 * `errors[]` entry the parser can place (`{"error": "Test run not found"}`,
 * `{"error": "A completed batch is frozen."}`). The caller renders whatever
 * the parser found; without this, such a refusal leaves the screen with a
 * heading and no reason.
 */
export const withRefusalSentence = (
  parsed: TParsedRunErrors,
  envelope?: ITestCaseErrorResponse | null
): TParsedRunErrors => {
  if (parsed.form.length > 0) return parsed;
  if (Object.keys(parsed.run).length > 0) return parsed;
  if (Object.keys(parsed.cases).length > 0) return parsed;

  const sentence = envelopeSentence(envelope);
  return sentence ? { ...parsed, form: [sentence] } : parsed;
};

/**
 * The hint of a ✔/✘ attempt that has nothing to record. §4.4 rule 1: a ✘
 * with no measured value is an invalid record — the measured value may be
 * the text or an attached screenshot. §6.1 rule 1: a step with neither box
 * ticked was not executed, and its measured value says why (「未准备环境」), so
 * the reason is what the ✘ guard asks for.
 */
export const missingMeasurement = (step: Pick<ITestRunStepRecord, "actual_value" | "attachments">): boolean =>
  !stepIsMeasured(step);
