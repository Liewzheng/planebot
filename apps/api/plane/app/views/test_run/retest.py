# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The re-test channel of a batch (STD-SW-003 §6.2 rule 2).

A re-test is a batch of its own: 「补测追加新批次，批次头写「补测：
`RUN-20261109-01`」指向原批次；不往旧批次里回填结论」. The link is the
whole relation — but the *way* the second execution is written down is
where the records went wrong: a batch was created by hand, the cases
were added to it and the evidence of the first execution was pasted in
over it. The two batches then held the same cases with the same
evidence, and nothing said which one came first.

This module is the one way to open a re-test:

* the **case set** of the original batch is copied into the new one — the
  re-test runs *these* cases, and the snapshot of every case is the one
  the original batch holds;
* **nothing of the execution is copied**: no conclusion, no evidence, no
  measured value, no verdict and no file. The re-test writes its own
  record of its own execution;
* the new batch is linked to the original through ``retest_of`` and
  carries **its own number** — the server hands the number out, so the
  record of a re-test can never point at another batch;
* the header of the new batch (the version, the environment, the
  executor) is inherited from the original batch, and the client may
  write its own.

Both surfaces call this module — the app API and the v1 API — so the two
answers cannot drift: the rules of a batch live in one implementation,
never in a copy of it.
"""

# Django imports
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.serializers.test_run import TestRunWriteSerializer
from plane.app.testcase_validity import describe_reasons
from plane.db.models import TestRun
from plane.service_principals.attribution import actor_id_for_request
from plane.utils.testcase_api import (
    copy_run_cases,
    is_uuid,
    object_body,
    retest_authoring_reasons,
    retest_header_defaults,
    serializer_error_response,
    validation_error_response,
)


def _copy_cases_requested(payload) -> bool:
    """True when the client wants the case set of the source batch.

    The default is yes: a batch that names the batch it re-tests *is* a
    re-test, and the case set is what a re-test runs. A client that
    wants an empty batch with the link alone says so
    (``copy_cases: false``).
    """
    value = payload.get("copy_cases")
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "")
    return bool(value)


def _source_of(payload, project, slug):
    """The batch the payload re-tests, or ``None``.

    ``None`` when the payload names no batch, when it names one that is
    not a batch of this project (the plain path refuses such a link with
    the model's own reason — same project, never itself, never a ring),
    or when the client asked for an empty batch.
    """
    raw = payload.get("retest_of")
    if not raw or not is_uuid(raw):
        return None
    if not _copy_cases_requested(payload):
        return None
    return TestRun.objects.filter(
        pk=raw, project_id=project.id, workspace__slug=slug
    ).first()


def _inherited_header(payload, source) -> dict:
    """The header fields the re-test takes over from the original batch.

    What the client wrote wins; what the client left empty is inherited.
    An empty field and a missing field mean the same thing here: the
    web form sends an empty string for a field nobody filled in.
    """
    inherited = retest_header_defaults(source)
    data = {}
    for field, value in inherited.items():
        sent = payload.get(field)
        if sent is None or (isinstance(sent, str) and not sent.strip()):
            data[field] = value
    return data


def create_run(request, project, slug):
    """Create a batch of the project — the re-test channel included.

    Returns ``(run, None)`` when the batch was created and
    ``(None, response)`` when it was refused. The refusal is the
    envelope of this feature, and the batch is not created when the
    answer is a refusal.
    """
    body = object_body(request)
    if isinstance(body, Response):
        # A body that is not a JSON object has no fields to read; the
        # refusal of the shared vocabulary answers it.
        return None, body

    source = _source_of(body, project, slug)
    if source is None:
        return _plain_run(request, project, body)
    return _retest_run(request, project, body, source)


def _plain_run(request, project, payload):
    """Create a batch that names no other batch."""
    serializer = TestRunWriteSerializer(data=payload, context={"request": request})
    if not serializer.is_valid():
        return None, serializer_error_response(serializer)

    try:
        run = serializer.save(project=project, workspace=project.workspace)
    except DjangoValidationError as exc:
        return None, validation_error_response(exc, "The batch could not be saved.")

    return run, None


def _retest_run(request, project, payload, source):
    """Create a batch that re-tests ``source`` and copy its case set.

    The copy and the batch are written in one transaction: a batch that
    exists without its case set is a batch that looks finished and is
    not.
    """
    # The authoring rules run over the snapshot the original batch holds:
    # a case that is not written yet (an expected result that a reader
    # cannot judge, §5.6) must not enter an execution batch — not through
    # the add path and not through the re-test path either.
    reasons = retest_authoring_reasons(source)
    if reasons:
        return None, Response(
            {
                "error": "The test case is not valid.",
                "code": "invalid_test_case",
                "errors": describe_reasons(reasons),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    data = dict(payload)
    # The inherited fields fill in what the client left empty — the
    # client's own values win over the original batch.
    data.update(_inherited_header(payload, source))
    if not data.get("run_date"):
        # The day of the execution is the day the re-test runs on — the
        # number of the batch carries it.
        data["run_date"] = timezone.now().date()

    serializer = TestRunWriteSerializer(data=data, context={"request": request})
    if not serializer.is_valid():
        return None, serializer_error_response(serializer)

    with transaction.atomic():
        try:
            run = serializer.save(project=project, workspace=project.workspace)
        except DjangoValidationError as exc:
            return None, validation_error_response(exc, "The batch could not be saved.")

        copy_run_cases(source, run, actor_id=actor_id_for_request(request))

    return run, None
