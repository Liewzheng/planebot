# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python Imports
import uuid

# Django Imports
from django.utils import timezone
from django.conf import settings

# Third party imports
from rest_framework import status
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiRequest

# Module Imports
from plane.bgtasks.storage_metadata_task import get_asset_object_metadata
from plane.settings.storage import S3Storage
from plane.utils.path_validator import sanitize_filename
from plane.db.models import FileAsset, Page, ProjectPage, User, Workspace
from plane.db.models.test_case import TestRunCase, TestRunStepRecord
from plane.db.testcase_validity import RUN_STATUS_COMPLETED
from plane.app.permissions import WorkspaceUserPermission
from plane.api.views.base import BaseAPIView
from plane.api.serializers import (
    UserAssetUploadSerializer,
    AssetUpdateSerializer,
    GenericAssetUploadSerializer,
    GenericAssetUpdateSerializer,
)
from plane.utils.openapi import (
    ASSET_ID_PARAMETER,
    WORKSPACE_SLUG_PARAMETER,
    PRESIGNED_URL_SUCCESS_RESPONSE,
    GENERIC_ASSET_UPLOAD_SUCCESS_RESPONSE,
    GENERIC_ASSET_VALIDATION_ERROR_RESPONSE,
    ASSET_CONFLICT_RESPONSE,
    ASSET_DOWNLOAD_SUCCESS_RESPONSE,
    ASSET_DOWNLOAD_ERROR_RESPONSE,
    ASSET_UPDATED_RESPONSE,
    ASSET_DELETED_RESPONSE,
    VALIDATION_ERROR_RESPONSE,
    ASSET_NOT_FOUND_RESPONSE,
    NOT_FOUND_RESPONSE,
    UNAUTHORIZED_RESPONSE,
    asset_docs,
)
from plane.utils.exception_logger import log_exception
from plane.service_principals.attribution import actor_for_request


