/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { forwardRef, useMemo } from "react";
// plane imports
import { RichTextEditorWithRef } from "@plane/editor";
import type { EditorRefApi, IRichTextEditorProps, TFileHandler } from "@plane/editor";
import type { MakeOptional, TSearchEntityRequestPayload, TSearchResponse } from "@plane/types";
import { cn } from "@plane/utils";
// components
import { EditorMentionsRoot } from "@/components/editor/embeds/mentions";
// helpers
import { convertIssueReferencesToLinks, getIssueReferenceConfig } from "@/helpers/issue-reference.helper";
// hooks
import { useEditorConfig, useEditorMention } from "@/hooks/editor";
import { useMember } from "@/hooks/store/use-member";
import { useParseEditorContent } from "@/hooks/use-parse-editor-content";
// plane web hooks
import { useEditorFlagging } from "@/hooks/use-editor-flagging";

type RichTextEditorWrapperProps = MakeOptional<
  Omit<IRichTextEditorProps, "fileHandler" | "mentionHandler" | "extendedEditorProps">,
  "disabledExtensions" | "editable" | "flaggedExtensions" | "getEditorMetaData"
> & {
  workspaceSlug: string;
  workspaceId: string;
  projectId?: string;
  issueSequenceId?: number;
} & (
    | {
        editable: false;
      }
    | {
        editable: true;
        searchMentionCallback: (payload: TSearchEntityRequestPayload) => Promise<TSearchResponse>;
        uploadFile: TFileHandler["upload"];
        duplicateFile: TFileHandler["duplicate"];
      }
  );

export const RichTextEditor = forwardRef(function RichTextEditor(
  props: RichTextEditorWrapperProps,
  ref: React.ForwardedRef<EditorRefApi>
) {
  const {
    containerClassName,
    editable,
    workspaceSlug,
    workspaceId,
    projectId,
    initialValue,
    disabledExtensions: additionalDisabledExtensions = [],
    ...rest
  } = props;
  // link work item references (e.g. PLANE-1) while typing and on paste
  const issueReference = useMemo(() => getIssueReferenceConfig(workspaceSlug), [workspaceSlug]);
  // persisted read-only content never goes through the autolink plugin, so link its identifiers once
  const processedInitialValue = useMemo(
    () => (editable === false ? convertIssueReferencesToLinks(initialValue, workspaceSlug) : initialValue),
    [editable, initialValue, workspaceSlug]
  );
  // store hooks
  const { getUserDetails } = useMember();
  // editor flaggings
  const { richText: richTextEditorExtensions } = useEditorFlagging({
    workspaceSlug,
    projectId,
  });
  // use editor mention
  const { fetchMentions } = useEditorMention({
    searchEntity: editable ? async (payload) => await props.searchMentionCallback(payload) : async () => ({}),
  });
  // editor config
  const { getEditorFileHandlers } = useEditorConfig();
  // parse content
  const { getEditorMetaData } = useParseEditorContent({
    projectId,
    workspaceSlug,
  });

  return (
    <RichTextEditorWithRef
      ref={ref}
      disabledExtensions={[...richTextEditorExtensions.disabled, ...(additionalDisabledExtensions ?? [])]}
      editable={editable}
      flaggedExtensions={richTextEditorExtensions.flagged}
      fileHandler={getEditorFileHandlers({
        projectId,
        uploadFile: editable ? props.uploadFile : async () => "",
        duplicateFile: editable ? props.duplicateFile : async () => "",
        workspaceId,
        workspaceSlug,
      })}
      getEditorMetaData={getEditorMetaData}
      initialValue={processedInitialValue}
      issueReference={issueReference}
      mentionHandler={{
        searchCallback: async (query) => {
          const res = await fetchMentions(query);
          if (!res) throw new Error("Failed in fetching mentions");
          return res;
        },
        renderComponent: EditorMentionsRoot,
        getMentionedEntityDetails: (id) => ({
          display_name: getUserDetails(id)?.display_name ?? "",
        }),
      }}
      extendedEditorProps={{}}
      {...rest}
      containerClassName={cn("relative pb-3 pl-3", containerClassName)}
    />
  );
});

RichTextEditor.displayName = "RichTextEditor";
