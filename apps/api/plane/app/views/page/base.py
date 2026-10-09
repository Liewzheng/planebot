# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import json
from datetime import datetime
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from django.utils.dateparse import parse_datetime

# Python imports
import logging

# Django imports
from django.conf import settings
from django.db import connection
from django.db.models import (
    Exists,
    OuterRef,
    Q,
    Value,
    UUIDField,
    Count,
    Case,
    When,
    IntegerField,
)
from django.http import StreamingHttpResponse
from django.contrib.postgres.aggregates import ArrayAgg
from django.contrib.postgres.fields import ArrayField
from django.db.models.functions import Coalesce

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import allow_permission, ROLE
from plane.app.serializers import (
    PageSerializer,
    PageDetailSerializer,
    PageBinaryUpdateSerializer,
)
from plane.db.models import (
    Page,
    PageLog,
    UserFavorite,
    ProjectMember,
    PageVersion,
    ProjectPage,
    Project,
    UserRecentVisit,
)
from plane.utils.error_codes import ERROR_CODES
from plane.utils.order_queryset import PAGE_ORDER_BY_ALLOWLIST, sanitize_order_by
from plane.utils.page_content import (
    content_is_unchanged,
    duplicated_block_length,
    invalidate_live_document,
    page_content_fingerprint,
    page_content_text_length,
    sync_page_description_formats,
)

# Local imports
from ..base import BaseAPIView, BaseViewSet
from plane.bgtasks.page_transaction_task import page_transaction
from plane.bgtasks.page_version_task import track_page_version
from plane.bgtasks.recent_visited_task import recent_visited_task
from plane.bgtasks.copy_s3_object import copy_s3_objects_of_description_and_assets
from plane.app.permissions import ProjectPagePermission


logger = logging.getLogger(__name__)


def is_revert_to_earlier_version(page, incoming_html) -> bool:
    """True when `incoming_html` reproduces a revision the page already moved past.

    Compares content fingerprints, so editor serialization differences do not
    count as a revision. Unknown/new content and the current revision are not
    reverts.
    """
    if not incoming_html:
        return False
    incoming = page_content_fingerprint(incoming_html)
    revision_fingerprints = [
        page_content_fingerprint(html)
        for html in PageVersion.objects.filter(page_id=page.id)
        .order_by("created_at")
        .values_list("description_html", flat=True)
    ]
    if not revision_fingerprints:
        return False
    if revision_fingerprints[-1] == incoming:
        # already the newest revision: nothing to revert
        return False
    return incoming in revision_fingerprints[:-1]


def unarchive_archive_page_and_descendants(page_id, archived_at):
    # Your SQL query
    sql = """
    WITH RECURSIVE descendants AS (
        SELECT id FROM pages WHERE id = %s
        UNION ALL
        SELECT pages.id FROM pages, descendants WHERE pages.parent_id = descendants.id
    )
    UPDATE pages SET archived_at = %s WHERE id IN (SELECT id FROM descendants);
    """

    # Execute the SQL query
    with connection.cursor() as cursor:
        cursor.execute(sql, [page_id, archived_at])


