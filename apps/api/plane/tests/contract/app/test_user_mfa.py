# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: user MFA management endpoints (/api/users/me/mfa/*)."""

import pytest
from rest_framework import status


@pytest.mark.contract
class TestUserMFAThrottle:
    """The MFA management endpoints are step-up protected and must be
    throttled per authenticated user.

    Regression: they previously used AuthenticationThrottle, which extends
    AnonRateThrottle and never fires for authenticated users — the throttle
    was a no-op. StepUpThrottle (UserRateThrottle) is user-keyed.
    """

    SETUP_URL = "/api/users/me/mfa/setup/"

    def test_setup_endpoint_is_throttled_per_user(self, session_client):
        # 10 requests/min are allowed (default STEP_UP_RATE_LIMIT)
        for _ in range(10):
            response = session_client.post(self.SETUP_URL)
            assert response.status_code != status.HTTP_429_TOO_MANY_REQUESTS

        response = session_client.post(self.SETUP_URL)
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
