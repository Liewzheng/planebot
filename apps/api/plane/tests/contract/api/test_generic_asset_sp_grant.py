# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests: SP grant enforcement on the v1 asset create / read path.

M55 — the v1 asset create URL (``POST /api/v1/workspaces/<slug>/assets/``)
carries no ``project_id`` in the path, so the engine's
``AuthzContext.project_id`` was empty and the ProjectGrant step was
silently skipped. An SP holding only a workspace-wide ``asset`` scope
could create ``FileAsset`` rows bound to any project in the workspace,
and the engine only caught up at the confirm PATCH (``asset_id`` in the
URL lets the engine read ``FileAsset.project_id``).

M55 r1 — the same family hole was open on the read path:
``GET /api/v1/workspaces/<slug>/assets/<asset_id>/`` also carries no
``project_id``, and the mixin loads no row for READ, so an SP with a
workspace-wide ``asset`` scope (and zero ProjectGrants) could fetch any
project-bound FileAsset by id and receive a working presigned download
URL — while the confirm PATCH on the same row still 403'd. The fix
extends the same hook so GET resolves the project from the URL's
``asset_id`` and the existing grant step fires.

M55 r2 reviewer finding — the project-scoped-scope shape was absent
from this file. The project-access UI toggle actually writes a
``ServiceScope`` row with ``project=<id>`` (not workspace-wide), so the
``workspace-wide-only`` shapes covered by r1 were a strict subset of
the production bot shape. Without this case the suite did not prove
the branch fixed the live MacDock SP's case: on base that SP got 403
``DENY_SCOPE_MISS`` (the under-permission the r2 reviewer surfaced);
on tip it reads 200. Added as the project-scoped-scope shape below.

M55 r2 reviewer finding — the storage-not-called assertion was
unreachable. The view constructs ``S3Storage(request=request)`` with a
keyword, so iterating ``storage_cls.call_args_list[i].args`` is always
empty, and on the fixed tree the deny paths 403 before the handler so
the loop never iterates at all. Fixed by asserting on
``storage_cls.return_value`` AFTER the ``with`` block exits — see
``_assert_no_presign_calls``.

Fix: ``GenericAssetEndpoint.get_authz_project_id`` resolves the target
project for each verb from the only place it lives — POST from the
request body (page reference or plain ``project_id``); GET from the
loaded FileAsset row's ``project_id`` (workspace-scoped to the URL slug
so a foreign workspace's asset id resolves to ``None``); PATCH/DELETE
fall through to ``None`` because the engine already reads
``project_id`` from the loaded ``FileAsset`` row via
``authz_resource_model`` for UPDATE/DELETE. The grant step then engages
on the actual target project in every case.

These tests pin:

* the regressions that motivated M55 (deny on create + no row created +
  ``generate_presigned_post`` never called) and M55 r1 (deny on read +
  no presign issued),
* the live MacDock SP shape — project-scoped scope row + grant (the
  r2 reviewer added bonus finding),
* the still-correct allow cases (granted SP can register, confirm, and
  read),
* the no-regression on the confirm PATCH (the ungranted SP gets the
  same M27 ``DENY_GRANT_MISS`` wording the revoker relied on),
* the no-regression on the human (session) upload path,
* the legitimate workspace-level read branch (USER_AVATAR asset still
  readable by an SP with a workspace-wide ``asset:read`` scope),
* the parallel bug on the plain-attachment create path (no entity
  type, posted ``project_id`` only) — the same silent grant-skip
  applied there and the fix covers it.
