/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import * as React from "react";

import type { ISvgIcons } from "../type";

// pbot brand mark: rounded blue square with a white bold "p".
// Kept under the PlaneLogo name (internal identifier) while the visible
// glyph is the pbot mark.
export function PlaneLogo({ width = "52", height = "52", className }: ISvgIcons) {
  return (
    <svg width={width} height={height} viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg" className={className}>
      <rect width="64" height="64" rx="14" fill="#3f76ff" />
      <text
        x="32"
        y="33"
        textAnchor="middle"
        dominantBaseline="central"
        fontFamily="Arial, Helvetica, sans-serif"
        fontSize="38"
        fontWeight="700"
        fill="#ffffff"
      >
        p
      </text>
    </svg>
  );
}
