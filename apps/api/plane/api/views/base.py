# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import zoneinfo
import logging

# Django imports
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import IntegrityError
from django.urls import resolve
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.generics import GenericAPIView

# Module imports
from plane.api.middleware.api_authentication import APIKeyAuthentication
from plane.api.rate_limit import ApiKeyRateThrottle
from plane.core.authz import AuthzContext, Action, enforce
from plane.utils.exception_logger import log_exception
from plane.utils.paginator import BasePaginator
from plane.utils.core.mixins import ReadReplicaControlMixin


logger = logging.getLogger("plane.api")


# Map request methods to the authorize() chain's action vocabulary. We map
# every GET-class request to READ (not LIST) — see the reviewer-m7 finding on
# workspace-level LIST being denied by the Q4 guard (LIST satisfies only via
# a READ scope, so requesting the simpler action is always equivalent for
# SPs and lets workspace-level collection endpoints reach the read-allowed
# path without modifying core authz).
_METHOD_TO_ACTION = {
    "GET": Action.READ,
    "HEAD": Action.READ,
    "OPTIONS": Action.READ,
    "POST": Action.CREATE,
    "PUT": Action.UPDATE,
    "PATCH": Action.UPDATE,
    "DELETE": Action.DELETE,
}


class TimezoneMixin:
    """
    This enables timezone conversion according
    to the user set timezone
    """

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.user.is_authenticated:
            timezone.activate(zoneinfo.ZoneInfo(request.user.user_timezone))
        else:
            timezone.deactivate()


class AIScopeEnforcementMixin:
    """Enforce per-account scope policies for AI (bot) service accounts and
    for Service Principal tokens.

    Three callers feed this hook:

    * **Human users** — pass through untouched. The base DRF permission
      classes already gate workspace/project membership.
    * **AI bots** (``request.user.is_bot=True``) — the historical path:
      ``plane.ai_accounts.policy.enforce_ai_scope`` checks the bot's
      ``AIScopePolicy`` rows against a URL-name keyed resource map. Behavior
      is unchanged from before M8 — vihar's review #2 / mission context
      explicit guarantee.
    * **Service principals** (``request._sp_principal`` stashed by
      :class:`APIKeyAuthentication`) — every SP-authenticated request runs
      :func:`plane.core.authz.authorize`. The view must declare its
      ``resource_type``; an undeclared type denies the request outright
      (default-deny). The view's DRF ``permission_classes`` are skipped via
      :meth:`get_permissions` because those classes reason about
      ``request.user``-as-member, which is the wrong semantic for an SP —
      the SP's grants (not the owner's) own the project membership decision.
    """

    #: Resource type this view targets. Subclasses MUST set this when the
    #: view should accept SP requests. ``None`` (the default) means
    #: SP-authenticated calls are 403'd at ``check_permissions`` time.
    resource_type: str | None = None

    def get_permissions(self):
        # SP requests bypass the view's membership-based permission classes
        # (ProjectMemberPermission, WorkspaceUserPermission, …) — those
        # reason about ``request.user``-as-workspace-member which is the
        # wrong semantic for an SP. The authorize() chain is the only gate.
        if getattr(self.request, "_sp_principal", None) is not None:
            return [IsAuthenticated()]
        return super().get_permissions()

    def check_permissions(self, request):
        # super() runs IsAuthenticated + the view's permission_classes
        # (skipped for SP via get_permissions above).
        super().check_permissions(request)

        sp_principal = getattr(request, "_sp_principal", None)
        if sp_principal is not None:
            self._enforce_sp_permission(request, sp_principal)
            return

        # AI-bot path — unchanged. Called only when the request user is an
        # is_bot User, never for SP requests (the wrapper sets is_bot=False).
        if getattr(request.user, "is_bot", False):
            from plane.ai_accounts.policy import enforce_ai_scope

            enforce_ai_scope(request, self)

    def _enforce_sp_permission(self, request, sp_principal):
        """Run :func:`authorize` for the current SP + (action, resource).

        Unannotated endpoints raise PermissionDenied (default-deny). The
        matrix entry drives role_cap; workspace-level writes are blocked
        inside the engine (Q4 guard).
        """
        resource_type = getattr(self, "resource_type", None)
        if not resource_type:
            raise PermissionDenied(
                "Endpoint is not exposed to service principals."
            )

        action = _METHOD_TO_ACTION.get(request.method)
        if action is None:
            raise PermissionDenied(
                f"Method {request.method} is not allowed for service principals."
            )

        ctx = AuthzContext(
            workspace_slug=getattr(self, "workspace_slug", None),
            project_id=getattr(self, "project_id", None),
        )
        decision = authorize_for_view(
            sp_principal, action, resource_type, ctx
        )
        enforce(decision)


def authorize_for_view(sp_principal, action, resource_type, ctx):
    """Thin wrapper so M8/M9 can share the authorize() invocation shape.

    Local imports keep the base view circular-free (engine.py imports
    WorkspaceMember and ProjectMember models).
    """
    from plane.core.authz import authorize as _authorize

    return _authorize(
        sp_principal,
        action,
        resource_type,
        ctx=ctx,
    )