"""

from unittest import mock

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import FileAsset, Page, Project, ProjectMember, ProjectPage
from plane.db.models.api import APIToken
from plane.service_principals.constants import PrincipalType
from plane.service_principals.models import (
    ProjectGrant,
    ServicePrincipal,
    ServiceScope,
)


ASSETS_URL = "/api/v1/workspaces/{slug}/assets/"
ASSET_DETAIL_URL = "/api/v1/workspaces/{slug}/assets/{asset_id}/"


# Rendered by AIScopeEnforcementMixin when the view has no ``resource_type``
# annotation. The M55 wiring keeps every v1 view annotated, so a regression
# that drops the annotation cannot satisfy any of the deny tests below.
DEFAULT_DENY_TEXT = "Endpoint is not exposed to service principals."


def _assert_chain_deny(response, expected_phrase):
    """Assert the 403 came from ``authorize()`` at a specific step rather
    than from the unannotated default-deny.

    The substring anchors survive wording tweaks because each step in
    :mod:`plane.core.authz.engine` carries a unique phrase.
    """
    body = response.json()
    assert response.status_code == status.HTTP_403_FORBIDDEN, (
        f"Expected 403, got {response.status_code} body={body!r}"
    )
    assert "detail" in body, f"403 body missing 'detail': {body!r}"
    detail = body["detail"]
    assert expected_phrase in detail, (
        f"Expected chain phrase '{expected_phrase}' in detail "
        f"(the request reached authorize() and a specific step denied), "
        f"got: {detail!r}"
    )
    assert DEFAULT_DENY_TEXT not in detail, (
        f"Detail '{detail!r}' matches the unannotated default-deny; "
        "this request never reached the four-step chain."
    )


def _make_sp(workspace, owner, name="bot-asset", token_value=None, is_active=True):
    sp = ServicePrincipal.objects.create(
        workspace=workspace,
        owner=owner,
        name=name,
        is_active=is_active,
    )
    APIToken.objects.create(
        label=f"svc:{name}",
        token=token_value or f"plane_svc_token_{name}",
        user=owner,
        principal_type=PrincipalType.SERVICE,
        service_principal=sp,
        workspace=workspace,
    )
    return sp


def _grant(sp, project, role_cap=15, is_active=True):
    return ProjectGrant.objects.create(
        service_principal=sp,
        project=project,
        role_cap=role_cap,
        is_active=is_active,
    )


def storage_stub(request=None):
    """Stand-in for ``S3Storage`` so the presign call doesn't round-trip."""
    storage = mock.MagicMock()
    storage.generate_presigned_post.return_value = {"url": "https://signed.example/upload", "fields": {}}
    storage.generate_presigned_url.return_value = "https://signed.example/get"
    return storage


def _patch_storage_allow():
    """Patch ``S3Storage`` with a single shared MagicMock so the
    caller can assert on it after the ``with`` block exits.

    The view constructs ``S3Storage(request=request)`` with a keyword
    argument, so iterating ``storage_cls.call_args_list[i].args``
    is always empty — every assertion has to go through
    ``storage_cls.return_value``.

    The assertion MUST run after the ``with`` block exits: putting
    ``assert_not_called()`` inside an active mock.patch puts the
    MagicMock into a failing traceback frame, which sends
    pytest 9.0.3's traceback renderer into unbounded
    ``findsource`` / ``inspect.getmodule`` recursion — the r2
    reviewer OOM'd a container at 42 GB before restructuring.
    """
    patcher = mock.patch("plane.api.views.asset.S3Storage")
    storage_cls = patcher.start()
    storage_cls.return_value.generate_presigned_post.return_value = {
        "url": "https://signed.example/upload",
        "fields": {},
    }
    storage_cls.return_value.generate_presigned_url.return_value = (
        "https://signed.example/get"
    )
    return patcher, storage_cls


def _assert_no_presign_calls(storage_cls):
    """Assert no presign was issued against the patched S3Storage.

    The deny paths 403 in ``check_permissions`` before the handler
    runs, so the call counts are zero. On the unfixed tree the
    create-path handler would have called ``generate_presigned_post``
    once and issued the upload URL — this assertion is what makes
    the regression test "live" (it would fail on the unfixed tree
    with a non-zero call count).
    """
    storage_cls.return_value.generate_presigned_post.assert_not_called()
    storage_cls.return_value.generate_presigned_url.assert_not_called()


def post_asset(client, slug, payload):
    return client.post(
        ASSETS_URL.format(slug=slug), payload, format="json"
    )


@pytest.fixture
def spv1_client():
    return APIClient()


@pytest.fixture
def target_project(db, workspace, create_user):
    """A project the SP writes against — the page belongs to it."""
    proj = Project.objects.create(
        name="Target Project",
        identifier="TP",
        workspace=workspace,
        created_by=create_user,
        network=2,
    )
    ProjectMember.objects.create(
        project=proj, member=create_user, role=20, is_active=True
    )
    return proj


@pytest.fixture
def target_page(db, workspace, target_project, create_user):
    """A page bound to ``target_project`` — the entity_identifier the
    PAGE_DESCRIPTION binding posts."""
    page = Page.objects.create(
        name="Target Page",
        description_html="<p>body</p>",
        workspace=workspace,
        owned_by=create_user,
        created_by=create_user,
    )
    ProjectPage.objects.create(
        project=target_project,
        workspace=workspace,
        page=page,
        created_by=create_user,
        updated_by=create_user,
    )
    return page


@pytest.fixture
def project_bound_asset(db, workspace, target_project, create_user):
    """An uploaded FileAsset bound to ``target_project`` — the row
    the GET probes against. Module-level so multiple test classes
    (TestSPGenericAssetReadEnforcesGrant,
    TestSPProjectScopedScopeShape) can share it."""
    return FileAsset.objects.create(
        attributes={"name": "doc.pdf", "type": "application/pdf", "size": 2048},
        asset=f"{workspace.id}/doc.pdf",
        size=2048,
        workspace=workspace,
        project=target_project,
        created_by=create_user,
        entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
        is_uploaded=True,
        storage_metadata={"size": 2048},
    )


