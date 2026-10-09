# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Authorization core.

The single authorization point for every execution surface. Decouples the
"what can this principal do on this resource" question from how the principal
was authenticated and which HTTP shape called the function. The four-step
decision chain (scope → grant → role_cap → owner intersection) lives in
:func:`authorize`; resource/action vocabulary lives in
:mod:`resources` and :mod:`actions`; visibility predicates live in
:mod:`visibility`.

Entry points:

* :func:`authorize` — four-step decision chain, returns a :class:`Decision`.
* :data:`VISIBLE_MEMBER_Q` — unified visible-member predicate, single source
  of truth for every member listing/search/count surface.
* :func:`effective_actions` — compute a principal's effective permissions
  for a given resource so the front end can stop self-computing.
* :func:`visible_principal_qs` — the queryset a view would feed the front end
  after applying :data:`VISIBLE_MEMBER_Q` (and the ``sp_assignable`` switch).
"""

from .actions import (
    ACTION_CHOICES,
    ACTION_REQUIRED_ROLE,
    Action,
    is_registered_action,
    required_role,
)
from .decision import (
    ALLOWED,
    DENY_GRANT_MISS,
    DENY_INACTIVE_OWNER,
    DENY_INACTIVE_PRINCIPAL,
    DENY_NO_PRINCIPAL,
    DENY_OWNER_NOT_WORKSPACE_MEMBER,
    DENY_OWNER_ROLE,
    DENY_ROLE_CAP,
    DENY_SCOPE_MISS,
    DENY_UNREGISTERED,
    DENY_WORKSPACE_LEVEL_WRITE,
    Decision,
)
from .engine import AuthzContext, authorize
from .exceptions import enforce, ensure_allowed
from .permissions import (
    ActionPermission,
    STANDARD_ACTIONS,
    effective_actions,
    sp_effective_actions,
    user_effective_actions,
)
from .principal import Principal, ServicePrincipal_, ServicePrincipalAuthz, UserPrincipal
from .resources import (
    ALLOWED_WORKSPACE_LEVEL_ACTIONS,
    RESOURCE_CHOICES,
    ResourceType,
    WORKSPACE_LEVEL_RESOURCES,
    is_registered,
    is_workspace_level,
)
from .visibility import (
    VISIBLE_MEMBER_Q,
    VisiblePrincipal,
    effective_role_cap,
    get_or_create_workspace_sp_settings,
    is_member_visible,
    is_sp_assignable,
    visible_member_qs,
    visible_principal_qs,
    visible_sp_qs,
)

__all__ = [
    # engine
    "AuthzContext",
    "authorize",
    "enforce",
    "ensure_allowed",
    "Decision",
    "Principal",
    "ServicePrincipal_",
    "ServicePrincipalAuthz",
    "UserPrincipal",
    # reason codes
    "ALLOWED",
    "DENY_GRANT_MISS",
    "DENY_INACTIVE_OWNER",
    "DENY_INACTIVE_PRINCIPAL",
    "DENY_NO_PRINCIPAL",
    "DENY_OWNER_NOT_WORKSPACE_MEMBER",
    "DENY_OWNER_ROLE",
    "DENY_ROLE_CAP",
    "DENY_SCOPE_MISS",
    "DENY_UNREGISTERED",
    "DENY_WORKSPACE_LEVEL_WRITE",
    # resources
    "ResourceType",
    "RESOURCE_CHOICES",
    "WORKSPACE_LEVEL_RESOURCES",
    "ALLOWED_WORKSPACE_LEVEL_ACTIONS",
    "is_registered",
    "is_workspace_level",
    # actions
    "Action",
    "ACTION_CHOICES",
    "ACTION_REQUIRED_ROLE",
    "STANDARD_ACTIONS",
    "is_registered_action",
    "required_role",
    # permissions helpers
    "ActionPermission",
    "effective_actions",
    "sp_effective_actions",
    "user_effective_actions",
    # visibility
    "VISIBLE_MEMBER_Q",
    "VisiblePrincipal",
    "effective_role_cap",
    "get_or_create_workspace_sp_settings",
    "is_member_visible",
    "is_sp_assignable",
    "visible_member_qs",
    "visible_principal_qs",
    "visible_sp_qs",
]