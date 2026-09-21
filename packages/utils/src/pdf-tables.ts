/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/** Rough width of one displayed character, in px, for the mixed CJK/latin body
 *  font at the export's table size. */
const PX_PER_CHAR = 7;
/** Cell padding (both sides) plus border slack, in px. */
const CELL_PADDING_PX = 20;

const displayUnits = (text: string): number => {
  let units = 0;
  for (const char of text) {
    units += char.codePointAt(0)! > 0xff ? 1 : 0.55;
  }
  return units;
};

/**
 * Content-based column widths (px) for a table whose rows carry plain-text
 * cells. Each column starts at the width of its widest cell, then the total
 * is scaled to `contentWidthPx`: narrow columns are protected from shrinking
 * below 1.5× an even share and the leftover goes to the flexible ones, so a
 * key/value table does not waste half the page on the key column.
 */
export const tableColumnWidths = (rowTexts: string[][], contentWidthPx: number): number[] => {
  const columns = Math.max(0, ...rowTexts.map((cells) => cells.length));
  if (!columns || contentWidthPx <= 0) return [];

  const widths = Array.from({ length: columns }, (_, i) => {
    const units = Math.max(1, ...rowTexts.map((cells) => displayUnits(cells[i] || "")));
    return units * PX_PER_CHAR + CELL_PADDING_PX;
  });

  let total = widths.reduce((s, w) => s + w, 0);
  if (total > contentWidthPx) {
    const evenShare = contentWidthPx / columns;
    const isProtected = widths.map((w) => w <= evenShare * 1.5);
    const protectedTotal = widths.filter((_, i) => isProtected[i]).reduce((s, w) => s + w, 0);
    const flexible = widths.flatMap((w, i) => (isProtected[i] ? [] : [i]));
    const leftover = contentWidthPx - protectedTotal;
    if (leftover > 0 && flexible.length > 0) {
      const ft = flexible.reduce((s, i) => s + widths[i], 0);
      flexible.forEach((i) => {
        widths[i] = (widths[i] / ft) * leftover;
      });
    } else {
      const scale = contentWidthPx / total;
      widths.forEach((w, i) => {
        widths[i] = w * scale;
      });
    }
  } else {
    // upscale proportionally so content ratios survive — an equal split would
    // dilute a key/value table back towards even columns
    const scale = contentWidthPx / total;
    widths.forEach((w, i) => {
      widths[i] = w * scale;
    });
  }
  total = widths.reduce((s, w) => s + w, 0);
  return widths.map((w) => (w / total) * contentWidthPx);
};

/** Merge `styleText` into the opening tag's existing `style="..."` attribute,
 *  or add the attribute when the tag has none. Only the opening tag is touched,
 *  so a `style=` inside the element's inner HTML is never matched. */
const stampStyle = (openTag: string, styleText: string): string => {
  const styleMatch = openTag.match(/\bstyle\s*=\s*"([^"]*)"/i);
  if (styleMatch) {
    const merged = `${styleMatch[1]}; ${styleText}`;
    return openTag.replace(styleMatch[0], `style="${merged}"`);
  }
  return openTag.replace(/>\s*$/, ` style="${styleText}">`);
};

/** stampStyle applied to a full cell element: split off the opening tag, stamp
 *  it, reattach the rest. */
const stampCellStyle = (cell: string, styleText: string): string => {
  const openEnd = cell.indexOf(">");
  return stampStyle(cell.slice(0, openEnd + 1), styleText) + cell.slice(openEnd + 1);
};

/**
 * Lay out the `<table>`s of a page export for react-pdf-html, working directly
 * on the HTML string (the export modal is code-split and cannot take a DOM
 * parser along).
 *
 * react-pdf-html renders tables as plain flex Views: every column gets an
 * equal share via the base style, and it does not understand `td { width }`
 * rules from a stylesheet. The stylesheet does pin `flexGrow`/`flexShrink` to
 * 0 on cells though, which makes an inline `width: NNpx` on each cell stick.
 * So per cell we stamp a content-derived pixel width (from
 * {@link tableColumnWidths}) and per table `width: 100%`, and while we are
 * inside the rows we paint the theme's zebra shade on every other body row —
 * the renderer has no `nth-child`.
 *
 * Tables whose rows disagree on their cell count (ragged) are left untouched.
 */
export const applyPdfTableLayoutToHtml = (html: string, contentWidthPx: number): string => {
  if (!html) return html;
  return html.replace(/<table\b[^>]*>[\s\S]*?<\/table>/gi, (tableMatch) => {
    const tableOpenEnd = tableMatch.indexOf(">");
    const tableOpenTag = tableMatch.slice(0, tableOpenEnd + 1);
    const tableBody = tableMatch.slice(tableOpenEnd + 1, -"</table>".length);

    const rowMatches = Array.from(tableBody.matchAll(/<tr\b[^>]*>[\s\S]*?<\/tr>/gi)).map((m) => m[0]);
    if (!rowMatches.length) return tableMatch;

    const cellSets = rowMatches.map((row) => {
      const rowOpenEnd = row.indexOf(">");
      const rowBody = row.slice(rowOpenEnd + 1, -"</tr>".length);
      return Array.from(rowBody.matchAll(/<t[dh]\b[^>]*>[\s\S]*?<\/t[dh]>/gi)).map((m) => m[0]);
    });
    const columnCount = cellSets[0]?.length ?? 0;
    if (!columnCount || cellSets.some((cells) => cells.length !== columnCount)) return tableMatch;

    const widths = tableColumnWidths(
      cellSets.map((cells) => cells.map((cell) => cell.replace(/<[^>]+>/g, "").trim())),
      contentWidthPx
    );
    if (widths.length !== columnCount) return tableMatch;

    // github.css shades even 1-indexed body rows — the first data row stays
    // white, the second is shaded, and so on.
    let bodyRowIndex = 0;
    const rebuiltRows = rowMatches.map((row, rowIdx) => {
      const isHeaderRow = /<th\b/i.test(cellSets[rowIdx][0]);
      const shade = !isHeaderRow && bodyRowIndex++ % 2 === 1;
      const cells = cellSets[rowIdx]
        .map((cell, colIdx) => {
          const width = `width: ${Math.round(widths[colIdx])}px`;
          return shade ? stampCellStyle(cell, `${width}; background-color: #f8f8f8`) : stampCellStyle(cell, width);
        })
        .join("");
      const rowOpenEnd = row.indexOf(">");
      return `${row.slice(0, rowOpenEnd + 1)}${cells}</tr>`;
    });

    return `${stampStyle(tableOpenTag, "width: 100%")}${rebuiltRows.join("")}</table>`;
  });
};
