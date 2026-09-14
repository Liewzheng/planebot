/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// pbot brand mark with a pulse animation, shown while pages load
export function LogoSpinner() {
  return (
    <div className="flex items-center justify-center">
      <svg
        className="h-6 w-auto animate-pulse sm:h-11"
        viewBox="0 0 64 64"
        xmlns="http://www.w3.org/2000/svg"
        role="img"
        aria-label="pbot logo"
      >
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
    </div>
  );
}
