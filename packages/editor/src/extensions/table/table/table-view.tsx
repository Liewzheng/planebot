/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { Editor, NodeViewProps } from "@tiptap/core";
import type { Node as ProseMirrorNode, ResolvedPos } from "@tiptap/pm/model";
import { TableMap, updateColumnsOnResize } from "@tiptap/pm/tables";
import type { Decoration, NodeView } from "@tiptap/pm/view";
import { h } from "jsx-dom-cjs";
// local imports
import { DEFAULT_COLUMN_WIDTH } from ".";

export class TableView implements NodeView {
  node: ProseMirrorNode;
  cellMinWidth: number;
  decorations: readonly Decoration[];
  editor: Editor;
  getPos: NodeViewProps["getPos"];
  hoveredCell: ResolvedPos | null = null;
  map: TableMap;
  root: HTMLElement;
  table: HTMLTableElement;
  colgroup: HTMLTableColElement;
  tbody: HTMLElement;
  controls?: HTMLElement;

  get dom() {
    return this.root;
  }

  get contentDOM() {
    return this.tbody;
  }

  constructor(
    node: ProseMirrorNode,
    cellMinWidth: number,
    decorations: readonly Decoration[],
    editor: Editor,
    getPos: NodeViewProps["getPos"]
  ) {
    this.node = node;
    this.cellMinWidth = cellMinWidth;
    this.decorations = decorations;
    this.editor = editor;
    this.getPos = getPos;
    this.hoveredCell = null;
    this.map = TableMap.get(node);

    this.colgroup = h(
      "colgroup",
      null,
      Array.from({ length: this.map.width }, () => 1).map(() => h("col"))
    );
    this.tbody = h("tbody");
    this.table = h("table", null, this.colgroup, this.tbody);

    this.root = h(
      "div",
      {
        className: "table-wrapper editor-full-width-block horizontal-scrollbar scrollbar-sm",
      },
      this.table
    );

    this.render();
  }

  update(node: ProseMirrorNode, decorations: readonly Decoration[]) {
    if (node.type !== this.node.type) {
      return false;
    }

    this.node = node;
    this.decorations = [...decorations];
    this.map = TableMap.get(this.node);

    this.render();

    return true;
  }

  render() {
    if (this.colgroup.children.length !== this.map.width) {
      const cols = Array.from({ length: this.map.width }, () => 1).map(() => h("col"));
      this.colgroup.replaceChildren(...cols);
    }

    // A table whose columns are all at the default width has never been
    // resized — freshly inserted tables and imported content (markdown/HTML
    // uploads get the default colwidth from the cell/header attribute default)
    // both land here. Such tables must not be pinned: clear the inline widths
    // so the CSS `table-layout: auto` + `width: max-content` rules size the
    // table and its columns by content, live, without manual dragging
    // (PLANE-45). Tables with any user-resized column keep the pinned layout
    // from updateColumnsOnResize.
    if (this.hasOnlyDefaultWidths()) {
      this.table.style.width = "";
      this.table.style.minWidth = "";
      for (const col of Array.from(this.colgroup.children) as HTMLElement[]) {
        col.style.width = "";
      }
      return;
    }

    updateColumnsOnResize(this.node, this.colgroup, this.table, this.cellMinWidth);
  }

  private hasOnlyDefaultWidths(): boolean {
    const firstRow = this.node.firstChild;
    if (!firstRow) return false;
    let allDefault = true;
    firstRow.forEach((cell) => {
      const colwidth = cell.attrs.colwidth as number[] | null;
      if (colwidth?.some((w) => w !== DEFAULT_COLUMN_WIDTH)) {
        allDefault = false;
      }
    });
    return allDefault;
  }

  ignoreMutation() {
    return true;
  }
}
