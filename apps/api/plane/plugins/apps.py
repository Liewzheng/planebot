# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.apps import AppConfig


class PluginsConfig(AppConfig):
    name = "plane.plugins"

    def ready(self):
        # No signal receivers: plugin state changes go through
        # ``registry.py`` so that each transition writes its audit row in the
        # same transaction. Signal-driven state changes would be a second
        # write path to keep in sync with the audit trail.
        pass
