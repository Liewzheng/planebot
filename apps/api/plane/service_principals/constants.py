# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Service Principal constants.

This module defines the resource types, actions, and principal-type enum that
back the SP API. Service principals are NOT User rows: they are their own
principal type and authenticate through service tokens (APIToken rows with
``principal_type=SERVICE`` and ``service_principal`` set).
"""

# Token prefix for SP service tokens. The standard user-token prefix is
# ``plane_api_``; service tokens get ``plane_svc_`` so they are visually and
# programmatically distinguishable at the credential layer.
SERVICE_TOKEN_PREFIX = "plane_svc_"

# ``bot_type`` written on the shadow bot User row backing a service principal
# (``ServicePrincipal.bot_user`` — see ``attribution.py``). Deliberately NOT
# added to ``plane.db.BotTypeEnum``: ``User.bot_type`` carries no DB-level
# choice constraint, and keeping ``plane/db`` untouched avoids migration
# conflicts when following upstream. Same rationale as
# ``ai_accounts.constants.BOT_TYPE_AI_AGENT``.
#
# The value is also load-bearing: ``core.authz.visibility`` hides every bot
# from member pickers unless its ``bot_type`` is explicitly allow-listed, so an
# SP bot stays out of member lists by construction.
BOT_TYPE_SERVICE_PRINCIPAL = "SERVICE_PRINCIPAL"


class PrincipalType:
    USER = 0
    SERVICE = 1


PRINCIPAL_TYPE_CHOICES = (
    (PrincipalType.USER, "User"),
    (PrincipalType.SERVICE, "Service"),
)


class ResourceType:
    ALL = "all"
    PROJECT = "project"
    MEMBER = "member"
    USER = "user"
    ASSET = "asset"
    ESTIMATE = "estimate"
    CYCLE = "cycle"
    MODULE = "module"
    STICKY = "sticky"
    LABEL = "label"
    INTAKE = "intake"
    WORK_ITEM = "work_item"
    COMMENT = "comment"
    STATE = "state"
    PAGE = "page"
    INVITE = "invite"


RESOURCE_CHOICES = (
    (ResourceType.ALL, "All"),
    (ResourceType.PROJECT, "Project"),
    (ResourceType.MEMBER, "Member"),
    (ResourceType.USER, "User"),
    (ResourceType.ASSET, "Asset"),
    (ResourceType.ESTIMATE, "Estimate"),
    (ResourceType.CYCLE, "Cycle"),
    (ResourceType.MODULE, "Module"),
    (ResourceType.STICKY, "Sticky"),
    (ResourceType.LABEL, "Label"),
    (ResourceType.INTAKE, "Intake"),
    (ResourceType.WORK_ITEM, "Work Item"),
    (ResourceType.COMMENT, "Comment"),
    (ResourceType.STATE, "State"),
    (ResourceType.PAGE, "Page"),
    (ResourceType.INVITE, "Invite"),
)


class Action:
    ALL = "all"
    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


ACTION_CHOICES = (
    (Action.ALL, "All"),
    (Action.READ, "Read"),
    (Action.CREATE, "Create"),
    (Action.UPDATE, "Update"),
    (Action.DELETE, "Delete"),
)