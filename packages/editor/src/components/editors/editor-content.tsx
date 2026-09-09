/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { EditorContent } from "@tiptap/react";
import type { Editor } from "@tiptap/react";
import type { ReactNode } from "react";

type Props = {
  className?: string;
  children?: ReactNode;
  editor: Editor | null;
  id: string;
  tabIndex?: number;
};

export function EditorContentWrapper(props: Props) {
  const { editor, className, children, tabIndex, id } = props;

  return (
    <div
      tabIndex={tabIndex}
      onFocus={(event) => {
        // only redirect focus that lands on the wrapper itself (e.g. via
        // tabIndex); focus entering interactive elements inside node views
        // (e.g. the math block source textarea) must stay where it is
        if (event.target !== event.currentTarget) return;
        editor?.chain().focus(undefined, { scrollIntoView: false }).run();
      }}
      className={className}
    >
      <EditorContent editor={editor} id={id} />
      {children}
    </div>
  );
}
