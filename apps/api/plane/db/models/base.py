# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import uuid

# Django imports
from django.db import models

# Third party imports
from crum import get_current_user

# Module imports
from plane.service_principals.attribution import current_sp_actor

from ..mixins import AuditModel


class BaseModel(AuditModel):
    id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True, primary_key=True)

    class Meta:
        abstract = True

    def save(self, *args, created_by_id=None, disable_auto_set_user=False, **kwargs):
        if not disable_auto_set_user:
            # Check if created_by_id is provided
            if created_by_id:
                self.created_by_id = created_by_id
            else:
                user = get_current_user()

                if user is None or user.is_anonymous:
                    self.created_by = None
                    self.updated_by = None
                else:
                    # A service-principal request is credited to the SP's
                    # shadow bot user, never to the owner the service token is
                    # filed under — "the bot wrote this", not "the admin who
                    # owns the bot wrote this". ``current_sp_actor`` returns
                    # None for every human request, where it costs a
                    # thread-local read and a couple of attribute checks.
                    user = current_sp_actor() or user

                    # Check if the model is being created or updated
                    if self._state.adding:
                        # If creating, set created_by and leave updated_by as None
                        self.created_by = user
                        self.updated_by = None
                    else:
                        # If updating, set updated_by only
                        self.updated_by = user

        super(BaseModel, self).save(*args, **kwargs)

    def __str__(self):
        return str(self.id)
