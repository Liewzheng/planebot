/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { NodeWithPos } from "@tiptap/core";
import { combineTransactionSteps, findChildrenInRange, getChangedRanges, getMarksBetween } from "@tiptap/core";
import type { MarkType } from "@tiptap/pm/model";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { find } from "linkifyjs";
// local imports
import type { TIssueReferenceConfig } from "../extension";

type AutolinkOptions = {
  type: MarkType;
  validate?: (url: string) => boolean;
  issueReference?: TIssueReferenceConfig;
};

export function autolink(options: AutolinkOptions): Plugin {
  return new Plugin({
    key: new PluginKey("autolink"),
    appendTransaction: (transactions, oldState, newState) => {
      const docChanges = transactions.some((transaction) => transaction.docChanged) && !oldState.doc.eq(newState.doc);
      const preventAutolink = transactions.some((transaction) => transaction.getMeta("preventAutolink"));

      if (!docChanges || preventAutolink) {
        return;
      }

      const { tr } = newState;
      const transform = combineTransactionSteps(oldState.doc, [...transactions]);
      const changes = getChangedRanges(transform);

      changes.forEach(({ newRange }) => {
        // Now let’s see if we can add new links.
        const nodesInChangedRanges = findChildrenInRange(newState.doc, newRange, (node) => node.isTextblock);

        let textBlock: NodeWithPos | undefined;
        let textBeforeWhitespace: string | undefined;

        if (nodesInChangedRanges.length > 1) {
          // Grab the first node within the changed ranges (ex. the first of two paragraphs when hitting enter).
          textBlock = nodesInChangedRanges[0];
          textBeforeWhitespace = newState.doc.textBetween(
            textBlock.pos,
            textBlock.pos + textBlock.node.nodeSize,
            undefined,
            " "
          );
        } else if (
          nodesInChangedRanges.length &&
          // We want to make sure to include the block separator argument to treat hard breaks like spaces.
          newState.doc.textBetween(newRange.from, newRange.to, " ", " ").endsWith(" ")
        ) {
          textBlock = nodesInChangedRanges[0];
          textBeforeWhitespace = newState.doc.textBetween(textBlock.pos, newRange.to, undefined, " ");
        }

        if (textBlock && textBeforeWhitespace) {
          const wordsBeforeWhitespace = textBeforeWhitespace.split(" ").filter((s) => s !== "");

          if (wordsBeforeWhitespace.length <= 0) {
            return false;
          }

          const lastWordBeforeSpace = wordsBeforeWhitespace[wordsBeforeWhitespace.length - 1];
          const lastWordAndBlockOffset = textBlock.pos + textBeforeWhitespace.lastIndexOf(lastWordBeforeSpace);

          if (!lastWordBeforeSpace) {
            return false;
          }

          // Candidates for the link mark: resolved href plus doc positions.
          const linkCandidates: { from: number; to: number; href: string }[] = [];

          const isInsideCodeMark = (from: number, to: number) =>
            !!newState.schema.marks.code && newState.doc.rangeHasMark(from, to, newState.schema.marks.code);

          find(lastWordBeforeSpace)
            .filter((link) => link.isLink)
            // Calculate link position.
            .map((link) => ({
              ...link,
              from: lastWordAndBlockOffset + link.start + 1,
              to: lastWordAndBlockOffset + link.end + 1,
            }))
            // ignore link inside code mark
            .filter((link) => !isInsideCodeMark(link.from, link.to))
            // validate link
            .filter((link) => {
              if (options.validate) {
                return options.validate(link.value);
              }
              return true;
            })
            .forEach((link) => linkCandidates.push({ from: link.from, to: link.to, href: link.href }));

          // Work item identifiers (e.g. PLANE-1) in the same trigger position:
          // right after the identifier, when a whitespace is typed. These are
          // resolved to internal routes, so they bypass `validate`.
          if (options.issueReference) {
            const { pattern, resolve } = options.issueReference;
            const globalPattern = new RegExp(pattern.source, pattern.global ? pattern.flags : `${pattern.flags}g`);
            let match: RegExpExecArray | null;
            while ((match = globalPattern.exec(lastWordBeforeSpace)) !== null) {
              // Guard against zero-length matches looping forever.
              if (!match[0]) {
                globalPattern.lastIndex += 1;
                continue;
              }
              const from = lastWordAndBlockOffset + match.index + 1;
              const to = from + match[0].length;
              // ignore identifier inside code mark
              if (isInsideCodeMark(from, to)) {
                continue;
              }
              linkCandidates.push({ from, to, href: resolve(match[0]) });
            }
          }

          // Add link mark.
          linkCandidates.forEach(({ from, to, href }) => {
            if (getMarksBetween(from, to, newState.doc).some((item) => item.mark.type === options.type)) {
              return;
            }

            tr.addMark(
              from,
              to,
              options.type.create({
                href,
              })
            );
          });
        }
      });

      if (!tr.steps.length) {
        return;
      }

      return tr;
    },
  });
}