class UserAssetEndpoint(BaseAPIView):
    """This endpoint is used to upload user profile images."""

    resource_type = "asset"

    # The row a PATCH owns: a member-tier asset UPDATE is ownership-scoped
    # (asset DELETE needs ADMIN and is exempt), so the FileAsset row must be
    # supplied rather than left fail-closed.
    authz_resource_model = FileAsset
    authz_resource_lookup = "asset_id"

    def asset_delete(self, asset_id):
        asset = FileAsset.objects.filter(id=asset_id).first()
        if asset is None:
            return
        asset.is_deleted = True
        asset.deleted_at = timezone.now()
        asset.save(update_fields=["is_deleted", "deleted_at"])
        return

    def entity_asset_delete(self, entity_type, asset, request):
        # User Avatar
        if entity_type == FileAsset.EntityTypeContext.USER_AVATAR:
            user = User.objects.get(id=asset.user_id)
            user.avatar_asset_id = None
            user.save()
            return
        # User Cover
        if entity_type == FileAsset.EntityTypeContext.USER_COVER:
            user = User.objects.get(id=asset.user_id)
            user.cover_image_asset_id = None
            user.save()
            return
        return

    @asset_docs(
        operation_id="create_user_asset_upload",
        summary="Generate presigned URL for user asset upload",
        description="Generate presigned URL for user asset upload",
        request=OpenApiRequest(
            request=UserAssetUploadSerializer,
            examples=[
                OpenApiExample(
                    "User Avatar Upload",
                    value={
                        "name": "profile.jpg",
                        "type": "image/jpeg",
                        "size": 1024000,
                        "entity_type": "USER_AVATAR",
                    },
                    description="Example request for uploading a user avatar",
                ),
                OpenApiExample(
                    "User Cover Upload",
                    value={
                        "name": "cover.jpg",
                        "type": "image/jpeg",
                        "size": 1024000,
                        "entity_type": "USER_COVER",
                    },
                    description="Example request for uploading a user cover",
                ),
            ],
        ),
        responses={
            200: PRESIGNED_URL_SUCCESS_RESPONSE,
            400: VALIDATION_ERROR_RESPONSE,
            401: UNAUTHORIZED_RESPONSE,
        },
    )
    def post(self, request):
        """Generate presigned URL for user asset upload.

        Create a presigned URL for uploading user profile assets (avatar or cover image).
        This endpoint generates the necessary credentials for direct S3 upload.
        """
        # get the asset key
        name = sanitize_filename(request.data.get("name")) or "unnamed"
        type = request.data.get("type", "image/jpeg")
        size = int(request.data.get("size", settings.FILE_SIZE_LIMIT))
        entity_type = request.data.get("entity_type", False)

        # Check if the file size is within the limit
        size_limit = min(size, settings.FILE_SIZE_LIMIT)

        #  Check if the entity type is allowed
        if not entity_type or entity_type not in ["USER_AVATAR", "USER_COVER"]:
            return Response(
                {"error": "Invalid entity type.", "status": False},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check if the file type is allowed
        allowed_types = [
            "image/jpeg",
            "image/png",
            "image/webp",
            "image/jpg",
            "image/gif",
        ]
        if type not in allowed_types:
            return Response(
                {
                    "error": "Invalid file type. Only JPEG and PNG files are allowed.",
                    "status": False,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # asset key
        asset_key = f"{uuid.uuid4().hex}-{name}"

        # Create a File Asset
        asset = FileAsset.objects.create(
            attributes={"name": name, "type": type, "size": size_limit},
            asset=asset_key,
            size=size_limit,
            user=request.user,
            created_by=actor_for_request(request),
            entity_type=entity_type,
        )

        # Get the presigned URL
        storage = S3Storage(request=request)
        # Generate a presigned URL to share an S3 object
        presigned_url = storage.generate_presigned_post(object_name=asset_key, file_type=type, file_size=size_limit)
        # Return the presigned URL
        return Response(
            {
                "upload_data": presigned_url,
                "asset_id": str(asset.id),
                "asset_url": asset.asset_url,
            },
            status=status.HTTP_200_OK,
        )

    @asset_docs(
        operation_id="update_user_asset",
        summary="Mark user asset as uploaded",
        description="Mark user asset as uploaded",
        parameters=[ASSET_ID_PARAMETER],
        request=OpenApiRequest(
            request=AssetUpdateSerializer,
            examples=[
                OpenApiExample(
                    "Update Asset Attributes",
                    value={
                        "attributes": {
                            "name": "updated_profile.jpg",
                            "type": "image/jpeg",
                            "size": 1024000,
                        },
                        "entity_type": "USER_AVATAR",
                    },
                    description="Example request for updating asset attributes",
                ),
            ],
        ),
        responses={
            204: ASSET_UPDATED_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    )
    def patch(self, request, asset_id):
        """Update user asset after upload completion.

        Update the asset status and attributes after the file has been uploaded to S3.
        This endpoint should be called after completing the S3 upload to mark the asset as uploaded.
        """
        # get the asset id
        asset = FileAsset.objects.get(id=asset_id, user_id=request.user.id)
        # get the storage metadata
        asset.is_uploaded = True
        # get the storage metadata
        if not asset.storage_metadata:
            get_asset_object_metadata.delay(asset_id=str(asset_id))
        # update the attributes
        asset.attributes = request.data.get("attributes", asset.attributes)
        # save the asset
        asset.save(update_fields=["is_uploaded", "attributes"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @asset_docs(
        operation_id="delete_user_asset",
        summary="Delete user asset",
        parameters=[ASSET_ID_PARAMETER],
        responses={
            204: ASSET_DELETED_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    )
    def delete(self, request, asset_id):
        """Delete user asset.

        Delete a user profile asset (avatar or cover image) and remove its reference from the user profile.
        This performs a soft delete by marking the asset as deleted and updating the user's profile.
        """
        asset = FileAsset.objects.get(id=asset_id, user_id=request.user.id)
        asset.is_deleted = True
        asset.deleted_at = timezone.now()
        # get the entity and save the asset id for the request field
        self.entity_asset_delete(entity_type=asset.entity_type, asset=asset, request=request)
        asset.save(update_fields=["is_deleted", "deleted_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserServerAssetEndpoint(BaseAPIView):
    """This endpoint is used to upload user profile images."""

    resource_type = "asset"

    authz_resource_model = FileAsset
    authz_resource_lookup = "asset_id"

    def asset_delete(self, asset_id):
        asset = FileAsset.objects.filter(id=asset_id).first()
        if asset is None:
            return
        asset.is_deleted = True
        asset.deleted_at = timezone.now()
        asset.save(update_fields=["is_deleted", "deleted_at"])
        return

    def entity_asset_delete(self, entity_type, asset, request):
        # User Avatar
        if entity_type == FileAsset.EntityTypeContext.USER_AVATAR:
            user = User.objects.get(id=asset.user_id)
            user.avatar_asset_id = None
            user.save()
            return
        # User Cover
        if entity_type == FileAsset.EntityTypeContext.USER_COVER:
            user = User.objects.get(id=asset.user_id)
            user.cover_image_asset_id = None
            user.save()
            return
        return

    @asset_docs(
        operation_id="create_user_server_asset_upload",
        summary="Generate presigned URL for user server asset upload",
        request=UserAssetUploadSerializer,
        responses={
            200: PRESIGNED_URL_SUCCESS_RESPONSE,
            400: VALIDATION_ERROR_RESPONSE,
        },
    )
    def post(self, request):
        """Generate presigned URL for user server asset upload.

        Create a presigned URL for uploading user profile assets
        (avatar or cover image) using server credentials. This endpoint generates the
        necessary credentials for direct S3 upload with server-side authentication.
        """
        # get the asset key
        name = sanitize_filename(request.data.get("name")) or "unnamed"
        type = request.data.get("type", "image/jpeg")
        size = int(request.data.get("size", settings.FILE_SIZE_LIMIT))
        entity_type = request.data.get("entity_type", False)

        # Check if the file size is within the limit
        size_limit = min(size, settings.FILE_SIZE_LIMIT)

        #  Check if the entity type is allowed
        if not entity_type or entity_type not in ["USER_AVATAR", "USER_COVER"]:
            return Response(
                {"error": "Invalid entity type.", "status": False},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check if the file type is allowed
        allowed_types = [
            "image/jpeg",
            "image/png",
            "image/webp",
            "image/jpg",
            "image/gif",
        ]
        if type not in allowed_types:
            return Response(
                {
                    "error": "Invalid file type. Only JPEG and PNG files are allowed.",
                    "status": False,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # asset key
        asset_key = f"{uuid.uuid4().hex}-{name}"

        # Create a File Asset
        asset = FileAsset.objects.create(
            attributes={"name": name, "type": type, "size": size_limit},
            asset=asset_key,
            size=size_limit,
            user=request.user,
            created_by=actor_for_request(request),
            entity_type=entity_type,
        )

        # Get the presigned URL
        storage = S3Storage(request=request)
        # Generate a presigned URL to share an S3 object
        presigned_url = storage.generate_presigned_post(object_name=asset_key, file_type=type, file_size=size_limit)
        # Return the presigned URL
        return Response(
            {
                "upload_data": presigned_url,
                "asset_id": str(asset.id),
                "asset_url": asset.asset_url,
            },
            status=status.HTTP_200_OK,
        )

    @asset_docs(
        operation_id="update_user_server_asset",
        summary="Mark user server asset as uploaded",
        parameters=[ASSET_ID_PARAMETER],
        request=AssetUpdateSerializer,
        responses={
            204: ASSET_UPDATED_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    )
    def patch(self, request, asset_id):
        """Update user server asset after upload completion.

        Update the asset status and attributes after the file has been uploaded to S3 using server credentials.
        This endpoint should be called after completing the S3 upload to mark the asset as uploaded.
        """
        # get the asset id
        asset = FileAsset.objects.get(id=asset_id, user_id=request.user.id)
        # get the storage metadata
        asset.is_uploaded = True
        # get the storage metadata
        if not asset.storage_metadata:
            get_asset_object_metadata.delay(asset_id=str(asset_id))
        # update the attributes
        asset.attributes = request.data.get("attributes", asset.attributes)
        # save the asset
        asset.save(update_fields=["is_uploaded", "attributes"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @asset_docs(
        operation_id="delete_user_server_asset",
        summary="Delete user server asset",
        parameters=[ASSET_ID_PARAMETER],
        responses={
            204: ASSET_DELETED_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    )
    def delete(self, request, asset_id):
        """Delete user server asset.

        Delete a user profile asset (avatar or cover image) using server credentials and
        remove its reference from the user profile. This performs a soft delete by marking the
        asset as deleted and updating the user's profile.
        """
        asset = FileAsset.objects.get(id=asset_id, user_id=request.user.id)
        asset.is_deleted = True
        asset.deleted_at = timezone.now()
        # get the entity and save the asset id for the request field
        self.entity_asset_delete(entity_type=asset.entity_type, asset=asset, request=request)
        asset.save(update_fields=["is_deleted", "deleted_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class GenericAssetEndpoint(BaseAPIView):
    """This endpoint is used to upload generic assets that can be later bound to entities."""

    resource_type = "asset"

    # The entity types a file may carry when it belongs to a record of a
    # batch: the measured value of one step or the evidence of one case.
    # A file that belongs to a whole entity as such (the cover of a
    # project, the logo of a workspace, an avatar) is not a file of a
    # record — binding it would take it away from that entity.
    TEST_RUN_ENTITY_TYPES = (
        FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD,
        FileAsset.EntityTypeContext.TEST_RUN_CASE_EVIDENCE,
    )

    # A member-tier asset PATCH is ownership-scoped, so the row is needed.
    authz_resource_model = FileAsset
    authz_resource_lookup = "asset_id"

    # The workspace is taken straight from the URL slug, so every method must
    # verify the caller is an active member of that workspace. Without this the
    # endpoint is a cross-workspace IDOR (the public-API sibling of the
    # CVE-2026-46558 dashboard fix).
    permission_classes = [WorkspaceUserPermission]

    use_read_replica = True

    # Cached project id resolved by ``get_authz_project_id``. Set on the first
    # call so the engine's ``AuthzContext.project_id`` engages the
    # ProjectGrant step (M55 + r1): the URL never carries a project_id, so
    # the body (POST) or the loaded row (GET) is the only source for it.
    # Returning ``None`` here is "no project to grant on" — the engine's
    # workspace-scoped branch covers that case (USER_AVATAR etc.).
    _AUTHZ_PROJECT_ID_UNSET = object()

    def _test_run_record(self, entity_type, entity_identifier):
        """The record of a batch a file of this entity type hangs on.

        ``None`` for every other entity type — those are not bound to a
        record of a batch — and for an identifier that names no record.
        """
        if entity_type == FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD:
            return (
                TestRunStepRecord.objects.filter(pk=entity_identifier)
                .select_related("test_run_case__test_run")
                .first()
            )
        if entity_type == FileAsset.EntityTypeContext.TEST_RUN_CASE_EVIDENCE:
            return (
                TestRunCase.objects.filter(pk=entity_identifier)
                .select_related("test_run")
                .first()
            )
        return None

    def _test_run_status(self, record):
        """The status of the batch a record of a batch belongs to."""
        if record is None:
            return None
        if isinstance(record, TestRunStepRecord):
            return record.test_run_case.test_run.status
        return record.test_run.status

    def _resolve_post_body_project_id(self, request):
        """Project id the posted asset should be bound to, or ``None``.

        Mirrors the resolution the view body performs: when
        ``entity_type=PAGE_DESCRIPTION`` is set, the page reference wins
        and the project is derived from the page's ``ProjectPage`` row
        scoped to the URL's workspace (the body's ``project_id`` is only
        checked for consistency in the view body). The two entity types
        of a batch work the same way: the record decides the project of
        the file. For the plain attachment path (no entity_type) the
        body's ``project_id`` is the only source. Anything else —
        workspace-level pages, unknown entity types, missing or malformed
        identifiers — leaves the asset project-less, so the engine treats
        the request as workspace-scoped.

        Returns ``None`` (not a string) when nothing resolves so the
        caller can tell "no project" from "project id could not be
        parsed". The authz hook returns ``None`` in that case so the
        engine's existing workspace-scoped branch fires.
        """
        entity_type = request.data.get("entity_type")
        if entity_type == FileAsset.EntityTypeContext.PAGE_DESCRIPTION:
            entity_identifier = request.data.get("entity_identifier")
            if not entity_identifier:
                return None
            try:
                page_id = uuid.UUID(str(entity_identifier))
            except (ValueError, TypeError, AttributeError):
                return None
            # Workspace filter keeps the cross-workspace IDOR closed even
            # if the engine's cross-workspace consistency check later
            # loosens (defense in depth). The view body's page lookup
            # also filters by workspace, so the two resolve identically.
            resolved = (
                ProjectPage.objects.filter(
                    page_id=page_id,
                    workspace__slug=self.kwargs.get("slug"),
                    deleted_at__isnull=True,
                )
                .values_list("project_id", flat=True)
                .first()
            )
            return str(resolved) if resolved else None

        if entity_type in self.TEST_RUN_ENTITY_TYPES:
            entity_identifier = request.data.get("entity_identifier")
            if not entity_identifier:
                # A file of a batch attachment that carries no record yet
                # (the bind endpoint attaches it later): the project comes
                # from the body, like on the plain attachment path.
                project_id = request.data.get("project_id")
                return str(project_id) if project_id else None
            record = self._test_run_record(entity_type, entity_identifier)
            if record is None:
                return None
            return str(record.project_id) if record.project_id else None

        # Plain attachment path — no entity binding, only a body project_id.
        project_id = request.data.get("project_id")
        return str(project_id) if project_id else None

    def _resolve_get_url_project_id(self, request):
        """Project id the targeted FileAsset row belongs to, or ``None``.

        Resolved from the URL's ``asset_id`` so the engine's ProjectGrant
        step fires on the actual target project — closing the r1 reviewer
        finding that an SP with a workspace-wide asset scope (and zero
        ProjectGrants) could fetch any project-bound FileAsset by id and
        receive a presigned download URL while the confirm PATCH on the
        same row 403'd.

        Workspace-less assets (``project_id IS NULL``, e.g. ``USER_AVATAR``)
        return ``None`` deliberately: that is the workspace-scoped branch
        the legitimate ``asset:read`` scope row covers, and the existing
        ``test_sp_with_workspace_wide_scope_can_read_a_workspace_level_asset``
        test pins it. The view body also scopes the lookup to the URL's
        workspace (``FileAsset.objects.get(..., workspace_id=workspace.id,
        is_deleted=False)``), so a probe against another workspace's asset
        id resolves to ``None`` and the engine's cross-workspace check
        denies the SP first.
        """
        asset_id = self.kwargs.get("asset_id")
        if not asset_id:
            return None
        try:
            uuid.UUID(str(asset_id))
        except (ValueError, TypeError, AttributeError):
            return None
        resolved = (
            FileAsset.objects.filter(
                id=asset_id,
                workspace__slug=self.kwargs.get("slug"),
                is_deleted=False,
            )
            .values_list("project_id", flat=True)
            .first()
        )
        return str(resolved) if resolved else None

    def get_authz_project_id(self, request):
        """Project id the engine evaluates the ProjectGrant step against.

        The URL never carries a ``project_id`` on this endpoint, so the
        hook has to source it from somewhere:

        * **POST** — body (page reference or plain ``project_id``). See
          :meth:`_resolve_post_body_project_id`.
        * **GET** — the loaded FileAsset row's ``project_id`` (workspaces
          the URL slug first). See :meth:`_resolve_get_url_project_id`.
          Without this the engine skips the grant step and an SP with a
          workspace-wide asset scope could fetch any project-bound asset
          by id (M55 r1 finding).
        * **PATCH / DELETE** — fall through to ``self.project_id``
          (``None``). The mixin's ``get_authz_resource`` already loads the
          row for UPDATE/DELETE, and the engine's
          :func:`_resolve_project_id` reads ``project_id`` straight from
          that loaded row — pre-M55 behaviour preserved byte-identically
          so the confirm PATCH's ``DENY_GRANT_MISS`` / 204 path is
          unchanged.
        """
        if request.method == "POST":
            sentinel = self._AUTHZ_PROJECT_ID_UNSET
            cached = getattr(self, "_authz_resolved_project_id", sentinel)
            if cached is sentinel:
                cached = self._resolve_post_body_project_id(request)
                self._authz_resolved_project_id = cached
            return cached
        if request.method == "GET":
            sentinel = self._AUTHZ_PROJECT_ID_UNSET
            cached = getattr(self, "_authz_get_project_id", sentinel)
            if cached is sentinel:
                cached = self._resolve_get_url_project_id(request)
                self._authz_get_project_id = cached
            return cached
        # PATCH / DELETE — see docstring. The engine reads project_id from
        # the loaded FileAsset row via ``authz_resource_model``; passing
        # ``None`` here keeps the pre-M55 chain identical (P2-1).
        return getattr(self, "project_id", None)

    @asset_docs(
        operation_id="get_generic_asset",
        summary="Get presigned URL for asset download",
        description="Get presigned URL for asset download",
        parameters=[WORKSPACE_SLUG_PARAMETER],
        responses={
            200: ASSET_DOWNLOAD_SUCCESS_RESPONSE,
            400: ASSET_DOWNLOAD_ERROR_RESPONSE,
            404: ASSET_NOT_FOUND_RESPONSE,
        },
    )
    def get(self, request, slug, asset_id):
        """Get presigned URL for asset download.

        Generate a presigned URL for downloading a generic asset.
        The asset must be uploaded and associated with the specified workspace.
        """
        try:
            # Get the workspace
            workspace = Workspace.objects.get(slug=slug)

            # Get the asset
            asset = FileAsset.objects.get(id=asset_id, workspace_id=workspace.id, is_deleted=False)

            # Check if the asset exists and is uploaded
            if not asset.is_uploaded:
                return Response(
                    {"error": "Asset not yet uploaded"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Generate presigned URL for GET.
            # Force attachment disposition for script-capable MIME types (e.g. SVG)
            # to prevent same-origin XSS when the asset URL shares the app's origin
            # (default MinIO self-hosted setup).
            storage = S3Storage(request=request)
            asset_mime_type = (asset.attributes.get("type") or "").split(";")[0].strip().lower()
            disposition = (
                "attachment" if asset_mime_type in settings.SCRIPT_CAPABLE_MIME_TYPES else "inline"
            )
            presigned_url = storage.generate_presigned_url(
                object_name=asset.asset.name,
                filename=asset.attributes.get("name"),
                disposition=disposition,
            )

            return Response(
                {
                    "asset_id": str(asset.id),
                    "asset_url": presigned_url,
                    "asset_name": asset.attributes.get("name", ""),
                    "asset_type": asset.attributes.get("type", ""),
                },
                status=status.HTTP_200_OK,
            )

        except Workspace.DoesNotExist:
            return Response({"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND)
        except FileAsset.DoesNotExist:
            return Response({"error": "Asset not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            log_exception(e)
            return Response(
                {"error": "Internal server error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @asset_docs(
        operation_id="create_generic_asset_upload",
        summary="Generate presigned URL for generic asset upload",
        description="Generate presigned URL for generic asset upload",
        parameters=[WORKSPACE_SLUG_PARAMETER],
        request=OpenApiRequest(
            request=GenericAssetUploadSerializer,
            examples=[
                OpenApiExample(
                    "GenericAssetUploadSerializer",
                    value={
                        "name": "image.jpg",
                        "type": "image/jpeg",
                        "size": 1024000,
                        "project_id": "123e4567-e89b-12d3-a456-426614174000",
                        "external_id": "1234567890",
                        "external_source": "github",
                    },
                    description="Example request for uploading a generic asset",
                ),
            ],
        ),
        responses={
            200: GENERIC_ASSET_UPLOAD_SUCCESS_RESPONSE,
            400: GENERIC_ASSET_VALIDATION_ERROR_RESPONSE,
            404: NOT_FOUND_RESPONSE,
            409: ASSET_CONFLICT_RESPONSE,
        },
    )
    def post(self, request, slug):
        """Generate presigned URL for generic asset upload.

        Create a presigned URL for uploading generic assets that can be bound to
        entities like work items — or, with ``entity_type``/``entity_identifier``,
        to a page, so an API-token client can upload a page's inline images
        without a browser session. Supports various file types and includes
        external source tracking for integrations.
        """
        name = sanitize_filename(request.data.get("name"))
        type = request.data.get("type")
        size = int(request.data.get("size", settings.FILE_SIZE_LIMIT))
        project_id = request.data.get("project_id")
        external_id = request.data.get("external_id")
        external_source = request.data.get("external_source")
        entity_type = request.data.get("entity_type")
        entity_identifier = request.data.get("entity_identifier")

        # Check if the request is valid
        if not name or not size:
            return Response(
                {"error": "Name and size are required fields.", "status": False},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check if the file size is within the limit
        size_limit = min(size, settings.FILE_SIZE_LIMIT)

        # Page images carry the allow-list the web editor enforces (the assets
        # v2 project endpoint); every other caller keeps the broader list.
        is_page_image = entity_type == FileAsset.EntityTypeContext.PAGE_DESCRIPTION
        allowed_types = (
            ["image/jpeg", "image/png", "image/webp", "image/jpg", "image/gif"]
            if is_page_image
            else list(settings.ATTACHMENT_MIME_TYPES)
        )

        # Check if the file type is allowed
        if not type or type not in allowed_types:
            return Response(
                {"error": "Invalid file type.", "status": False},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get the workspace
        workspace = Workspace.objects.get(slug=slug)

        # Optional entity binding — PAGE_DESCRIPTION and the two entity
        # types of a batch are open for now.
        binding = {}
        if entity_type:
            if entity_type == FileAsset.EntityTypeContext.PAGE_DESCRIPTION:
                try:
                    page_id = uuid.UUID(str(entity_identifier))
                except (ValueError, TypeError, AttributeError):
                    return Response(
                        {"error": "entity_identifier must be the id of an existing page."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                page = Page.objects.filter(id=page_id, workspace_id=workspace.id, deleted_at__isnull=True).first()
                if not page:
                    return Response(
                        {"error": "Page not found in this workspace."},
                        status=status.HTTP_404_NOT_FOUND,
                    )
                # A project page pins the asset to that project: the web editor
                # resolves a page image through the project-scoped v2 URL. A
                # workspace-level page has no project, so project_id must stay empty.
                project_page = (
                    ProjectPage.objects.filter(page_id=page.id, workspace_id=workspace.id, deleted_at__isnull=True)
                    .only("project_id")
                    .first()
                )
                if project_page:
                    if project_id and str(project_id) != str(project_page.project_id):
                        return Response(
                            {"error": "project_id does not match the project this page belongs to."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    project_id = project_page.project_id
                elif project_id:
                    return Response(
                        {"error": "project_id must be omitted for a workspace-level page."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                binding = {"page": page}
            elif entity_type in self.TEST_RUN_ENTITY_TYPES:
                # The files of a batch hang on a record of that batch: the
                # measured value of one step or the evidence of one case.
                # The record decides the project of the file (a file
                # without a project is readable by every member of the
                # workspace), and a completed batch is frozen — the
                # verdict of a finished batch may not change any more.
                if not entity_identifier:
                    # No record: the file is of the kind of a batch
                    # attachment but carries no foreign key yet — the
                    # bind endpoint attaches it to a record later. The
                    # project comes from the body.
                    if not project_id:
                        return Response(
                            {"error": "project_id is required for an unbound batch attachment."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                else:
                    record = self._test_run_record(entity_type, entity_identifier)
                    if record is None or record.workspace_id != workspace.id:
                        return Response(
                            {"error": "The requested record could not be found."},
                            status=status.HTTP_404_NOT_FOUND,
                        )
                    if self._test_run_status(record) == RUN_STATUS_COMPLETED:
                        return Response(
                            {"error": "A completed batch is frozen."},
                            status=status.HTTP_409_CONFLICT,
                        )
                    if project_id and str(project_id) != str(record.project_id):
                        return Response(
                            {"error": "project_id does not match the project this record belongs to."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    project_id = record.project_id
                    binding = (
                        {"test_run_step_record": record}
                        if entity_type == FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD
                        else {"test_run_case": record}
                    )
            else:
                return Response(
                    {"error": f"Unsupported entity type '{entity_type}'."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # asset key
        asset_key = f"{workspace.id}/{uuid.uuid4().hex}-{name}"

        # Check for existing asset with same external details if provided
        if external_id and external_source:
            existing_asset = FileAsset.objects.filter(
                workspace__slug=slug,
                external_source=external_source,
                external_id=external_id,
                is_deleted=False,
            ).first()

            if existing_asset:
                return Response(
                    {
                        "message": "Asset with same external id and source already exists",
                        "asset_id": str(existing_asset.id),
                        "asset_url": existing_asset.asset_url,
                    },
                    status=status.HTTP_409_CONFLICT,
                )

        # Create a File Asset. Without an entity_type the caller is a plain
        # attachment producer, which this endpoint has always bound to issues.
        asset = FileAsset.objects.create(
            attributes={"name": name, "type": type, "size": size_limit},
            asset=asset_key,
            size=size_limit,
            workspace_id=workspace.id,
            project_id=project_id,
            created_by=actor_for_request(request),
            external_id=external_id,
            external_source=external_source,
            entity_type=entity_type or FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
            **binding,
        )

        # Get the presigned URL
        storage = S3Storage(request=request)
        presigned_url = storage.generate_presigned_post(object_name=asset_key, file_type=type, file_size=size_limit)

        return Response(
            {
                "upload_data": presigned_url,
                "asset_id": str(asset.id),
                "asset_url": asset.asset_url,
            },
            status=status.HTTP_200_OK,
        )

    @asset_docs(
        operation_id="update_generic_asset",
        summary="Update generic asset after upload completion",
        description="Update generic asset after upload completion",
        parameters=[WORKSPACE_SLUG_PARAMETER, ASSET_ID_PARAMETER],
        request=OpenApiRequest(
            request=GenericAssetUpdateSerializer,
            examples=[
                OpenApiExample(
                    "GenericAssetUpdateSerializer",
                    value={"is_uploaded": True},
                    description="Example request for updating a generic asset",
                )
            ],
        ),
        responses={
            204: ASSET_UPDATED_RESPONSE,
            404: ASSET_NOT_FOUND_RESPONSE,
        },
    )
    def patch(self, request, slug, asset_id):
        """Update generic asset after upload completion.

        Update the asset status after the file has been uploaded to S3.
        This endpoint should be called after completing the S3 upload to mark the asset as uploaded
        and trigger metadata extraction.
        """
        try:
            asset = FileAsset.objects.get(id=asset_id, workspace__slug=slug, is_deleted=False)

            # Update is_uploaded status
            asset.is_uploaded = request.data.get("is_uploaded", asset.is_uploaded)

            # Update storage metadata if not present
            if not asset.storage_metadata:
                get_asset_object_metadata.delay(asset_id=str(asset_id))

            asset.save(update_fields=["is_uploaded"])

            return Response(status=status.HTTP_204_NO_CONTENT)
        except FileAsset.DoesNotExist:
            return Response({"error": "Asset not found"}, status=status.HTTP_404_NOT_FOUND)
