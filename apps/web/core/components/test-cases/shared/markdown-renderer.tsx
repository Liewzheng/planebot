/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@plane/utils";

type Props = {
  /** The Markdown source — it is rendered as it was typed. */
  markdown: string;
  className?: string;
};

/**
 * The renderer of the Markdown of the test-case surface.
 *
 * The rules of the feature read the measured value with the GFM
 * strikethrough extension: `~~ok~~` is read as `ok` and is rejected as an
 * evaluation word. A renderer without that extension shows the tildes, so
 * the tester would read something the rules do not judge. The extension
 * is therefore enabled here — and only here: the pages and the comments
 * of the product keep the renderer they had.
 *
 * The renderer of the whole app
 * (`apps/web/core/components/ui/markdown-to-component.tsx`) is not used
 * here on purpose — it is a shared component and the change would reach
 * every page that renders Markdown.
 */
export function TestCaseMarkdownRenderer(props: Props) {
  const { markdown, className } = props;

  if (!markdown) return null;

  return (
    <div className={cn("flex flex-col gap-2 text-13 text-secondary", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p className="break-words whitespace-pre-wrap">{children}</p>,
          a: ({ children, href }) => (
            <a href={href} className="underline hover:no-underline" target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
          ul: ({ children }) => <ul className="ml-4 list-disc">{children}</ul>,
          ol: ({ children }) => <ol className="ml-4 list-decimal">{children}</ol>,
          code: ({ children }) => (
            <code className="font-mono rounded-sm bg-layer-3 px-1 py-0.5 text-12">{children}</code>
          ),
          pre: ({ children }) => (
            <pre className="font-mono overflow-x-auto rounded-md bg-layer-3 p-2 text-12">{children}</pre>
          ),
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-subtle-1 pl-3 text-tertiary">{children}</blockquote>
          ),
          table: ({ children }) => (
            <table className="w-full border-collapse text-12">
              <tbody>{children}</tbody>
            </table>
          ),
          th: ({ children }) => <th className="border border-subtle-1 px-2 py-1 text-left">{children}</th>,
          td: ({ children }) => <td className="border border-subtle-1 px-2 py-1">{children}</td>,
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
