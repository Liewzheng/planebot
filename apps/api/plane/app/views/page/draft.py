# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db.models import Q

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.db.models import Page, PageDraft
from plane.app.serializers import PageDraftSerializer, PageDraftUpdateSerializer
from plane.app.permissions import ProjectPagePermission
from ..base import BaseAPIView


class PageDraftEndpoint(BaseAPIView):
    """The requesting user's unpublished revision of a page (PLANE-77).

    A draft is kept beside the page so that nothing else can see it: it never
    touches `description_html`, the collaborative document, the version history
    or the public API, and every query here is scoped to the caller, so one user
    can neither read nor overwrite another user's draft.
    """

    permission_classes = [ProjectPagePermission]

    def _page(self, request, slug, project_id, page_id):
        return Page.objects.get(
            Q(owned_by=request.user) | Q(access=Page.PUBLIC_ACCESS),
            pk=page_id,
            workspace__slug=slug,
            projects__id=project_id,
            project_pages__deleted_at__isnull=True,
        )

    def get(self, request, slug, project_id, page_id):
        page = self._page(request, slug, project_id, page_id)
        draft = PageDraft.objects.filter(page=page, owned_by=request.user).first()
        if not draft:
            # no draft is a normal state, not a missing resource: the client
            # checks `description_html` for null
            return Response({"description_html": None}, status=status.HTTP_200_OK)
        return Response(PageDraftSerializer(draft).data, status=status.HTTP_200_OK)

    def patch(self, request, slug, project_id, page_id):
        page = self._page(request, slug, project_id, page_id)
        serializer = PageDraftUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # a soft-deleted draft does not block writing a new one
        draft, created = PageDraft.objects.get_or_create(
            page=page,
            owned_by=request.user,
            defaults={
                "workspace_id": page.workspace_id,
                "description_html": serializer.validated_data["description_html"],
            },
        )
        if not created:
            draft.description_html = serializer.validated_data["description_html"]
            draft.save(update_fields=["description_html", "updated_at"])

        return Response(PageDraftSerializer(draft).data, status=status.HTTP_200_OK)

    def delete(self, request, slug, project_id, page_id):
        page = self._page(request, slug, project_id, page_id)
        PageDraft.objects.filter(page=page, owned_by=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
