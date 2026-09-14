/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import * as React from "react";

// pbot logo lockup (brand mark + wordmark). Not part of @makeplane/propel's
// icon set, so it is kept as a local asset. Kept under the PlaneLockup name
// (internal identifier) while the visible glyph is the pbot lockup.

type PlaneLockupProps = {
  width?: string | number;
  height?: string | number;
  className?: string;
  color?: string;
};

export function PlaneLockup({ width = "253", height = "53", className, color = "currentColor" }: PlaneLockupProps) {
  return (
    <svg width={width} height={height} viewBox="0 0 253 53" xmlns="http://www.w3.org/2000/svg" className={className}>
      <rect width="53" height="53" rx="12" fill="#3f76ff" />
      <text
        x="26.5"
        y="27.5"
        textAnchor="middle"
        dominantBaseline="central"
        fontFamily="Arial, Helvetica, sans-serif"
        fontSize="32"
        fontWeight="700"
        fill="#ffffff"
      >
        p
      </text>
      <text
        x="66"
        y="27.5"
        dominantBaseline="central"
        fontFamily="Arial, Helvetica, sans-serif"
        fontSize="34"
        fontWeight="700"
        fill={color}
      >
        pbot
      </text>
    </svg>
  );
}
