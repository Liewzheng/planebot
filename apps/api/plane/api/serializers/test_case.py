# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The serializers of the test-case feature on the v1 surface.

The **field names are the contract** of the feature: the v1 surface and
the app surface hand out the same fields, so the serializers are
imported from the app layer instead of being written a second time. A
second set of serializers would be a second contract, and the two would
drift the first time a field is added to one of them.

What the v1 surface adds on top of the app surface is the *transport*,
not the shape: the pagination envelope of the public API, and the
``fields`` / ``expand`` query parameters the other v1 resources carry.
Neither changes a field name, so neither is a second contract.
"""

# Module imports
from plane.app.serializers.test_case import (
    FileAssetAttachmentSerializer,
    TestCaseIssueSerializer,
    TestCaseModuleSerializer,
    TestCaseSerializer,
    TestCaseStepSerializer,
    TestCaseWriteSerializer,
)

__all__ = [
    "FileAssetAttachmentSerializer",
    "TestCaseIssueSerializer",
    "TestCaseModuleSerializer",
    "TestCaseSerializer",
    "TestCaseStepSerializer",
    "TestCaseWriteSerializer",
]
