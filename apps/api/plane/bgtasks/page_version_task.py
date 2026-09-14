# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import json
import logging


# Third party imports
from celery import shared_task

# Django imports
from django.utils import timezone

# Module imports
from plane.db.models import Page, PageVersion
from plane.utils.exception_logger import log_exception

logger = logging.getLogger(__name__)

PAGE_VERSION_TASK_TIMEOUT = 600


@shared_task
def track_page_version(page_id, existing_instance, user_id):
    try:
        # Get the page
        page = Page.objects.get(id=page_id)

        # A version must be attributable; fall back to whoever last touched the
        # page when the caller could not provide an actor (e.g. internal calls)
        user_id = user_id or page.updated_by_id or page.owned_by_id
        if not user_id:
            logger.warning("Skipping page version for %s: no user to attribute it to", page_id)
            return

        # Get the current instance
        current_instance = json.loads(existing_instance) if existing_instance is not None else {}
        sub_pages = {}

        # Create a version if description_html is updated
        if current_instance.get("description_html") != page.description_html:
            # Fetch the latest page version
            page_version = PageVersion.objects.filter(page_id=page_id).order_by("-last_saved_at").first()

            # Get the latest page version if it exists and is owned by the user
            if (
                page_version
                and str(page_version.owned_by_id) == str(user_id)
                and (timezone.now() - page_version.last_saved_at).total_seconds() <= PAGE_VERSION_TASK_TIMEOUT
            ):
                page_version.description_html = page.description_html
                page_version.description_binary = page.description_binary
                page_version.description_json = page.description_json
                page_version.description_stripped = page.description_stripped
                page_version.sub_pages_data = sub_pages
                page_version.save(
                    update_fields=[
                        "description_html",
                        "description_binary",
                        "description_json",
                        "description_stripped",
                        "sub_pages_data",
                        "updated_at",
                    ]
                )
            else:
                # Create a new page version
                PageVersion.objects.create(
                    page_id=page_id,
                    workspace_id=page.workspace_id,
                    description_json=page.description_json,
                    description_html=page.description_html,
                    description_binary=page.description_binary,
                    description_stripped=page.description_stripped,
                    owned_by_id=user_id,
                    last_saved_at=timezone.now(),
                    sub_pages_data=sub_pages,
                )
            # If page versions are greater than 20 delete the oldest one
            if PageVersion.objects.filter(page_id=page_id).count() > 20:
                # Delete the old page version
                PageVersion.objects.filter(page_id=page_id).order_by("last_saved_at").first().delete()

        return
    except Page.DoesNotExist:
        return
    except Exception as e:
        log_exception(e)
        return
