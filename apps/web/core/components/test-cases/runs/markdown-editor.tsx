/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { forwardRef, useCallback, useImperativeHandle, useRef, useState } from "react";
import { useTranslation } from "@plane/i18n";
import { ImageFileIcon, LinkIcon, ListLayoutIcon, MonospaceIcon } from "@plane/propel/icons";
import { cn } from "@plane/utils";
// components
import { TestCaseMarkdownRenderer } from "../shared/markdown-renderer";

/**
 * What the caller may do with the editor from the outside: put Markdown at
 * the caret. The image button of the toolbar and the upload that follows it
 * both need it — the picture is inserted where the tester is writing, not at
 * the end of the text.
 */
export type TTestCaseMarkdownEditorHandle = {
  insertAtCursor: (markdown: string) => void;
  focus: () => void;
};

type Props = {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  onBlur?: () => void;
  disabled?: boolean;
  placeholder?: string;
  /** The caller opened the file picker — the editor does not upload itself. */
  onRequestImage?: () => void;
  hasError?: boolean;
  rows?: number;
};

type TWrap = { prefix: string; suffix: string; placeholder: string };

/**
 * The Markdown editor of the execution form (the measured value, the
 * evidence, the notes).
 *
 * The text is the **source** the server stores and hands back: it is neither
 * converted on write nor stripped on read. The preview renders exactly that
 * source through the GFM renderer the rest of the feature uses — the same one
 * the rules parse with, so the tester reads what the rules judge.
 *
 * The toolbar carries the five actions the mission asks for: bold, inline
 * code, list, link and image. Every button has a visible label: a phone
 * tester has no hover tooltip to read.
 */