class PageViewSet(BaseViewSet):
    serializer_class = PageSerializer
    model = Page
    permission_classes = [ProjectPagePermission]
    search_fields = ["name"]

    def get_queryset(self):
        subquery = UserFavorite.objects.filter(
            user=self.request.user,
            entity_type="page",
            entity_identifier=OuterRef("pk"),
            workspace__slug=self.kwargs.get("slug"),
        )
        return self.filter_queryset(
            super()
            .get_queryset()
            .filter(workspace__slug=self.kwargs.get("slug"))
            .filter(
                projects__project_projectmember__member=self.request.user,
                projects__project_projectmember__is_active=True,
                projects__archived_at__isnull=True,
            )
            .filter(parent__isnull=True)
            .filter(Q(owned_by=self.request.user) | Q(access=0))
            .prefetch_related("projects")
            .select_related("workspace")
            .select_related("owned_by")
            .annotate(is_favorite=Exists(subquery))
            .prefetch_related("labels")
            # Sanitize the user-supplied order_by against an allowlist: Django
            # resolves the field at call time, so an unknown field raises
            # FieldError (500 DoS) and a relation path (e.g. owned_by__password)
            # enables ORM relational traversal. Favourites stay
            # pinned first; the sanitized user ordering is the secondary sort
            # (a single .order_by() so it is not overridden), with id as a
            # stable tiebreak for pagination.
            .order_by(
                "-is_favorite",
                sanitize_order_by(
                    self.request.GET.get("order_by", "-created_at"),
                    PAGE_ORDER_BY_ALLOWLIST,
                    default="-created_at",
                ),
                "id",
            )
            .annotate(
                project=Exists(
                    ProjectPage.objects.filter(page_id=OuterRef("id"), project_id=self.kwargs.get("project_id"))
                )
            )
            .annotate(
                label_ids=Coalesce(
                    ArrayAgg(
                        "page_labels__label_id",
                        distinct=True,
                        filter=~Q(page_labels__label_id__isnull=True),
                    ),
                    Value([], output_field=ArrayField(UUIDField())),
                ),
                project_ids=Coalesce(
                    ArrayAgg("projects__id", distinct=True, filter=~Q(projects__id=True)),
                    Value([], output_field=ArrayField(UUIDField())),
                ),
            )
            .filter(project=True)
            .distinct()
        )

    def create(self, request, slug, project_id):
        serializer = PageSerializer(
            data=request.data,
            context={
                "project_id": project_id,
                "owned_by_id": request.user.id,
                "description_json": request.data.get("description_json", {}),
                "description_binary": request.data.get("description_binary", None),
                "description_html": request.data.get("description_html", "<p></p>"),
            },
        )

        if serializer.is_valid():
            serializer.save()
            # Convert HTML-only writes into the document JSON + Yjs binary so the
            # collaborative editor can load the page.
            sync_page_description_formats(serializer.instance)
            # capture the page transaction
            page_transaction.delay(
                new_description_html=request.data.get("description_html", "<p></p>"),
                old_description_html=None,
                page_id=serializer.data["id"],
            )
            page = self.get_queryset().get(pk=serializer.data["id"])
            serializer = PageDetailSerializer(page)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def partial_update(self, request, slug, project_id, page_id):
        try:
            page = Page.objects.get(
                pk=page_id,
                workspace__slug=slug,
                projects__id=project_id,
                project_pages__deleted_at__isnull=True,
            )

            if page.is_locked:
                return Response({"error": "Page is locked"}, status=status.HTTP_400_BAD_REQUEST)

            parent = request.data.get("parent", None)
            if parent:
                _ = Page.objects.get(
                    pk=parent,
                    workspace__slug=slug,
                    projects__id=project_id,
                    project_pages__deleted_at__isnull=True,
                )

            # Only update access if the page owner is the requesting  user
            if page.access != request.data.get("access", page.access) and page.owned_by_id != request.user.id:
                return Response(
                    {"error": "Access cannot be updated since this page is owned by someone else"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            serializer = PageDetailSerializer(page, data=request.data, partial=True)
            page_description = page.description_html
            if serializer.is_valid():
                # the body moved: keep the content-revision clock in sync so the
                # editor's first-publisher-wins check sees this write. Set it
                # BEFORE the save so it is never later than `updated_at`
                # (auto_now): the editor sends `updated_at` as its publish base
                # and the check compares it against this clock.
                if "description_html" in request.data:
                    page.description_updated_at = timezone.now()
                serializer.save()
                # Backfill the Yjs binary when content was written as HTML only;
                # regenerate it when the HTML changed (this endpoint carries no
                # binary, so a stale one would keep the previous document).
                sync_page_description_formats(page, force="description_html" in request.data)
                # capture the page transaction
                if request.data.get("description_html"):
                    page_transaction.delay(
                        new_description_html=request.data.get("description_html", "<p></p>"),
                        old_description_html=page_description,
                        page_id=page_id,
                    )

                return Response(serializer.data, status=status.HTTP_200_OK)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except Page.DoesNotExist:
            return Response(
                {"error": "Access cannot be updated since this page is owned by someone else"},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def retrieve(self, request, slug, project_id, page_id=None):
        page = self.get_queryset().filter(pk=page_id).first()
        project = Project.objects.get(pk=project_id)
        track_visit = request.query_params.get("track_visit", "true").lower() == "true"

        """
        if the role is guest and guest_view_all_features is false and owned by is not
        the requesting user then dont show the page
        """

        if (
            ProjectMember.objects.filter(
                workspace__slug=slug,
                project_id=project_id,
                member=request.user,
                role=5,
                is_active=True,
            ).exists()
            and not project.guest_view_all_features
            and not page.owned_by == request.user
        ):
            return Response(
                {"error": "You are not allowed to view this page"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if page is None:
            return Response({"error": "Page not found"}, status=status.HTTP_404_NOT_FOUND)
        else:
            issue_ids = PageLog.objects.filter(page_id=page_id, entity_name="issue").values_list(
                "entity_identifier", flat=True
            )
            data = PageDetailSerializer(page).data
            data["issue_ids"] = issue_ids
            if track_visit:
                recent_visited_task.delay(
                    slug=slug,
                    entity_name="page",
                    entity_identifier=page_id,
                    user_id=request.user.id,
                    project_id=project_id,
                )
            return Response(data, status=status.HTTP_200_OK)

    def lock(self, request, slug, project_id, page_id):
        page = Page.objects.get(
            pk=page_id,
            workspace__slug=slug,
            projects__id=project_id,
            project_pages__deleted_at__isnull=True,
        )

        page.is_locked = True
        page.save()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def unlock(self, request, slug, project_id, page_id):
        page = Page.objects.get(
            pk=page_id,
            workspace__slug=slug,
            projects__id=project_id,
            project_pages__deleted_at__isnull=True,
        )

        page.is_locked = False
        page.save()

        return Response(status=status.HTTP_204_NO_CONTENT)

    def access(self, request, slug, project_id, page_id):
        access = request.data.get("access", 0)
        page = Page.objects.get(
            pk=page_id,
            workspace__slug=slug,
            projects__id=project_id,
            project_pages__deleted_at__isnull=True,
        )

        # Only update access if the page owner is the requesting user
        if page.access != request.data.get("access", page.access) and page.owned_by_id != request.user.id:
            return Response(
                {"error": "Access cannot be updated since this page is owned by someone else"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        page.access = access
        page.save()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def list(self, request, slug, project_id):
        queryset = self.get_queryset()
        project = Project.objects.get(pk=project_id)
        if (
            ProjectMember.objects.filter(
                workspace__slug=slug,
                project_id=project_id,
                member=request.user,
                role=5,
                is_active=True,
            ).exists()
            and not project.guest_view_all_features
        ):
            queryset = queryset.filter(owned_by=request.user)
        pages = PageSerializer(queryset, many=True).data
        return Response(pages, status=status.HTTP_200_OK)

    def archive(self, request, slug, project_id, page_id):
        page = Page.objects.get(
            pk=page_id,
            workspace__slug=slug,
            projects__id=project_id,
            project_pages__deleted_at__isnull=True,
        )

        # only the owner or admin can archive the page
        if (
            ProjectMember.objects.filter(
                project_id=project_id, member=request.user, is_active=True, role__lte=15
            ).exists()
            and request.user.id != page.owned_by_id
        ):
            return Response(
                {"error": "Only the owner or admin can archive the page"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        UserFavorite.objects.filter(
            entity_type="page",
            entity_identifier=page_id,
            project_id=project_id,
            workspace__slug=slug,
        ).delete()

        unarchive_archive_page_and_descendants(page_id, datetime.now())

        return Response({"archived_at": str(datetime.now())}, status=status.HTTP_200_OK)

    def unarchive(self, request, slug, project_id, page_id):
        page = Page.objects.get(
            pk=page_id,
            workspace__slug=slug,
            projects__id=project_id,
            project_pages__deleted_at__isnull=True,
        )

        # only the owner or admin can un archive the page
        if (
            ProjectMember.objects.filter(
                project_id=project_id, member=request.user, is_active=True, role__lte=15
            ).exists()
            and request.user.id != page.owned_by_id
        ):
            return Response(
                {"error": "Only the owner or admin can un archive the page"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # if parent archived then page will be un archived breaking hierarchy
        if page.parent_id and page.parent.archived_at:
            page.parent = None
            page.save(update_fields=["parent"])

        unarchive_archive_page_and_descendants(page_id, None)

        return Response(status=status.HTTP_204_NO_CONTENT)

    def destroy(self, request, slug, project_id, page_id):
        page = Page.objects.get(
            pk=page_id,
            workspace__slug=slug,
            projects__id=project_id,
            project_pages__deleted_at__isnull=True,
        )

        if page.archived_at is None:
            return Response(
                {"error": "The page should be archived before deleting"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if page.owned_by_id != request.user.id and (
            not ProjectMember.objects.filter(
                workspace__slug=slug,
                member=request.user,
                role=20,
                project_id=project_id,
                is_active=True,
            ).exists()
        ):
            return Response(
                {"error": "Only admin or owner can delete the page"},
                status=status.HTTP_403_FORBIDDEN,
            )

        # remove parent from all the children
        _ = Page.objects.filter(
            parent_id=page_id,
            projects__id=project_id,
            workspace__slug=slug,
            project_pages__deleted_at__isnull=True,
        ).update(parent=None)

        page.delete()
        # Delete the user favorite page
        UserFavorite.objects.filter(
            project=project_id,
            workspace__slug=slug,
            entity_identifier=page_id,
            entity_type="page",
        ).delete()
        # Delete the page from recent visit
        UserRecentVisit.objects.filter(
            project_id=project_id,
            workspace__slug=slug,
            entity_identifier=page_id,
            entity_name="page",
        ).delete(soft=False)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def summary(self, request, slug, project_id):
        queryset = (
            Page.objects.filter(workspace__slug=slug)
            .filter(
                projects__project_projectmember__member=self.request.user,
                projects__project_projectmember__is_active=True,
                projects__archived_at__isnull=True,
            )
            .filter(parent__isnull=True)
            .filter(Q(owned_by=request.user) | Q(access=0))
            .annotate(
                project=Exists(
                    ProjectPage.objects.filter(page_id=OuterRef("id"), project_id=self.kwargs.get("project_id"))
                )
            )
            .filter(project=True)
            .distinct()
        )

        project = Project.objects.get(pk=project_id)
        if (
            ProjectMember.objects.filter(
                workspace__slug=slug,
                project_id=project_id,
                member=request.user,
                role=ROLE.GUEST.value,
                is_active=True,
            ).exists()
            and not project.guest_view_all_features
        ):
            queryset = queryset.filter(owned_by=request.user)

        stats = queryset.aggregate(
            public_pages=Count(
                Case(
                    When(access=Page.PUBLIC_ACCESS, archived_at__isnull=True, then=1),
                    output_field=IntegerField(),
                )
            ),
            private_pages=Count(
                Case(
                    When(access=Page.PRIVATE_ACCESS, archived_at__isnull=True, then=1),
                    output_field=IntegerField(),
                )
            ),
            archived_pages=Count(Case(When(archived_at__isnull=False, then=1), output_field=IntegerField())),
        )

        return Response(stats, status=status.HTTP_200_OK)


class PageFavoriteViewSet(BaseViewSet):
    model = UserFavorite

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def create(self, request, slug, project_id, page_id):
        _ = UserFavorite.objects.create(
            project_id=project_id,
            entity_identifier=page_id,
            entity_type="page",
            user=request.user,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def destroy(self, request, slug, project_id, page_id):
        page_favorite = UserFavorite.objects.get(
            project=project_id,
            user=request.user,
            workspace__slug=slug,
            entity_identifier=page_id,
            entity_type="page",
        )
        page_favorite.delete(soft=False)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PagesDescriptionViewSet(BaseViewSet):
    permission_classes = [ProjectPagePermission]

    def retrieve(self, request, slug, project_id, page_id):
        page = Page.objects.get(
            Q(owned_by=self.request.user) | Q(access=0),
            pk=page_id,
            workspace__slug=slug,
            projects__id=project_id,
            project_pages__deleted_at__isnull=True,
        )
        binary_data = page.description_binary

        def stream_data():
            if binary_data:
                yield binary_data
            else:
                yield b""

        response = StreamingHttpResponse(stream_data(), content_type="application/octet-stream")
        response["Content-Disposition"] = 'attachment; filename="page_description.bin"'
        return response

    def partial_update(self, request, slug, project_id, page_id):
        page = Page.objects.get(
            Q(owned_by=self.request.user) | Q(access=0),
            pk=page_id,
            workspace__slug=slug,
            projects__id=project_id,
            project_pages__deleted_at__isnull=True,
        )

        if page.is_locked:
            return Response(
                {
                    "error_code": ERROR_CODES["PAGE_LOCKED"],
                    "error_message": "PAGE_LOCKED",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if page.archived_at:
            return Response(
                {
                    "error_code": ERROR_CODES["PAGE_ARCHIVED"],
                    "error_message": "PAGE_ARCHIVED",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Store the old description_html before saving (needed for both tasks)
        old_description_html = page.description_html

        # Serialize the existing instance
        existing_instance = json.dumps({"description_html": old_description_html}, cls=DjangoJSONEncoder)

        # Use serializer for validation and update
        serializer = PageBinaryUpdateSerializer(page, data=request.data, partial=True)
        # The live server identifies itself with a shared secret header. An
        # explicit API write is a deliberate act (including "restore version"),
        # so the revert guard below only applies to the collaborative store.
        is_live_writer = bool(settings.LIVE_INTERNAL_API_KEY) and (
            request.headers.get("x-live-internal-key") == settings.LIVE_INTERNAL_API_KEY
        )
        # The editor writes here when the user presses Save (`save_source`), which
        # is what page content is persisted by: the live server never stores the
        # collaborative document on its own any more. A save is a deliberate act
        # by a signed-in user, and the document it was read from already holds
        # exactly this content — dropping the server's in-memory copy would
        # reload every open editor on every save.
        is_editor_save = request.data.get("save_source") == "editor"
        # Writes that may not resurrect a revision the page already moved past,
        # and that are dropped when they change nothing a reader can see.
        is_content_write = is_live_writer or is_editor_save

        # A client whose local copy was ballooned (a stale copy merged as a yjs
        # union) would persist the page holding its own content twice, and every
        # client would then sync that back. Refused before the serializer runs so
        # the caller gets the code the editor acts on (it keeps the draft and
        # reloads the stored copy) instead of a plain validation error. The body
        # is only refused when it repeats a quarter of itself and the stored page
        # does not: a document that repeats a table row on purpose stays writable.
        ballooned_block = duplicated_block_length(request.data.get("description_html"))
        if is_editor_save and ballooned_block and not duplicated_block_length(page.description_html):
            logger.warning(
                "REFUSED a save that duplicated page %s (repeated block of %s chars); "
                "dropping the live document so clients reload the stored copy",
                page.id,
                ballooned_block,
            )
            invalidate_live_document(str(page.id), page_content_text_length(page.description_html))
            return Response(
                {
                    "error_code": ERROR_CODES.get("PAGE_DUPLICATED", "CONTENT_DUPLICATED"),
                    "error_message": "Refused to save a page whose content was duplicated by a stale copy",
                },
                status=status.HTTP_409_CONFLICT,
            )

        # First publisher wins. An editing session is seeded from the published
        # revision and the author sends that revision's `updated_at` back with
        # the publish: if the page's content moved on while they were editing
        # (someone published, restored a version, re-uploaded), publishing their
        # older base would silently overwrite the newer revision. Refuse, so the
        # draft is kept and the client reloads what won.
        #
        # `page.updated_at` is not the base to compare against: it is an
        # auto_now field, so it also moves when only properties change (a
        # rename, an access change, a logo) — the author's own title edit is
        # even saved to the page row right before publishing, which used to
        # refuse every publish that kept the content and only renamed the page.
        # `description_updated_at` moves exactly when the body is written, so
        # that is what "the revision the author read" is measured against.
        base_updated_at = request.data.get("base_updated_at")
        if is_editor_save and isinstance(base_updated_at, str):
            base_datetime = parse_datetime(base_updated_at)
            if base_datetime is None:
                return Response(
                    {
                        "error_code": "INVALID_BASE_REVISION",
                        "error_message": "base_updated_at is not a valid timestamp",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            content_revision_at = page.description_updated_at or page.created_at
            if content_revision_at and base_datetime < content_revision_at:
                logger.warning(
                    "REFUSED a publish of page %s from a stale revision (base %s, content saved at %s); "
                    "dropping the live document so clients reload what won",
                    page.id,
                    base_updated_at,
                    content_revision_at,
                )
                invalidate_live_document(str(page.id), page_content_text_length(page.description_html))
                return Response(
                    {
                        "error_code": "PAGE_VERSION_CONFLICT",
                        "error_message": "This page was updated while you were editing. "
                        "Reload it and re-apply your changes.",
                        "updated_at": page.updated_at.isoformat(),
                        "updated_by": (page.updated_by.display_name or page.updated_by.email)
                        if page.updated_by
                        else None,
                    },
                    status=status.HTTP_409_CONFLICT,
                )

        if serializer.is_valid():
            # Opening a page is enough to make the collaborative client rewrite
            # the markup it received (node ids are migrated, markup normalized),
            # so a write that changes nothing a reader can see would drift the
            # stored html and name whoever saved it, for a change nobody made.
            # Content identity ignores serialization, so such a write is dropped
            # whole: no row update, no version, no attribution.
            incoming_html = request.data.get("description_html")
            if is_content_write and content_is_unchanged(page.description_html, incoming_html):
                logger.debug("ignoring a write that did not change the content of page %s", page.id)
                return Response({"message": "No content change"}, status=status.HTTP_200_OK)

            # A client holding a stale copy must not push the page back to a
            # state it already had: that silently reverts content written
            # through the API (reproduced three times, PLANE-76). Refuse the
            # write and drop the live document so every client reloads the
            # database copy.
            if is_content_write and is_revert_to_earlier_version(page, incoming_html):
                latest_version = (
                    PageVersion.objects.filter(page_id=page.id)
                    .select_related("owned_by")
                    .order_by("-created_at")
                    .first()
                )
                logger.warning(
                    "REFUSED a write that reverts page %s to an earlier revision; "
                    "dropping the live document so clients reload it",
                    page.id,
                )
                invalidate_live_document(str(page.id), page_content_text_length(page.description_html))
                return Response(
                    {
                        "error_code": ERROR_CODES.get("PAGE_REVERTED", "CONTENT_REVERTED"),
                        "error_message": "Refused to overwrite a newer revision with an older one",
                        # what the caller lost to, so the editor can say who saved
                        # first instead of just reporting a failure
                        "conflict": {
                            "version_id": str(latest_version.id),
                            "saved_by": latest_version.owned_by.display_name or latest_version.owned_by.email,
                            "saved_at": latest_version.created_at.isoformat(),
                        }
                        if latest_version
                        else None,
                    },
                    status=status.HTTP_409_CONFLICT,
                )

            # the body moved: this is the revision later publishes must build
            # on (see the first-publisher-wins check above). Set it BEFORE the
            # save so it is never later than `updated_at` (auto_now): the
            # editor sends `updated_at` as its publish base.
            page.description_updated_at = timezone.now()
            serializer.save()

            # The write replaced the content the live server holds: editing
            # happens in the client's own document now, so the shared one is
            # always the previous revision. Dropping it makes every client -
            # including the one that published - load the stored revision.
            # Either way the stored formats must agree: an html-only write has
            # its binary rebuilt from it (which invalidates the document too).
            if not request.data.get("description_binary"):
                sync_page_description_formats(page, force=True)
            else:
                invalidate_live_document(str(page.id), page_content_text_length(page.description_html))

            # Capture the page transaction
            if incoming_html:
                page_transaction.delay(
                    new_description_html=request.data.get("description_html", "<p></p>"),
                    old_description_html=old_description_html,
                    page_id=page_id,
                )

            # Run background tasks
            track_page_version.delay(
                page_id=page_id,
                existing_instance=existing_instance,
                user_id=request.user.id,
            )
            return Response({"message": "Updated successfully", "updated_at": page.updated_at})
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PageDuplicateEndpoint(BaseAPIView):
    permission_classes = [ProjectPagePermission]

    def post(self, request, slug, project_id, page_id):
        page = Page.objects.get(
            pk=page_id,
            workspace__slug=slug,
            projects__id=project_id,
            project_pages__deleted_at__isnull=True,
        )

        # check for permission
        if page.access == Page.PRIVATE_ACCESS and page.owned_by_id != request.user.id:
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        # get all the project ids where page is present
        project_ids = ProjectPage.objects.filter(page_id=page_id).values_list("project_id", flat=True)

        page.pk = None
        page.name = f"{page.name} (Copy)"
        page.description_binary = None
        page.owned_by = request.user
        page.created_by = request.user
        page.updated_by = request.user
        page.save()

        for project_id in project_ids:
            ProjectPage.objects.create(
                workspace_id=page.workspace_id,
                project_id=project_id,
                page_id=page.id,
                created_by_id=page.created_by_id,
                updated_by_id=page.updated_by_id,
            )

        page_transaction.delay(
            new_description_html=page.description_html,
            old_description_html=None,
            page_id=page.id,
        )

        # Copy the s3 objects uploaded in the page
        copy_s3_objects_of_description_and_assets.delay(
            entity_name="PAGE",
            entity_identifier=page.id,
            project_id=project_id,
            slug=slug,
            user_id=request.user.id,
        )

        page = (
            Page.objects.filter(pk=page.id)
            .annotate(
                project_ids=Coalesce(
                    ArrayAgg("projects__id", distinct=True, filter=~Q(projects__id=True)),
                    Value([], output_field=ArrayField(UUIDField())),
                )
            )
            .first()
        )
        serializer = PageDetailSerializer(page)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