@pytest.mark.contract
@pytest.mark.django_db
class TestSPGenericAssetCreateEnforcesGrant:
    """The v1 asset create path must engage the ProjectGrant step.

    Before M55: an SP holding only a workspace-wide ``asset:all`` scope
    could POST a page-bound asset for any project in the workspace and
    get a 200 with a FileAsset row bound to that project. The engine's
    grant step was skipped because the create URL carries no
    ``project_id``.

    After M55: ``GenericAssetEndpoint.get_authz_project_id`` resolves
    the target project from the request body before the engine runs,
    so the grant step denies the request (DENY_GRANT_MISS) and no row
    is created.
    """

    def test_ungranted_sp_page_image_create_denies_with_grant_miss(
        self, workspace, create_user, target_project, target_page, spv1_client
    ):
        """The M55 regression scenario: SP with a workspace-wide
        ``asset:all`` scope and ZERO ProjectGrants on the target project
        posts a page-bound asset. The request must be denied at the
        grant step, and no FileAsset row must be created and no object
        must be written to storage."""
        sp = _make_sp(workspace, create_user, name="asset-ungranted")
        # Workspace-wide (all/all) scope, exactly as the converted
        # production bots are shaped — the same shape that, pre-M55,
        # landed the FileAsset row in the target project.
        ServiceScope.objects.create(
            service_principal=sp,
            project=None,
            resource_type="all",
            action="all",
        )

        before_count = FileAsset.objects.filter(workspace=workspace).count()

        spv1_client.credentials(HTTP_X_API_KEY="plane_svc_token_asset-ungranted")

        patcher, storage_cls = _patch_storage_allow()
        try:
            response = post_asset(
                spv1_client,
                workspace.slug,
                {
                    "name": "diagram.png",
                    "type": "image/png",
                    "size": 2048,
                    "entity_type": "PAGE_DESCRIPTION",
                    "entity_identifier": str(target_page.id),
                },
            )
        finally:
            patcher.stop()

        # Storage assertion runs FIRST so it is the line that fails on
        # the unfixed tree (where the handler does call
        # generate_presigned_post once before the engine denies later
        # in a different code path). On the fixed tree this is 0 and
        # the test proceeds; on the unfixed tree the assertion is the
        # one that catches the live defect.
        _assert_no_presign_calls(storage_cls)

        assert response.status_code == status.HTTP_403_FORBIDDEN, (
            f"Expected 403 from authorize(), got {response.status_code} "
            f"body={response.json()!r}"
        )
        # Pin the chain phrase so the assertion cannot be satisfied by
        # a regression to the unannotated default-deny.
        _assert_chain_deny(
            response,
            "has no active grant for project",
        )
        # No FileAsset row was created at any point during the request.
        assert (
            FileAsset.objects.filter(workspace=workspace).count() == before_count
        ), (
            "An ungranted SP POST must not leave a FileAsset row "
            "behind even when the engine later denies — that was M55's "
            "exact bug."
        )
        # And no row was bound to the target project.
        assert FileAsset.objects.filter(project=target_project).count() == 0

    def test_granted_sp_page_image_create_allows(
        self, workspace, create_user, target_project, target_page, spv1_client
    ):
        """Positive control: SP with the (all/all) scope AND an active
        ProjectGrant at the matrix floor (MEMBER=15) can register a
        page-bound asset in the granted project."""
        sp = _make_sp(workspace, create_user, name="asset-granted")
        ServiceScope.objects.create(
            service_principal=sp,
            project=None,
            resource_type="all",
            action="all",
        )
        _grant(sp, target_project, role_cap=15)

        spv1_client.credentials(HTTP_X_API_KEY="plane_svc_token_asset-granted")

        with mock.patch("plane.api.views.asset.S3Storage", side_effect=storage_stub):
            response = post_asset(
                spv1_client,
                workspace.slug,
                {
                    "name": "diagram.png",
                    "type": "image/png",
                    "size": 2048,
                    "entity_type": "PAGE_DESCRIPTION",
                    "entity_identifier": str(target_page.id),
                },
            )

        assert response.status_code == status.HTTP_200_OK, (
            f"Granted SP create should succeed; got "
            f"{response.status_code} body={response.json()!r}"
        )
        asset = FileAsset.objects.get(id=response.data["asset_id"])
        assert asset.project_id == target_project.id
        assert asset.page_id == target_page.id
        assert asset.entity_type == FileAsset.EntityTypeContext.PAGE_DESCRIPTION

    def test_ungranted_sp_plain_attachment_create_denies(
        self, workspace, create_user, target_project, spv1_client
    ):
        """Same bug class on the plain-attachment path: no
        ``entity_type`` in the body, the project is taken from a posted
        ``project_id``. The hook must resolve it the same way and the
        engine must deny at the grant step."""
        sp = _make_sp(workspace, create_user, name="attach-ungranted")
        ServiceScope.objects.create(
            service_principal=sp,
            project=None,
            resource_type="all",
            action="all",
        )

        spv1_client.credentials(HTTP_X_API_KEY="plane_svc_token_attach-ungranted")

        before_count = FileAsset.objects.filter(workspace=workspace).count()
        patcher, storage_cls = _patch_storage_allow()
        try:
            response = post_asset(
                spv1_client,
                workspace.slug,
                {
                    "name": "doc.pdf",
                    "type": "application/pdf",
                    "size": 1024,
                    "project_id": str(target_project.id),
                },
            )
        finally:
            patcher.stop()

        # See note on the page-image test above — storage assertion
        # runs first so it catches the unfixed tree on the presign call
        # (1) rather than the 200 status.
        _assert_no_presign_calls(storage_cls)

        _assert_chain_deny(
            response,
            "has no active grant for project",
        )
        assert (
            FileAsset.objects.filter(workspace=workspace).count() == before_count
        ), "Plain-attachment create must not leave a FileAsset row."

    def test_granted_sp_plain_attachment_create_allows(
        self, workspace, create_user, target_project, spv1_client
    ):
        """Positive control for the plain-attachment path: granted SP
        at the CREATE floor (MEMBER=15) creates an attachment bound
        to the granted project."""
        sp = _make_sp(workspace, create_user, name="attach-granted")
        ServiceScope.objects.create(
            service_principal=sp,
            project=None,
            resource_type="all",
            action="all",
        )
        _grant(sp, target_project, role_cap=15)

        spv1_client.credentials(HTTP_X_API_KEY="plane_svc_token_attach-granted")

        with mock.patch("plane.api.views.asset.S3Storage", side_effect=storage_stub):
            response = post_asset(
                spv1_client,
                workspace.slug,
                {
                    "name": "doc.pdf",
                    "type": "application/pdf",
                    "size": 1024,
                    "project_id": str(target_project.id),
                },
            )

        assert response.status_code == status.HTTP_200_OK, response.data
        asset = FileAsset.objects.get(id=response.data["asset_id"])
        assert asset.project_id == target_project.id


