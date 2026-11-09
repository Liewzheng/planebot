# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Unit tests for the authorization registration of the test-case resources.

The user decided that members may use the test-case feature and that
service principals (and the AI accounts that run as them) may use it too,
so the two resource types are registered in the authorization core:

* they are **project** resources — they are deliberately absent from
  ``WORKSPACE_LEVEL_RESOURCES``, so a write is anchored on a project
  grant;
* reading is open to every project member (GUEST), writing needs MEMBER,
  removing needs ADMIN;
* the per-resource ownership step of the engine applies unchanged: the
  attribution column of every new row is ``created_by_id``, the column the
  engine already reads (``_OWNER_ATTNAME``).
"""

import pytest

from plane.core.authz import actions, resources
from plane.core.authz.engine import _OWNER_ATTNAME, _resource_owner_id
from plane.db.models import (
    TestCase,
    TestCaseIssue,
    TestCaseModule,
    TestCaseStep,
    TestRun,
    TestRunCase,
    TestRunStepRecord,
)


pytestmark = pytest.mark.unit

NEW_RESOURCE_TYPES = ("test_case", "test_run")


class TestVocabulary:
    def test_the_resource_types_are_registered(self):
        assert resources.ResourceType.TEST_CASE == "test_case"
        assert resources.ResourceType.TEST_RUN == "test_run"

    @pytest.mark.parametrize("resource_type", NEW_RESOURCE_TYPES)
    def test_a_registered_type_is_not_default_deny(self, resource_type):
        assert resources.is_registered(resource_type) is True

    @pytest.mark.parametrize("resource_type", NEW_RESOURCE_TYPES)
    def test_the_types_are_project_scoped(self, resource_type):
        assert resources.is_workspace_level(resource_type) is False
        assert resource_type not in resources.WORKSPACE_LEVEL_RESOURCES

    @pytest.mark.parametrize("resource_type", NEW_RESOURCE_TYPES)
    def test_the_types_are_in_the_choice_list(self, resource_type):
        assert resource_type in dict(resources.RESOURCE_CHOICES)

    def test_the_service_principal_vocabulary_mirrors_the_core(self):
        # The repository keeps two copies of the vocabulary and a contract
        # test asserts they never drift; an SP can only hold a scope row
        # for a resource type its own copy knows.
        from plane.service_principals.constants import (
            RESOURCE_CHOICES as SP_CHOICES,
        )

        assert dict(resources.RESOURCE_CHOICES) == dict(SP_CHOICES)

    @pytest.mark.parametrize("resource_type", NEW_RESOURCE_TYPES)
    def test_the_service_principal_vocabulary_knows_the_types(self, resource_type):
        from plane.service_principals.constants import (
            RESOURCE_CHOICES as SP_CHOICES,
        )

        assert resource_type in dict(SP_CHOICES)


class TestActionMatrix:
    @pytest.mark.parametrize("resource_type", NEW_RESOURCE_TYPES)
    @pytest.mark.parametrize(
        "action,expected_role",
        [
            (actions.Action.READ, actions.ROLE_GUEST),
            (actions.Action.LIST, actions.ROLE_GUEST),
            (actions.Action.CREATE, actions.ROLE_MEMBER),
            (actions.Action.UPDATE, actions.ROLE_MEMBER),
            (actions.Action.DELETE, actions.ROLE_ADMIN),
        ],
    )
    def test_the_required_role_of_each_action(self, resource_type, action, expected_role):
        assert actions.required_role(action, resource_type) == expected_role

    @pytest.mark.parametrize("resource_type", NEW_RESOURCE_TYPES)
    def test_every_action_is_registered_for_the_type(self, resource_type):
        # A half-registered type would deny one action with
        # DENY_UNREGISTERED instead of the role floor.
        for action in (
            actions.Action.READ,
            actions.Action.LIST,
            actions.Action.CREATE,
            actions.Action.UPDATE,
            actions.Action.DELETE,
        ):
            assert actions.required_role(action, resource_type) is not None

    def test_the_matrix_only_knows_the_documented_roles(self):
        for required in actions.ACTION_REQUIRED_ROLE.values():
            assert required in (actions.ROLE_GUEST, actions.ROLE_MEMBER, actions.ROLE_ADMIN)


class TestOwnershipReusesTheExistingRule:
    def test_the_attribution_column_is_the_one_the_engine_reads(self):
        assert _OWNER_ATTNAME == "created_by_id"

    @pytest.mark.parametrize(
        "model",
        [
            TestCaseModule,
            TestCase,
            TestCaseStep,
            TestRun,
            TestRunCase,
            TestRunStepRecord,
            TestCaseIssue,
        ],
    )
    def test_every_new_model_carries_the_attribution_column(self, model):
        # The ownership step of the engine reads ``created_by_id``; a
        # model without that column could never be owned by a service
        # principal, and every member-tier write on it would deny.
        assert hasattr(model, _OWNER_ATTNAME)

    def test_the_engine_reads_the_owner_from_the_attribution_column(self, db):
        from plane.db.models import Project, User, Workspace

        user = User.objects.create(email="owner-m63@plane.so", username="owner_m63")
        workspace = Workspace.objects.create(
            name="M63 Ownership Workspace",
            slug="m63-ownership-workspace",
            owner=user,
        )
        project = Project.objects.create(
            name="M63 Ownership Project",
            identifier="OWN",
            workspace=workspace,
            network=2,
        )
        module = TestCaseModule.objects.create(
            project=project,
            code="CLI",
            description="The command line interface of pbot",
        )
        case = TestCase(
            project=project,
            module=module,
            title="Log in with a valid account",
            purpose="Make sure the login path works",
            object_overview="The login form of pbot",
            priority="P1",
            preconditions="The account exists",
            environment="Windows 10, pbot 1.2.3",
            created_by=user,
        )
        # ``BaseModel.save`` fills ``created_by`` from the current user; a
        # test has no request context, so the attribution is set by hand.
        case.save(disable_auto_set_user=True)

        assert case.created_by_id == user.id
        assert _resource_owner_id(case) == str(user.id)
        assert _resource_owner_id(None) is None