export const TestCaseMarkdownEditor = forwardRef<TTestCaseMarkdownEditorHandle, Props>(function TestCaseMarkdownEditor(
  props: Props,
  ref
) {
  const {
    id,
    label,
    value,
    onChange,
    onBlur,
    disabled = false,
    placeholder,
    onRequestImage,
    hasError,
    rows = 4,
  } = props;
  const { t } = useTranslation();
  // states
  const [isPreview, setIsPreview] = useState(false);
  // refs
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const hasFocusedRef = useRef(false);

  const applyToSelection = useCallback(
    (wrap: TWrap) => {
      const textarea = textareaRef.current;
      if (!textarea) return;

      const start = textarea.selectionStart ?? value.length;
      const end = textarea.selectionEnd ?? value.length;
      const selected = value.slice(start, end);
      const body = selected || wrap.placeholder;
      const next = `${value.slice(0, start)}${wrap.prefix}${body}${wrap.suffix}${value.slice(end)}`;

      onChange(next);

      // Put the caret around the inserted body — on the placeholder when
      // nothing was selected, after the selection when there was one.
      const bodyStart = start + wrap.prefix.length;
      requestAnimationFrame(() => {
        const node = textareaRef.current;
        if (!node) return;
        node.focus();
        node.setSelectionRange(bodyStart, bodyStart + body.length);
      });
    },
    [onChange, value]
  );

  const insertAtCursor = useCallback(
    (markdown: string) => {
      const textarea = textareaRef.current;
      // A textarea that was never focused has its caret at 0; an upload
      // started before the tester wrote anything would then put the picture
      // in front of the text. Until the field was focused, the end of the
      // value is the caret.
      const start = textarea && hasFocusedRef.current ? (textarea.selectionStart ?? value.length) : value.length;
      const end = textarea && hasFocusedRef.current ? (textarea.selectionEnd ?? value.length) : value.length;
      const next = `${value.slice(0, start)}${markdown}${value.slice(end)}`;
      onChange(next);

      const caret = start + markdown.length;
      requestAnimationFrame(() => {
        const node = textareaRef.current;
        if (!node) return;
        node.focus();
        node.setSelectionRange(caret, caret);
      });
    },
    [onChange, value]
  );

  useImperativeHandle(ref, () => ({ insertAtCursor, focus: () => textareaRef.current?.focus() }), [insertAtCursor]);

  const prefixLines = useCallback(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;

    const start = textarea.selectionStart ?? value.length;
    const end = textarea.selectionEnd ?? value.length;
    const lineStart = value.lastIndexOf("\n", start - 1) + 1;
    const selected = value.slice(lineStart, Math.max(end, start));
    const lines = selected.split("\n").map((line) => `- ${line}`);
    const next = `${value.slice(0, lineStart)}${lines.join("\n")}${value.slice(Math.max(end, start))}`;
    onChange(next);
  }, [onChange, value]);

  const wrapLink = useCallback(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;

    const start = textarea.selectionStart ?? value.length;
    const end = textarea.selectionEnd ?? value.length;
    const selected = value.slice(start, end);
    const text = selected || t("project_test_case.execution.markdown.link_text");
    const url = "https://";
    const next = `${value.slice(0, start)}[${text}](${url})${value.slice(end)}`;
    onChange(next);

    const urlStart = start + text.length + 3;
    requestAnimationFrame(() => {
      const node = textareaRef.current;
      if (!node) return;
      node.focus();
      node.setSelectionRange(urlStart, urlStart + url.length);
    });
  }, [onChange, t, value]);

  const actions = [
    {
      key: "bold",
      label: t("project_test_case.execution.markdown.bold"),
      // No bold glyph exists in the icon set; the letter itself is the
      // label every Markdown editor uses.
      render: () => <span className="text-12 leading-none font-bold">B</span>,
      onClick: () =>
        applyToSelection({
          prefix: "**",
          suffix: "**",
          placeholder: t("project_test_case.execution.markdown.bold_text"),
        }),
    },
    {
      key: "code",
      label: t("project_test_case.execution.markdown.code"),
      render: () => <MonospaceIcon className="size-3.5" />,
      onClick: () =>
        applyToSelection({
          prefix: "`",
          suffix: "`",
          placeholder: t("project_test_case.execution.markdown.code_text"),
        }),
    },
    {
      key: "list",
      label: t("project_test_case.execution.markdown.list"),
      render: () => <ListLayoutIcon className="size-3.5" />,
      onClick: prefixLines,
    },
    {
      key: "link",
      label: t("project_test_case.execution.markdown.link"),
      render: () => <LinkIcon className="size-3.5" />,
      onClick: wrapLink,
    },
    ...(onRequestImage
      ? [
          {
            key: "image",
            label: t("project_test_case.execution.markdown.image"),
            render: () => <ImageFileIcon className="size-3.5" />,
            onClick: onRequestImage,
          },
        ]
      : []),
  ];

  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <div className="flex flex-wrap items-center justify-between gap-1">
        <label htmlFor={id} className="text-11 font-medium text-secondary">
          {label}
        </label>
        <button
          type="button"
          onClick={() => setIsPreview((current) => !current)}
          className="rounded-sm px-1.5 py-0.5 text-11 text-tertiary hover:bg-layer-2 hover:text-secondary"
          aria-pressed={isPreview}
        >
          {isPreview
            ? t("project_test_case.execution.markdown.edit")
            : t("project_test_case.execution.markdown.preview")}
        </button>
      </div>

      {!isPreview && (
        <div className="flex flex-wrap items-center gap-1 rounded-md border-[0.5px] border-subtle-1 bg-layer-2 px-1.5 py-1">
          {actions.map((action) => (
            <button
              key={action.key}
              type="button"
              disabled={disabled}
              onClick={action.onClick}
              title={action.label}
              aria-label={action.label}
              className="flex items-center gap-1 rounded-sm px-1.5 py-1 text-11 text-tertiary hover:bg-layer-1 hover:text-secondary disabled:cursor-not-allowed disabled:opacity-40"
            >
              {action.render()}
              <span className="hidden sm:inline">{action.label}</span>
            </button>
          ))}
        </div>
      )}

      {isPreview ? (
        <div className="min-h-[72px] rounded-md border-[0.5px] border-subtle-1 bg-layer-1 p-2">
          {value.trim().length > 0 ? (
            <TestCaseMarkdownRenderer markdown={value} />
          ) : (
            <p className="text-12 text-tertiary">{t("project_test_case.execution.markdown.empty_preview")}</p>
          )}
        </div>
      ) : (
        <textarea
          id={id}
          ref={textareaRef}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onFocus={() => {
            hasFocusedRef.current = true;
          }}
          onBlur={onBlur}
          disabled={disabled}
          rows={rows}
          placeholder={placeholder}
          className={cn(
            "w-full resize-y rounded-md border-[0.5px] bg-layer-2 p-2 text-13 text-primary placeholder:text-tertiary focus:outline-none disabled:cursor-not-allowed disabled:opacity-60",
            hasError ? "border-danger-strong" : "border-subtle-1"
          )}
        />
      )}

      <p className="text-11 text-tertiary">{t("project_test_case.execution.markdown.hint")}</p>
    </div>
  );
});
