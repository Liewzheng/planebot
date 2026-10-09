# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.apps import AppConfig


class ServicePrincipalsConfig(AppConfig):
    name = "plane.service_principals"

    def ready(self):
        # No signal receivers yet — the SP foundation owns no post_save hooks
        # because SPs do not auto-join projects or workspaces. Future missions
        # that add live-server / webhook wiring will import their receivers
        # from this hook.
        pass