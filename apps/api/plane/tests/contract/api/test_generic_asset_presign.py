# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The public-API asset endpoints must build storage with ``request`` alone.

``S3Storage.__init__`` takes a single ``request`` argument, but the v1 asset
views passed ``is_server=True`` — a parameter the class never had — so every
register and read-back raised ``TypeError`` and answered 500 to API-token
clients. The stub below deliberately keeps the real signature: an extra keyword
now fails exactly the way production did, which is what the earlier loose
``mock.patch`` hid.
"""

from unittest import mock

import pytest
from rest_framework import status

from plane.db.models import FileAsset


def storage_stub(request=None):
    """Stand-in for ``S3Storage`` that accepts only ``request``."""
    storage = mock.MagicMock()
    storage.generate_presigned_post.return_value = {"url": "https://signed.example/upload", "fields": {}}
    storage.generate_presigned_url.return_value = "https://signed.example/get"
    return storage


@pytest.fixture
def uploaded_asset(db, workspace, create_user):
    return FileAsset.objects.create(
        attributes={"name": "doc.pdf", "type": "application/pdf", "size": 1024},
        asset=f"{workspace.id}/doc.pdf",
        size=1024,
        workspace=workspace,
        created_by=create_user,
        entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
        is_uploaded=True,
        storage_metadata={"size": 1024},
    )


@pytest.mark.contract
class TestGenericAssetPresignCalls:
    @pytest.mark.django_db
    def test_register_builds_storage_with_request_alone(self, api_key_client, workspace):
        with mock.patch("plane.api.views.asset.S3Storage", side_effect=storage_stub):
            response = api_key_client.post(
                f"/api/v1/workspaces/{workspace.slug}/assets/",
                {"name": "doc.pdf", "type": "application/pdf", "size": 1024},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["upload_data"] == {"url": "https://signed.example/upload", "fields": {}}

    @pytest.mark.django_db
    def test_read_back_builds_storage_with_request_alone(self, api_key_client, workspace, uploaded_asset):
        with mock.patch("plane.api.views.asset.S3Storage", side_effect=storage_stub):
            response = api_key_client.get(f"/api/v1/workspaces/{workspace.slug}/assets/{uploaded_asset.id}/")

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["asset_url"] == "https://signed.example/get"
