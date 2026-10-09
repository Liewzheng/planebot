# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Plane core utilities.

Houses cross-cutting logic shared by every execution surface (internal app
API, public v1 API, live server, webhook/event). Anything in ``core`` must
remain framework-agnostic so it can be imported from any Django app.
"""