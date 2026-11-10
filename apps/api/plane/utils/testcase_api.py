# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The shared HTTP vocabulary of the test-case feature (STD-SW-003).

The feature has two HTTP surfaces — the app API under ``/api/`` and the
public v1 API under ``/api/v1/`` — and both answer their failures in the
same shape: the ``{"error", "code", "errors": [{code, message, path,
field}]}`` envelope of a 400/409, and the DRF ``{"detail"}`` of a 403.
The helpers that build those bodies therefore live here, in a module
neither surface owns, instead of in one surface's view module.

That placement is what keeps the two surfaces independent at import
time: the v1 views import this module and the app *serializers*, never
an app *view* module. A refactor of the app view layer that moves or
renames one of these helpers would otherwise raise ``ImportError`` while
Django imports the URLconf of the v1 surface — and the api container
would not start at all, not just these endpoints.

Nothing in this module may import ``plane.app.permissions``, the app's
authentication/session layer or a view module of either surface. The
rule vocabulary (:mod:`plane.app.testcase_validity`) is imported: it is
a pure module over the dataclasses of :mod:`plane.db.testcase_validity`
and carries the Chinese sentences the entries of ``errors`` quote.
"""

# Django imports
from django.utils.dateparse import parse_datetime

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.testcase_validity import REASON_MESSAGES_ZH, reason_code_of
from plane.db.models import FileAsset, Project


#: Reason codes the app layer adds on top of the vocabulary of
#: :mod:`plane.db.testcase_validity`. They are endpoint-level problems
#: the rules do not know about.
APP_REASON_MESSAGES_ZH = {
    "conflict": "这条记录在你编辑期间被改过了，请重新载入后再保存。",
    "module_code_taken": "该模块码已在本项目登记。",
    "module_code_format": "模块码必须是 2 至 6 位大写字母。",
    "module_description_missing": "模块说明不能为空。",
    "module_not_in_project": "该模块不属于本项目。",
    "issue_not_in_project": "该工作项不属于本项目。",
    "issue_link_exists": "该工作项已经关联到这个用例。",
    "case_not_in_project": "该用例不属于本项目。",
    "invalid_updated_at": "updated_at 不是合法的时间戳。",
    "duplicate_test_case": "同一个用例不能重复加入批次。",
    "test_case_ids_missing": "请至少选择一个用例。",
    "asset_not_found": "附件不存在。",
    "asset_already_bound": "该附件已经绑定到其它记录。",
    "asset_not_bindable": "该附件是其它用途的文件（封面、标识等），不能绑定到测试记录上。",
    "asset_project_mismatch": "该附件属于其它项目，不能绑定到本项目的记录上。",
    "run_version_fixed": "一个批次只对应一个被测版本，补测请新建批次。",
    "run_date_fixed": "批次的执行日期在创建后不能修改。",
}


def is_uuid(value) -> bool:
    """True when the value looks like a UUID."""
    from uuid import UUID

    try:
        UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def same_date(value, other) -> bool:
    """True when the client sent the day the batch carries.

    The client may send the day as ``2026-11-09`` or as a full
    timestamp; the batch stores the day.
    """
    from datetime import date

    try:
        sent = date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return False
    return sent == other


def object_body(request):
    """The body of the request as a mapping — or the refusal response.

    The writes of this feature read the body directly
    (``request.data.get(...)``). A body that is not a JSON object — a
    list, a string, a number — has no ``get``; the read would raise an
    ``AttributeError`` and the endpoint would answer 500 where the
    contract promises a 400. Such a body is a refused payload, exactly
    as it is on the endpoints that go through a serializer.

    Returns the body (a ``dict``) or a :class:`Response` carrying the
    refusal; the caller answers the response when it gets one.
    """
    data = request.data
    if isinstance(data, dict):
        return data
    return Response(
        {
            "error": "The payload is not valid.",
            "code": "invalid_payload",
            "errors": [
                error_entry(
                    "invalid_payload",
                    "",
                    "The body of the request must be a JSON object.",
                )
            ],
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


def error_entry(code: str, path: str = "", message: str = "") -> dict:
    """One entry of the ``errors`` list of a 400 body.

    The sentence comes from the vocabulary of the rules
    (:data:`REASON_MESSAGES_ZH`) or, for the codes the app layer adds on
    top of it, from :data:`APP_REASON_MESSAGES_ZH`. Both are consulted:
    an entry without a sentence would leave the front end with nothing
    to show next to the offending field.
    """
    return {
        "code": code,
        "message": message
        or REASON_MESSAGES_ZH.get(code)
        or APP_REASON_MESSAGES_ZH.get(code, ""),
        "path": path,
        "field": path,
    }


def serializer_error_response(serializer, error: str = "The payload is not valid.") -> Response:
    """Wrap the refusal of a serializer in the envelope of this feature.

    A field the serializer refuses (an unknown priority, a date that is
    not a date) is answered in the same shape as every other error of
    the feature, so the front end and the CLI never have to parse a
    second shape. The message of such an entry is the **detail of the
    serializer** — it is not a localised sentence; the code and the
    field are what a client keys on.
    """
    errors = []
    raw_errors = serializer.errors
    if isinstance(raw_errors, dict):
        items = raw_errors.items()
    else:
        items = [("", raw_errors)]

    for field, messages in items:
        if not isinstance(messages, (list, tuple)):
            messages = [messages]
        for message in messages:
            errors.append(
                {
                    "code": "invalid_payload",
                    "message": str(message),
                    "path": field,
                    "field": field,
                }
            )

    return Response(
        {"error": error, "code": "invalid_payload", "errors": errors},
        status=status.HTTP_400_BAD_REQUEST,
    )


def validation_error_response(exc, error: str = "The payload is not valid.") -> Response:
    """Map a Django ``ValidationError`` onto the 400 body of this feature.

    The model layer prefixes its messages with the stable reason code
    (``"conclusion_is_final: …"``); the code is what the front end keys
    on, the Chinese sentence is what it shows.
    """
    errors = []
    message_dict = getattr(exc, "message_dict", None)
    if message_dict:
        for field, messages in message_dict.items():
            for message in messages:
                code = reason_code_of(message) or ""
                errors.append(
                    {
                        "code": code,
                        "message": REASON_MESSAGES_ZH.get(code, message),
                        "detail": message,
                        "path": field,
                        "field": field,
                    }
                )
    else:
        for message in getattr(exc, "messages", [str(exc)]):
            code = reason_code_of(message) or ""
            errors.append(
                {
                    "code": code,
                    "message": REASON_MESSAGES_ZH.get(code, message),
                    "detail": message,
                    "path": "",
                    "field": "",
                }
            )
    return Response(
        {"error": error, "code": "invalid_payload", "errors": errors},
        status=status.HTTP_400_BAD_REQUEST,
    )


def conflict_response(current: dict) -> Response:
    """The 409 body of an optimistic-concurrency conflict."""
    return Response(
        {
            "error": "The record was changed by somebody else. Reload it and try again.",
            "code": "conflict",
            "errors": [error_entry("conflict")],
            "current": current,
        },
        status=status.HTTP_409_CONFLICT,
    )


def stale_record_response(request, record, current: dict):
    """The 409 response when the client's ``updated_at`` no longer matches.

    ``None`` when the client sent no ``updated_at`` (the write is then a
    plain last-write-wins) or when the value still matches.
    """
    body = object_body(request)
    if isinstance(body, Response):
        return body

    client_value = body.get("updated_at")
    if not client_value:
        return None
    parsed = parse_datetime(str(client_value))
    if parsed is None:
        return Response(
            {
                "error": "updated_at is not a valid timestamp.",
                "code": "invalid_updated_at",
                "errors": [error_entry("invalid_updated_at", "updated_at")],
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    if parsed != record.updated_at:
        return conflict_response(current)
    return None


def project_or_404(slug, project_id):
    """The project of the URL, or ``None``."""
    return Project.objects.filter(pk=project_id, workspace__slug=slug).first()


def run_conflict_response(message: str, code: str = "run_completed") -> Response:
    """The 409 body of a write that a completed batch refuses."""
    return Response(
        {
            "error": message,
            "code": code,
            "errors": [error_entry(code)],
        },
        status=status.HTTP_409_CONFLICT,
    )


#: The foreign keys of ``FileAsset`` that name the entity a file hangs
#: on. A file that already carries one of them may not get a second
#: owner: the row would then belong to two entities at once, and the
#: unbind of one of them would take the file away from the other.
_ASSET_ENTITY_FIELDS = (
    "issue_id",
    "page_id",
    "comment_id",
    "draft_issue_id",
    "user_id",
    "test_run_case_id",
    "test_run_step_record_id",
)

#: The entity types a file may carry when it is bound to a record of a
#: batch. A file that belongs to a whole entity as such — the cover of a
#: project, the logo of a workspace, an avatar — is not a file of a
#: record: binding it would take it away from that entity, and the
#: unbind would flag the row deleted. Only a file without an entity (or
#: one that was already meant for a batch) may be attached to a record.
_BINDABLE_ENTITY_TYPES = frozenset(
    {
        "",
        None,
        FileAsset.EntityTypeContext.TEST_RUN_STEP_RECORD,
        FileAsset.EntityTypeContext.TEST_RUN_CASE_EVIDENCE,
    }
)


def bindable_asset(request, slug, record):
    """The asset the request wants to bind to ``record``, or the error.

    The asset has to belong to the **project of the record**: a file of
    another project may not be moved into this one — the bind would
    silently re-home it, and the unbind would then soft-delete a row of a
    project the caller has no rights in. A file that already hangs on
    another entity may not get a second owner either, and a file that
    belongs to a whole entity (the cover of a project, the logo of a
    workspace) may not be attached to a record at all.

    Returns the asset, or a :class:`Response` carrying the reason.
    """
    body = object_body(request)
    if isinstance(body, Response):
        return body

    asset_id = body.get("asset_id")
    if not asset_id or not is_uuid(asset_id):
        return Response(
            {
                "error": "asset_id is required.",
                "code": "asset_not_found",
                "errors": [error_entry("asset_not_found", "asset_id")],
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    asset = FileAsset.objects.filter(
        id=asset_id, workspace__slug=slug, is_deleted=False
    ).first()
    if asset is None:
        return Response(
            {
                "error": "The asset does not exist.",
                "code": "asset_not_found",
                "errors": [error_entry("asset_not_found", "asset_id")],
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    if asset.project_id and asset.project_id != record.project_id:
        return Response(
            {
                "error": "The asset belongs to another project.",
                "code": "asset_project_mismatch",
                "errors": [error_entry("asset_project_mismatch", "asset_id")],
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    if any(getattr(asset, field) for field in _ASSET_ENTITY_FIELDS):
        # The file hangs on another entity (a work item, a page, a
        # comment, a record of another batch). The check comes first: the
        # answer names the entity the file already belongs to, not the
        # kind of entity type it carries.
        return Response(
            {
                "error": "The asset is already bound to a record.",
                "code": "asset_already_bound",
                "errors": [error_entry("asset_already_bound", "asset_id")],
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    if asset.entity_type not in _BINDABLE_ENTITY_TYPES:
        # The file belongs to a whole entity — the cover of a project,
        # the logo of a workspace, an avatar. It carries no entity
        # foreign key, so the check above cannot see it.
        return Response(
            {
                "error": "The asset belongs to another entity.",
                "code": "asset_not_bindable",
                "errors": [error_entry("asset_not_bindable", "asset_id")],
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    return asset