class BaseAPIView(AIScopeEnforcementMixin, TimezoneMixin, GenericAPIView, ReadReplicaControlMixin, BasePaginator):
    authentication_classes = [APIKeyAuthentication]

    permission_classes = [IsAuthenticated]

    use_read_replica = False

    def filter_queryset(self, queryset):
        for backend in list(self.filter_backends):
            queryset = backend().filter_queryset(self.request, queryset, self)
        return queryset

    def get_throttles(self):
        return [ApiKeyRateThrottle()]

    def handle_exception(self, exc):
        """
        Handle any exception that occurs, by returning an appropriate response,
        or re-raising the error.
        """
        try:
            response = super().handle_exception(exc)
            return response
        except Exception as e:
            if isinstance(e, IntegrityError):
                return Response(
                    {"error": "The payload is not valid"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if isinstance(e, ValidationError):
                return Response(
                    {"error": "Please provide valid detail"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if isinstance(e, ObjectDoesNotExist):
                return Response(
                    {"error": "The requested resource does not exist."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            if isinstance(e, KeyError):
                return Response(
                    {"error": "The required key does not exist"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            log_exception(e)
            return Response(
                {"error": "Something went wrong please try again later"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def dispatch(self, request, *args, **kwargs):
        try:
            response = super().dispatch(request, *args, **kwargs)
            if settings.DEBUG:
                from django.db import connection

                print(f"{request.method} - {request.get_full_path()} of Queries: {len(connection.queries)}")
            return response
        except Exception as exc:
            response = self.handle_exception(exc)
            return response

    def finalize_response(self, request, response, *args, **kwargs):
        # Call super to get the default response
        response = super().finalize_response(request, response, *args, **kwargs)

        # Add custom headers if they exist in the request META
        ratelimit_remaining = request.META.get("X-RateLimit-Remaining")
        if ratelimit_remaining is not None:
            response["X-RateLimit-Remaining"] = ratelimit_remaining

        ratelimit_reset = request.META.get("X-RateLimit-Reset")
        if ratelimit_reset is not None:
            response["X-RateLimit-Reset"] = ratelimit_reset

        return response

    @property
    def workspace_slug(self):
        return self.kwargs.get("slug", None)

    @property
    def project_id(self):
        project_id = self.kwargs.get("project_id", None)
        if project_id:
            return project_id

        if resolve(self.request.path_info).url_name == "project":
            return self.kwargs.get("pk", None)

    @property
    def fields(self):
        fields = [field for field in self.request.GET.get("fields", "").split(",") if field]
        return fields if fields else None

    @property
    def expand(self):
        expand = [expand for expand in self.request.GET.get("expand", "").split(",") if expand]
        return expand if expand else None


class BaseViewSet(AIScopeEnforcementMixin, TimezoneMixin, ReadReplicaControlMixin, ModelViewSet, BasePaginator):
    model = None

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [
        IsAuthenticated,
    ]
    use_read_replica = False

    def get_queryset(self):
        try:
            return self.model.objects.all()
        except Exception as e:
            log_exception(e)
            raise APIException("Please check the view", status.HTTP_400_BAD_REQUEST)

    def handle_exception(self, exc):
        """
        Handle any exception that occurs, by returning an appropriate response,
        or re-raising the error.
        """
        try:
            response = super().handle_exception(exc)
            return response
        except Exception as e:
            if isinstance(e, IntegrityError):
                log_exception(e)
                return Response(
                    {"error": "The payload is not valid"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if isinstance(e, ValidationError):
                logger.warning(
                    "Validation Error",
                    extra={
                        "error_code": "VALIDATION_ERROR",
                        "error_message": str(e),
                    },
                )
                return Response(
                    {"error": "Please provide valid detail"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if isinstance(e, ObjectDoesNotExist):
                logger.warning(
                    "Object Does Not Exist",
                    extra={
                        "error_code": "OBJECT_DOES_NOT_EXIST",
                        "error_message": str(e),
                    },
                )
                return Response(
                    {"error": "The required object does not exist."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            if isinstance(e, KeyError):
                logger.error(
                    "Key Error",
                    extra={
                        "error_code": "KEY_ERROR",
                        "error_message": str(e),
                    },
                )
                return Response(
                    {"error": "The required key does not exist"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            log_exception(e)
            return Response(
                {"error": "Something went wrong please try again later"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def dispatch(self, request, *args, **kwargs):
        try:
            response = super().dispatch(request, *args, **kwargs)

            if settings.DEBUG:
                from django.db import connection

                print(f"{request.method} - {request.get_full_path()} of Queries: {len(connection.queries)}")

            return response
        except Exception as exc:
            response = self.handle_exception(exc)
            return response

    @property
    def workspace_slug(self):
        return self.kwargs.get("slug", None)

    @property
    def project_id(self):
        project_id = self.kwargs.get("project_id", None)
        if project_id:
            return project_id

        if resolve(self.request.path_info).url_name == "project":
            return self.kwargs.get("pk", None)

    @property
    def fields(self):
        fields = [field for field in self.request.GET.get("fields", "").split(",") if field]
        return fields if fields else None

    @property
    def expand(self):
        expand = [expand for expand in self.request.GET.get("expand", "").split(",") if expand]
        return expand if expand else None
