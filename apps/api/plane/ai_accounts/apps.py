# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.apps import AppConfig


class AIAccountsConfig(AppConfig):
    name = "plane.ai_accounts"

    def ready(self):
        # M12 retired the historical AI bot signals and policy hooks; the
        # app now only persists the ``AIAccount`` table until the cleanup
        # migration drops it in a follow-up release. Nothing to wire here.
        pass
