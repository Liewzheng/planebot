# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import json

# Third party imports
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.db.models import Page, PageVersion
from ..base import BaseAPIView
from plane.app.serializers import PageVersionSerializer, PageVersionDetailSerializer
from plane.app.permissions import ProjectPagePermission
from plane.bgtasks.page_version_task import track_page_version
from plane.utils.error_codes import ERROR_CODES
from plane.utils.page_content import (
    content_is_unchanged,
    invalidate_live_document,
    page_content_text_length,
    sync_page_description_formats,
)


class PageVersionEndpoint(BaseAPIView):
    permission_classes = [ProjectPagePermission]

    def get(self, request, slug, project_id, page_id, pk=None):
        # Check if pk is provided
        if pk:
            # Return a single page version. Scope to an *active* ProjectPage link
            # for the URL project so a page belonging to (or removed from)
            # another project cannot be read via this endpoint (GHSA-g49r /
            # GHSA-ghcr). The active-link partial-unique constraint keeps the
            # join to a single row; distinct() is a defensive guard so the
            # page__project_pages join can never make get() raise
            # MultipleObjectsReturned (a 500).
            page_version = (
                PageVersion.objects.filter(
                    workspace__slug=slug,
                    page__project_pages__project_id=project_id,
                    page__project_pages__deleted_at__isnull=True,
                    page_id=page_id,
                    pk=pk,
                )
                .distinct()
                .get()
            )
            # Serialize the page version
            serializer = PageVersionDetailSerializer(page_version)
            return Response(serializer.data, status=status.HTTP_200_OK)
        # Return all page versions scoped to an active ProjectPage link for the
        # URL project (defense in depth).
        page_versions = PageVersion.objects.filter(
            workspace__slug=slug,
            page__project_pages__project_id=project_id,
            page__project_pages__deleted_at__isnull=True,
            page_id=page_id,
        )
        # Serialize the page versions
        serializer = PageVersionSerializer(page_versions, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class PageVersionRestoreEndpoint(BaseAPIView):
    permission_classes = [ProjectPagePermission]

    def post(self, request, slug, project_id, page_id, pk):
        """Put an earlier revision back on the page.

        Restoring is a deliberate act by a signed-in user, so it is written
        here: the collaborative session never persists anything on its own
        (PLANE-76). The rest of the page's formats are rebuilt from the
        restored html and the in-memory document is dropped, so every client
        picks the restored revision up instead of union-merging the one that
        was replaced.
        """
        page = Page.objects.get(
            Q(owned_by=request.user) | Q(access=Page.PUBLIC_ACCESS),
            pk=page_id,
            workspace__slug=slug,
            projects__id=project_id,
            project_pages__deleted_at__isnull=True,
        )

        if page.is_locked:
            return Response(
                {"error_code": ERROR_CODES["PAGE_LOCKED"], "error_message": "PAGE_LOCKED"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if page.archived_at:
            return Response(
                {"error_code": ERROR_CODES["PAGE_ARCHIVED"], "error_message": "PAGE_ARCHIVED"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        page_version = (
            PageVersion.objects.filter(
                workspace__slug=slug,
                page__project_pages__project_id=project_id,
                page__project_pages__deleted_at__isnull=True,
                page_id=page_id,
                pk=pk,
            )
            .distinct()
            .get()
        )

        old_description_html = page.description_html

        # Restoring the revision the page already holds would rewrite the
        # document binary for nothing — and a rebuilt binary is a new set of Yjs
        # identities, which every client that still holds the old one merges as
        # a second copy of the page (PLANE-76).
        if content_is_unchanged(old_description_html, page_version.description_html):
            return Response({"message": "Page already holds this version"}, status=status.HTTP_200_OK)

        page.description_html = page_version.description_html
        # the restored revision becomes the content baseline later publishes
        # must build on (the first-publisher-wins check)
        page.description_updated_at = timezone.now()
        page.save()

        if not sync_page_description_formats(page, force=True):
            # the live service could not rebuild the document formats: drop the
            # binary so the next load converts the restored html instead of
            # serving the revision that was just replaced
            page.description_binary = None
            page.description_json = {}
            page.save(update_fields=["description_binary", "description_json"])
            invalidate_live_document(str(page.id), page_content_text_length(page.description_html))

        track_page_version.delay(
            page_id=str(page.id),
            existing_instance=json.dumps({"description_html": old_description_html}, cls=DjangoJSONEncoder),
            user_id=request.user.id,
        )

        return Response({"message": "Page version restored"}, status=status.HTTP_200_OK)