@pytest.mark.contract
@pytest.mark.django_db
class TestSPGenericAssetConfirmEnforcementUnchanged:
    """The confirm PATCH behaviour must not drift.

    M55 only changed the create path. The confirm PATCH already engaged
    the grant step (the URL carries ``asset_id``, so the engine reads
    ``FileAsset.project_id``). Both branches must keep their pre-M55
    semantics byte-identical so a workspace admin revoking a grant
    still gets the same M27 ``DENY_GRANT_MISS`` 403 on the confirm.
    """

    def test_granted_sp_can_confirm_a_page_image(
        self, workspace, create_user, target_project, target_page, spv1_client
    ):
        """Granted SP can confirm a FileAsset they registered: the
        confirm PATCH must 204."""
        sp = _make_sp(workspace, create_user, name="confirm-granted")
        ServiceScope.objects.create(
            service_principal=sp,
            project=None,
            resource_type="all",
            action="all",
        )
        _grant(sp, target_project, role_cap=15)

        spv1_client.credentials(HTTP_X_API_KEY="plane_svc_token_confirm-granted")

        # First, register through the (now grant-enforcing) create path.
        with mock.patch("plane.api.views.asset.S3Storage", side_effect=storage_stub):
            register = post_asset(
                spv1_client,
                workspace.slug,
                {
                    "name": "diagram.png",
                    "type": "image/png",
                    "size": 2048,
                    "entity_type": "PAGE_DESCRIPTION",
                    "entity_identifier": str(target_page.id),
                },
            )
        assert register.status_code == status.HTTP_200_OK, register.data
        asset_id = register.data["asset_id"]

        # Now confirm. Pre-populate storage_metadata so the handler
        # doesn't enqueue the metadata Celery task during the test.
        FileAsset.objects.filter(pk=asset_id).update(
            storage_metadata={"size": 2048},
        )

        response = spv1_client.patch(
            ASSET_DETAIL_URL.format(slug=workspace.slug, asset_id=asset_id),
            {"is_uploaded": True},
            format="json",
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT, (
            f"Granted SP confirm should 204; got {response.status_code} "
            f"body={response.content!r}"
        )

    def test_ungranted_sp_confirm_denies_with_grant_miss(
        self, workspace, create_user, target_project, target_page, spv1_client
    ):
        """The M27 contract on the confirm PATCH must stay unchanged:
        once a grant is revoked, the next PATCH on the same asset must
        still 403 with the ``has no active grant for project`` wording
        so the human revoker can use the same remediation path."""
        # First: register the asset through the (granted) create path,
        # then revoke the grant, then attempt the confirm.
        sp = _make_sp(workspace, create_user, name="confirm-then-revoke")
        ServiceScope.objects.create(
            service_principal=sp,
            project=None,
            resource_type="all",
            action="all",
        )
        grant = _grant(sp, target_project, role_cap=15)

        spv1_client.credentials(HTTP_X_API_KEY="plane_svc_token_confirm-then-revoke")

        with mock.patch("plane.api.views.asset.S3Storage", side_effect=storage_stub):
            register = post_asset(
                spv1_client,
                workspace.slug,
                {
                    "name": "diagram.png",
                    "type": "image/png",
                    "size": 2048,
                    "entity_type": "PAGE_DESCRIPTION",
                    "entity_identifier": str(target_page.id),
                },
            )
        assert register.status_code == status.HTTP_200_OK, register.data
        asset_id = register.data["asset_id"]

        # Revoke the grant — same shape a workspace admin uses to
        # remove a bot's access from a project.
        grant.delete()

        response = spv1_client.patch(
            ASSET_DETAIL_URL.format(slug=workspace.slug, asset_id=asset_id),
            {"is_uploaded": True},
            format="json",
        )

        _assert_chain_deny(
            response,
            "has no active grant for project",
        )

    def test_human_session_upload_path_unaffected(
        self, workspace, create_user, target_project, target_page
    ):
        """The non-SP code path must continue to work byte-for-byte:
        the human owner's API token (the regular ``plane_api_`` token,
        not ``plane_svc_``) still registers a page image with 200 and
        a confirm PATCH that 204s.

        Guards against regressions where the M55 fix accidentally
        disabled the membership-based permission classes for human
        callers — the SP branch only engages when
        ``request._sp_principal`` is set (APIKeyAuthentication), and
        a regular ``plane_api_`` user token never sets it.
        """
        api_client = APIClient()
        # Re-use the api_key_client fixture pattern (conftest.py): a
        # plain ``plane_api_`` token with no ``principal_type`` set,
        # i.e. the legacy user-token path.
        api_token = APIToken.objects.create(
            user=create_user,
            label="human-token",
            token="plane_api_human-token",
        )
        api_client.credentials(HTTP_X_API_KEY=api_token.token)

        with mock.patch("plane.api.views.asset.S3Storage", side_effect=storage_stub):
            register = api_client.post(
                ASSETS_URL.format(slug=workspace.slug),
                {
                    "name": "diagram.png",
                    "type": "image/png",
                    "size": 2048,
                    "entity_type": "PAGE_DESCRIPTION",
                    "entity_identifier": str(target_page.id),
                },
                format="json",
            )

        assert register.status_code == status.HTTP_200_OK, register.data
        asset_id = register.data["asset_id"]
        FileAsset.objects.filter(pk=asset_id).update(storage_metadata={"size": 2048})

        confirm = api_client.patch(
            ASSET_DETAIL_URL.format(slug=workspace.slug, asset_id=asset_id),
            {"is_uploaded": True},
            format="json",
        )
        assert confirm.status_code == status.HTTP_204_NO_CONTENT, confirm.content


@pytest.mark.contract
@pytest.mark.django_db
class TestSPGenericAssetReadEnforcesGrant:
    """The v1 asset read path must engage the ProjectGrant step.

    M55 r1 finding: ``GET /api/v1/workspaces/<slug>/assets/<asset_id>/``
    has no ``project_id`` either, and the mixin loads no row for READ
    (``base.py:196-201`` — the row is fetched only for UPDATE/DELETE),
    so ``ctx.project_id`` was ``None`` and the engine's grant step was
    skipped exactly as it was on the create path. Measured at M55 r1
    review: an SP with a workspace-wide ``asset:all`` scope and zero
    ProjectGrants could fetch any project-bound FileAsset by id and
    receive a working presigned download URL — while the confirm PATCH
    on the same row still 403'd.

    Fix: ``GenericAssetEndpoint.get_authz_project_id`` resolves the
    project from the URL's ``asset_id`` for GET. The grant step now
    fires on the actual target project.
    """

    def test_ungranted_sp_get_on_project_bound_asset_denies_with_grant_miss(
        self, workspace, create_user, target_project, project_bound_asset, spv1_client
    ):
        """The M55 r1 regression: SP with a workspace-wide
        ``asset:all`` scope and ZERO ProjectGrants on the target
        project fetches the asset's presigned URL. Must be denied at
        the grant step, and ``generate_presigned_url`` must never be
        called.

        The reviewer measured this same shape on the base branch and
        got a 200 + a working presigned URL; this test is red on base
        and green on tip."""
        sp = _make_sp(workspace, create_user, name="get-ungranted")
        ServiceScope.objects.create(
            service_principal=sp,
            project=None,
            resource_type="all",
            action="all",
        )

        spv1_client.credentials(HTTP_X_API_KEY="plane_svc_token_get-ungranted")

        patcher, storage_cls = _patch_storage_allow()
        try:
            response = spv1_client.get(
                ASSET_DETAIL_URL.format(
                    slug=workspace.slug, asset_id=project_bound_asset.id
                )
            )
        finally:
            patcher.stop()

        # See note on the create test above — storage assertion runs
        # first so it catches the unfixed tree on the presign call.
        _assert_no_presign_calls(storage_cls)

        _assert_chain_deny(
            response,
            "has no active grant for project",
        )
        # The asset row is untouched (the request never reaches the view
        # body, so even ``is_uploaded`` could not be flipped).
        project_bound_asset.refresh_from_db()
        assert project_bound_asset.is_uploaded is True, (
            "An ungranted SP GET must not mutate the asset row."
        )

    def test_ungranted_sp_get_with_asset_read_scope_denies(
        self, workspace, create_user, target_project, project_bound_asset, spv1_client
    ):
        """Same as above, scoped narrowly to ``asset:read``. The
        workspace-wide read scope must satisfy the scope step, so the
        request reaches the grant step — and the grant step is what
        denies. Same expectation."""
        sp = _make_sp(workspace, create_user, name="get-read-only")
        ServiceScope.objects.create(
            service_principal=sp,
            project=None,
            resource_type="asset",
            action="read",
        )

        spv1_client.credentials(HTTP_X_API_KEY="plane_svc_token_get-read-only")

        patcher, storage_cls = _patch_storage_allow()
        try:
            response = spv1_client.get(
                ASSET_DETAIL_URL.format(
                    slug=workspace.slug, asset_id=project_bound_asset.id
                )
            )
        finally:
            patcher.stop()

        _assert_no_presign_calls(storage_cls)

        _assert_chain_deny(
            response,
            "has no active grant for project",
        )

    def test_granted_sp_get_on_project_bound_asset_allows(
        self, workspace, create_user, target_project, project_bound_asset, spv1_client
    ):
        """Positive control: granted SP at the READ floor (GUEST=5) can
        fetch a project-bound asset (200) and receive a presigned
        download URL."""
        sp = _make_sp(workspace, create_user, name="get-granted")
        ServiceScope.objects.create(
            service_principal=sp,
            project=None,
            resource_type="all",
            action="all",
        )
        _grant(sp, target_project, role_cap=5)

        spv1_client.credentials(HTTP_X_API_KEY="plane_svc_token_get-granted")

        with mock.patch("plane.api.views.asset.S3Storage", side_effect=storage_stub):
            response = spv1_client.get(
                ASSET_DETAIL_URL.format(
                    slug=workspace.slug, asset_id=project_bound_asset.id
                )
            )

        assert response.status_code == status.HTTP_200_OK, (
            f"Granted SP GET should 200; got {response.status_code} "
            f"body={response.json()!r}"
        )
        assert response.data["asset_url"] == "https://signed.example/get"

    def test_revoked_sp_get_on_project_bound_asset_denies(
        self, workspace, create_user, target_project, project_bound_asset, spv1_client
    ):
        """Revocation must stop the read. M55 r1 measured: pre-fix the
        SP could still GET the row after the grant was deleted; the
        confirm PATCH on the same row already 403'd. This pins the
        post-fix symmetry."""
        sp = _make_sp(workspace, create_user, name="get-then-revoke")
        ServiceScope.objects.create(
            service_principal=sp,
            project=None,
            resource_type="all",
            action="all",
        )
        grant = _grant(sp, target_project, role_cap=15)

        spv1_client.credentials(HTTP_X_API_KEY="plane_svc_token_get-then-revoke")

        # Sanity: granted → 200.
        with mock.patch("plane.api.views.asset.S3Storage", side_effect=storage_stub):
            granted = spv1_client.get(
                ASSET_DETAIL_URL.format(
                    slug=workspace.slug, asset_id=project_bound_asset.id
                )
            )
        assert granted.status_code == status.HTTP_200_OK, granted.data

        # Revoke.
        grant.delete()

        # Now denied.
        with mock.patch("plane.api.views.asset.S3Storage", side_effect=storage_stub):
            revoked = spv1_client.get(
                ASSET_DETAIL_URL.format(
                    slug=workspace.slug, asset_id=project_bound_asset.id
                )
            )
        _assert_chain_deny(
            revoked,
            "has no active grant for project",
        )


@pytest.mark.contract
@pytest.mark.django_db
class TestSPGenericAssetResourceClassificationUnchanged:
    """The M55 fix must not move ``asset`` into the workspace-level set
    or otherwise change its resource classification — that would be the
    opposite of the M31 direction and would silently widen the SP
    authority on writes.

    The v1 asset GET on a workspace-level (project-less) FileAsset is
    the canary: it must still succeed for an SP with a workspace-wide
    ``asset:read`` scope, because workspace-level assets are the only
    case where the engine's workspace-level branch legitimately
    applies.
    """

    def test_sp_with_workspace_wide_scope_can_read_a_workspace_level_asset(
        self, workspace, create_user, spv1_client
    ):
        """A user-avatar / workspace-logo FileAsset has project_id=None
        and lives at the workspace level. The engine treats that as a
        workspace-scoped resource and a workspace-wide ``asset:read``
        scope row covers it (no ProjectGrant needed for reads)."""
        workspace_level_asset = FileAsset.objects.create(
            attributes={"name": "logo.png", "type": "image/png", "size": 1024},
            asset=f"{workspace.id}/logo.png",
            size=1024,
            workspace=workspace,
            created_by=create_user,
            entity_type=FileAsset.EntityTypeContext.USER_AVATAR,
            is_uploaded=True,
            storage_metadata={"size": 1024},
        )

        sp = _make_sp(workspace, create_user, name="asset-ws-read")
        # Workspace-wide read scope — no project, no grant.
        ServiceScope.objects.create(
            service_principal=sp,
            project=None,
            resource_type="asset",
            action="read",
        )

        spv1_client.credentials(HTTP_X_API_KEY="plane_svc_token_asset-ws-read")

        with mock.patch("plane.api.views.asset.S3Storage", side_effect=storage_stub):
            response = spv1_client.get(
                ASSET_DETAIL_URL.format(
                    slug=workspace.slug,
                    asset_id=workspace_level_asset.id,
                )
            )

        assert response.status_code == status.HTTP_200_OK, (
            f"Workspace-wide read on a workspace-level asset must "
            f"succeed; got {response.status_code} body={response.json()!r}"
        )


@pytest.mark.contract
@pytest.mark.django_db
class TestSPProjectScopedScopeShape:
    """The r2 reviewer surfaced a pre-existing *under*-permission: the
    project-access UI toggle (and the live MacDock SP) actually writes
    a ``ServiceScope`` row with ``project=<id>`` set — not
    workspace-wide. On base that shape got ``403 DENY_SCOPE_MISS``
    because the engine only matched workspace-wide scope rows when
    ``ctx.project_id`` was ``None``; on tip the hook resolves the
    target project from the body / row, so the project-scoped scope
    row matches and the request is allowed. None of the r1 tests
    covered this shape, so the branch's green suite did not prove the
    case the whole mission exists for. This class pins it.
    """

    @pytest.fixture
    def other_project(self, db, workspace, create_user):
        """A second project in the same workspace the SP has neither
        scope nor grant for. Used to assert the "scope-on-another-project"
        shape, which the r2 reviewer verified returns 403
        ``DENY_SCOPE_MISS`` (the scope step, not a spurious grant step).
        """
        proj = Project.objects.create(
            name="Other Project",
            identifier="OP",
            workspace=workspace,
            created_by=create_user,
            network=2,
        )
        ProjectMember.objects.create(
            project=proj, member=create_user, role=20, is_active=True
        )
        return proj

    def test_project_scoped_scope_sp_with_grant_can_create(
        self,
        workspace,
        create_user,
        target_project,
        target_page,
        spv1_client,
    ):
        """The live MacDock SP shape: project-scoped ``asset:create``
        scope row + active ProjectGrant at the matrix floor. The
        request must succeed (200) and the row must land in the
        project the scope row was bound to. This is the case the
        whole mission exists for and was absent from the r1 test
        file.

        On base this shape returned 403 DENY_SCOPE_MISS: the engine
        saw ``ctx.project_id=None``, so a project-scoped scope row
        ``project=A, resource_type=asset, action=create`` did not
        match (only workspace-wide scope rows were considered). On
        tip the hook resolves the target project from the page
        reference, the project-scoped scope row matches, and the
        request passes the scope + grant + role_cap steps.
        """
        sp = _make_sp(workspace, create_user, name="proj-shape-granted")
        ServiceScope.objects.create(
            service_principal=sp,
            project=target_project,
            resource_type="asset",
            action="create",
        )
        _grant(sp, target_project, role_cap=15)

        spv1_client.credentials(
            HTTP_X_API_KEY="plane_svc_token_proj-shape-granted"
        )

        patcher, storage_cls = _patch_storage_allow()
        try:
            response = post_asset(
                spv1_client,
                workspace.slug,
                {
                    "name": "diagram.png",
                    "type": "image/png",
                    "size": 2048,
                    "entity_type": "PAGE_DESCRIPTION",
                    "entity_identifier": str(target_page.id),
                },
            )
        finally:
            patcher.stop()

        assert response.status_code == status.HTTP_200_OK, (
            f"Project-scoped scope + grant SP must succeed; got "
            f"{response.status_code} body={response.json()!r}"
        )
        asset = FileAsset.objects.get(id=response.data["asset_id"])
        assert asset.project_id == target_project.id
        assert asset.page_id == target_page.id
        # The presign was actually issued — the patch is live and the
        # allow case issued a working upload URL.
        assert (
            storage_cls.return_value.generate_presigned_post.call_count == 1
        ), "Allow case must issue exactly one presigned upload URL."

    def test_project_scoped_scope_sp_with_no_grant_denies_create(
        self,
        workspace,
        create_user,
        target_project,
        target_page,
        spv1_client,
    ):
        """The project-scoped-scope SP without a grant must be denied
        at the grant step. Mirrors the r1 workspace-wide regression
        test but for the production bot shape: same fix surfaces
        the same engine branch.
        """
        sp = _make_sp(workspace, create_user, name="proj-shape-ungranted")
        ServiceScope.objects.create(
            service_principal=sp,
            project=target_project,
            resource_type="asset",
            action="create",
        )
        # No ProjectGrant.

        spv1_client.credentials(
            HTTP_X_API_KEY="plane_svc_token_proj-shape-ungranted"
        )

        before_count = FileAsset.objects.filter(workspace=workspace).count()
        patcher, storage_cls = _patch_storage_allow()
        try:
            response = post_asset(
                spv1_client,
                workspace.slug,
                {
                    "name": "diagram.png",
                    "type": "image/png",
                    "size": 2048,
                    "entity_type": "PAGE_DESCRIPTION",
                    "entity_identifier": str(target_page.id),
                },
            )
        finally:
            patcher.stop()

        # See note on the r1 create test above — storage assertion
        # runs first so it catches the unfixed tree on the presign call.
        _assert_no_presign_calls(storage_cls)

        _assert_chain_deny(
            response,
            "has no active grant for project",
        )
        assert (
            FileAsset.objects.filter(workspace=workspace).count() == before_count
        )

    def test_project_scoped_scope_sp_with_grant_can_read(
        self,
        workspace,
        create_user,
        target_project,
        project_bound_asset,
        spv1_client,
    ):
        """Read path: same project-scoped scope + grant shape must
        read a project-bound asset (200 + presigned URL). The r1
        workspace-wide GET test did not cover this; the r2 reviewer
        verified the live shape here.
        """
        sp = _make_sp(workspace, create_user, name="proj-shape-grant-read")
        ServiceScope.objects.create(
            service_principal=sp,
            project=target_project,
            resource_type="asset",
            action="read",
        )
        _grant(sp, target_project, role_cap=5)

        spv1_client.credentials(
            HTTP_X_API_KEY="plane_svc_token_proj-shape-grant-read"
        )

        patcher, storage_cls = _patch_storage_allow()
        try:
            response = spv1_client.get(
                ASSET_DETAIL_URL.format(
                    slug=workspace.slug, asset_id=project_bound_asset.id
                )
            )
        finally:
            patcher.stop()

        assert response.status_code == status.HTTP_200_OK, (
            f"Project-scoped scope + grant SP GET must 200; got "
            f"{response.status_code} body={response.json()!r}"
        )
        assert (
            storage_cls.return_value.generate_presigned_url.call_count == 1
        )

    def test_project_scoped_scope_sp_against_unrelated_project_denies(
        self,
        workspace,
        create_user,
        target_project,
        other_project,
        project_bound_asset,
        spv1_client,
    ):
        """Scope pinned to ``other_project``, asset bound to
        ``target_project`` — the SP must not be able to read across
        the scope. The r2 reviewer verified this is 403
        ``DENY_SCOPE_MISS`` (the scope step, not a spurious grant
        step): the hook resolves the row to ``target_project``, the
        scope row is on ``other_project``, the scope step trips.
        """
        sp = _make_sp(workspace, create_user, name="proj-shape-foreign")
        # Scope pinned to a different project.
        ServiceScope.objects.create(
            service_principal=sp,
            project=other_project,
            resource_type="asset",
            action="read",
        )
        # And a grant on the other project — does not help, the
        # scope step denies before the grant step runs.
        _grant(sp, other_project, role_cap=15)

        spv1_client.credentials(
            HTTP_X_API_KEY="plane_svc_token_proj-shape-foreign"
        )

        patcher, storage_cls = _patch_storage_allow()
        try:
            response = spv1_client.get(
                ASSET_DETAIL_URL.format(
                    slug=workspace.slug, asset_id=project_bound_asset.id
                )
            )
        finally:
            patcher.stop()

        _assert_no_presign_calls(storage_cls)

        _assert_chain_deny(
            response,
            "lacks scope for read asset",
        )
