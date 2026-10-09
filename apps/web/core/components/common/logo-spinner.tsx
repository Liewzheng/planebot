/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// The loading mark lives in `@plane/propel` so web / space / admin cannot drift
// apart; this file stays as the app-local import path (PLANE-59).
export { LogoSpinner } from "@plane/propel/logo-spinner";
