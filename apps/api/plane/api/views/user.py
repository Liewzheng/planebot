# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from rest_framework import status
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiResponse

# Module imports
from plane.api.serializers import UserLiteSerializer
from plane.api.views.base import BaseAPIView
from plane.db.models import User
from plane.utils.openapi.decorators import user_docs
from plane.utils.openapi import USER_EXAMPLE


class UserEndpoint(BaseAPIView):
    """Authenticated-principal self-identity endpoint.

    Human callers (``plane_api_`` token or session) get their own profile via
    :class:`UserLiteSerializer` — byte-for-byte unchanged from prior releases.

    Service-principal callers (``plane_svc_`` token, see
    ``APIKeyAuthentication._finalize_service_token``) get a guarded payload
    that describes the SP itself: ``id``, ``name``, ``type="service_principal"``.
    The owner's profile (name/email/avatar/id/first_name/last_name/display_name)
    is never serialized, so any token whose SP wiring would otherwise expose
    the human owner's row to the holder of the bot token finds nothing to leak
    here. M8 left this endpoint inert to avoid that surface; the
    ``resource_type = "user"`` annotation routes SP requests through the
    authorize() chain and the matrix's existing ``(READ, user) = ROLE_GUEST``
    entry (M7) is the only engine-side ground this relies on.
    """

    resource_type = "user"
    serializer_class = UserLiteSerializer
    model = User

    @user_docs(
        operation_id="get_current_user",
        summary="Get current user",
        description="Retrieve the authenticated user's profile information including basic details.",
        responses={
            200: OpenApiResponse(
                description="Current user profile",
                response=UserLiteSerializer,
                examples=[USER_EXAMPLE],
            ),
        },
    )
    def get(self, request):
        """Get current user

        Retrieve the authenticated user's profile information including basic details.
        Returns user data based on the current authentication context.
        """
        sp_principal = getattr(request, "_sp_principal", None)
        if sp_principal is not None:
            sp = sp_principal.service_principal
            return Response(
                {
                    "id": str(sp.id),
                    "name": sp.name,
                    "type": "service_principal",
                },
                status=status.HTTP_200_OK,
            )
        serializer = UserLiteSerializer(request.user)
        return Response(serializer.data, status=status.HTTP_200_OK)
