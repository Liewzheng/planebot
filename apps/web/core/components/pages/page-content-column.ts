/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { cn } from "@plane/utils";

// Single source of truth for the page content column width. The live document
// body and the version history view both wrap their content in this class so
// the two modes stay visually identical.
export const getPageContentColumnClassName = (isFullWidth: boolean) =>
  cn("mx-auto block w-full max-w-[720px] bg-transparent transition-all duration-200 ease-in-out", {
    "max-w-[1152px]": isFullWidth,
  });
