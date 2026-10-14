# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Idempotent AIAccount → ServicePrincipal migration command.

Convert legacy ``AIAccount`` rows into ``ServicePrincipal`` rows so the
service path (M6+) becomes the sole credentialed principal type for AI bots.
Expected to be run *after* the SP foundation + authz chain ships and
*before* the ai_accounts app is retired.

Conversion rules — these match the migration contract in M12:

* ``AIAccount`` → ``ServicePrincipal`` (id carried over deterministically;
  ``name`` / ``description`` / ``owner`` / ``is_active`` preserved;
  ``avatar`` populated from the backing bot's ``avatar_asset.asset_url``).
* ``AIScopePolicy(project=X)`` → ``ServiceScope(project=X)``.
* ``AIScopePolicy(project=None)`` → workspace-wide ``ServiceScope``
  (the SP engine's Q4 guard rejects workspace-level writes at request
  time anyway).
* ``action`` is copied through, except for the legacy ``list`` action,
  which the SP vocabulary does not carry: a ``list`` policy whose
  ``(project, resource_type)`` already has a ``read`` policy is dropped
  (permission-equivalent), otherwise it is written as ``read`` with a
  warning. See ``_plan_scope_rows``.
* ``ProjectGrant(project=X, role_cap=<bot's ProjectMember.role>)`` is
  emitted for every project the bot is reachable in: the union of
  project-scoped ``AIScopePolicy`` projects and the bot's active
  ``ProjectMember`` projects, deduped per project. The
  ``ProjectMember`` arm is required because the conversion soft-deletes
  the bot's memberships in the same transaction — without it, bots that
  only ever held workspace-global policies would lose every project grant
  on the very conversion that retires their ``ProjectMember`` rows, and
  ``authorize()`` would ``DENY_GRANT_MISS`` every project-scoped read.
* Bot ``WorkspaceMember`` / ``ProjectMember`` rows are soft-deleted
  (``is_active=False``).
* Existing ``is_service=True`` legacy tokens are reassigned to point at
  the new ``ServicePrincipal`` (``principal_type=1``, ``service_principal``
  FK set) and re-filed under the account **owner**, so ``request.user`` is
  the owner once the dual-read shim routes the token through the SP branch.
* The account's bot user is carried over as
  ``ServicePrincipal.bot_user``: the rows that bot wrote *before* the
  conversion are already attributed to it, so linking the same user keeps
  "this SP created that" true across the migration instead of orphaning its
  history behind a freshly minted bot.

Idempotency: a row in :class:`AIAccountMigrationRecord` per converted AI
account is the marker. Re-running the command on top of an already-
converted database is a no-op for converted accounts and picks up any
that were created after the last run.

``--dry-run`` reports the same grant and scope counts the apply path
would produce, so the two never disagree (M20 regression).

Usage::

    ./manage.py migrate_ai_accounts_to_service_principals
    ./manage.py migrate_ai_accounts_to_service_principals --dry-run
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from plane.ai_accounts.models import (
    AIAccount,
    AIAccountMigrationRecord,
    AIScopePolicy,
)
from plane.db.models import APIToken, ProjectMember, WorkspaceMember
from plane.service_principals.constants import PrincipalType
from plane.service_principals.models import (
    ProjectGrant,
    ServicePrincipal,
    ServiceScope,
)


# ProjectGrant.role_cap choices — mirrors the existing Plane role integers.
_VALID_ROLE_CAPS = {20, 15, 5}
_DEFAULT_ROLE_CAP = 15

# The legacy AI-account policy vocabulary has a ``list`` action that the SP
# scope vocabulary deliberately does not (``service_principals.constants``
# ACTION_CHOICES has no ``list``; ``core.authz.actions`` does). Copying it
# verbatim is what produced the unreachable SP scope rows retired by
# ``service_principals/migrations/0003_retire_list_scope_rows.py``.
_LEGACY_LIST_ACTION = "list"
_READ_ACTION = "read"


def _coerce_role_cap(raw_role):
    if raw_role in _VALID_ROLE_CAPS:
        return raw_role
    return _DEFAULT_ROLE_CAP


def _plan_scope_rows(policies):
    """Map legacy ``AIScopePolicy`` rows onto the SP scope rows to write.

    Returns ``(rows, skipped, widened)``: ``rows`` is the list of
    ``(project_id, resource_type, action)`` triples to persist, ``skipped``
    counts legacy ``list`` policies dropped because an equivalent ``read``
    row is written for the same ``(project_id, resource_type)``, and
    ``widened`` counts the ``list`` policies that became ``read`` because no
    such sibling exists.

    ``list`` is not part of the SP vocabulary — the management API only
    exposes ``read``, and ``core/authz/engine.py::_scope_action_set`` already
    lets a ``read`` row satisfy a ``list`` request — so a copied ``list`` row
    is unreachable in practice *and* strictly dominated by a ``read`` row on
    the same resource. Two cases:

    * a ``read`` policy exists for the same ``(project_id, resource_type)``:
      the legacy row grants nothing the ``read`` row does not already grant
      (``_scope_action_set('list')`` includes ``read``), so it is dropped —
      permission for permission identical.
    * no ``read`` sibling exists: dropping the row would lose the account's
      read access, so it is widened to ``read`` and reported as a warning.
      That is the deliberate trade-off — ``read`` still satisfies the ``list``
      request, so nothing the account could do before is lost, but the row may
      now also authorize plain ``read`` requests, which the operator should
      re-check.

    The decision is taken against the whole policy set, not row order, so it
    does not depend on the queryset's ordering. Only an exact ``read`` policy
    counts as a sibling: an ``action='all'`` policy would cover a ``list``
    request too, but it is not treated as one here — widening is always the
    safe direction, so the rule errs where it cannot lose reach.
    """
    read_keys = {
        (policy.project_id, policy.resource_type)
        for policy in policies
        if policy.action == _READ_ACTION
    }

    rows = []
    skipped = 0
    widened = 0
    for policy in policies:
        if policy.action == _LEGACY_LIST_ACTION:
            if (policy.project_id, policy.resource_type) in read_keys:
                skipped += 1
                continue
            rows.append((policy.project_id, policy.resource_type, _READ_ACTION))
            widened += 1
            continue
        rows.append((policy.project_id, policy.resource_type, policy.action))

    return rows, skipped, widened


def _avatar_for_bot(bot_user):
    """Resolve a flat string for the SP.avatar field from the bot's avatar.

    Prefer ``avatar_asset.asset_url`` when available; fall back to the
    legacy ``avatar`` string field. Empty string when neither is set —
    the SP contract accepts that as "no avatar".
    """
    asset = getattr(bot_user, "avatar_asset", None)
    if asset is not None:
        try:
            url = asset.asset_url
        except Exception:
            url = ""
        if url:
            return str(url)[:800]
    return (getattr(bot_user, "avatar", "") or "")[:800]


class Command(BaseCommand):
    help = "Idempotently migrate AIAccount → ServicePrincipal."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Report what would be converted without writing. The "
                "marker table is not touched in dry-run, so re-running "
                "for real afterwards is fine."
            ),
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        qs = AIAccount.objects.all().order_by("created_at")
        total = qs.count()
        already = AIAccountMigrationRecord.objects.filter(
            ai_account__isnull=False
        ).count()
        self.stdout.write(
            f"Found {total} AIAccount rows "
            f"({already} already converted)."
        )

        converted = 0
        skipped = 0
        errors = 0

        for account in qs.iterator():
            try:
                did_work = self._convert_one(account, dry_run=dry_run)
            except Exception as exc:  # pragma: no cover - log + continue
                errors += 1
                self.stderr.write(
                    self.style.ERROR(
                        f"  FAIL {account.id} ({account.name}): {exc!r}"
                    )
                )
                continue
            if did_work:
                converted += 1
            else:
                skipped += 1

        verb = "Would convert" if dry_run else "Converted"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {converted}, skipped {skipped}, errors {errors} "
                f"(of {total})."
            )
        )

    # ------------------------------------------------------------------
    # Per-account conversion
    # ------------------------------------------------------------------

    def _convert_one(self, account: AIAccount, *, dry_run: bool) -> bool:
        """Convert a single AIAccount.

        Returns True if any work was done, False if the row was already
        converted (idempotency short-circuit). Raises on hard errors so
        the caller can log + continue with the next row.
        """
        existing = AIAccountMigrationRecord.objects.filter(
            ai_account=account
        ).first()
        if existing is not None:
            return False

        # Snapshot memberships BEFORE we touch them. Soft-delete is the
        # last step so a partial-failure before then still has the bot
        # in good standing with the legacy authz chain.
        ws_role_snapshot = WorkspaceMember.objects.filter(
            workspace_id=account.workspace_id,
            member_id=account.bot_user_id,
            is_active=True,
        ).values_list("role", flat=True).first()

        project_roles_snapshot = {
            pid: role
            for pid, role in ProjectMember.objects.filter(
                member_id=account.bot_user_id,
                is_active=True,
            ).values_list("project_id", "role")
        }

        # ProjectGrant projects are the union of:
        #   * project-scoped AIScopePolicy rows (the bot was authorized
        #     for those projects by policy at the time of provisioning),
        #     and
        #   * active ProjectMember rows (the bot held a project seat,
        #     which authorize() needs an explicit grant to honor once
        #     those ProjectMember rows are soft-deleted below).
        # The ProjectMember arm is non-negotiable: it is what stops an
        # account whose every AIScopePolicy is workspace-global from
        # being converted into an SP with zero ProjectGrants, which
        # would lock the bot out of every project at authorize() step 2
        # (``DENY_GRANT_MISS``). It is also deduplicated: a project named
        # by both sources produces exactly one grant, preserving the
        # existing project-scoped-policy behaviour.
        policy_projects = set(
            AIScopePolicy.objects.filter(
                ai_account=account, project_id__isnull=False
            ).values_list("project_id", flat=True)
        )
        membership_projects = set(project_roles_snapshot.keys())
        seen_project_grants = policy_projects | membership_projects

        policies = list(AIScopePolicy.objects.filter(ai_account=account))
        planned_scopes, skipped_list_scopes, widened_list_scopes = _plan_scope_rows(policies)
        scopes_count = len(planned_scopes)
        grants_planned = len(seen_project_grants)

        bot_user = account.bot_user

        if skipped_list_scopes or widened_list_scopes:
            # Reported on both the dry-run and the apply path so the two
            # never disagree about what the conversion does.
            self.stderr.write(
                self.style.WARNING(
                    f"  ! {account.id} ({account.name}): legacy 'list' scopes: "
                    f"{skipped_list_scopes} skipped (a 'read' sibling covers "
                    f"them), {widened_list_scopes} widened to 'read' (no "
                    f"'read' sibling — re-check those grants by hand)"
                )
            )

        if dry_run:
            # Same numbers the apply path would produce, so dry-run and
            # apply agree. Previously dry-run printed the bot's project
            # membership count under ``projects_granted`` while apply
            # printed ``grants`` -- the two diverged on accounts whose
            # policies were all workspace-global.
            self.stdout.write(
                f"  - {account.id} ({account.name}): "
                f"scopes={scopes_count}, grants={grants_planned}"
            )
            return True

        with transaction.atomic():
            sp = ServicePrincipal.objects.create(
                id=account.id,  # deterministic id so token FKs can be
                                # redirected via bulk_update without an
                                # id-resolution step
                workspace_id=account.workspace_id,
                owner_id=account.owner_id,
                name=account.name,
                description=account.description or "",
                avatar=_avatar_for_bot(bot_user),
                is_active=account.is_active,
                # Carry the bot over instead of letting the attribution layer
                # mint a fresh one on first use: the rows this bot wrote
                # before the conversion are already attributed to it, and
                # migration 0005's inherited-bot branch preserves exactly that
                # continuity for accounts converted before it ran. A converted
                # SP must not be the one hole in it.
                bot_user=bot_user,
            )

            scopes_created = 0
            grants_created = 0

            for project_id, resource_type, action in planned_scopes:
                ServiceScope.objects.create(
                    service_principal=sp,
                    project_id=project_id,
                    resource_type=resource_type,
                    action=action,
                )
                scopes_created += 1

            for pid in seen_project_grants:
                role = project_roles_snapshot.get(pid, _DEFAULT_ROLE_CAP)
                ProjectGrant.objects.create(
                    service_principal=sp,
                    project_id=pid,
                    role_cap=_coerce_role_cap(role),
                    is_active=True,
                )
                grants_created += 1

            # Soft-delete memberships. Order doesn't matter — the bot User
            # row is no longer an active member of any project / workspace.
            WorkspaceMember.objects.filter(
                workspace_id=account.workspace_id,
                member_id=account.bot_user_id,
                is_active=True,
            ).update(is_active=False, updated_at=timezone.now())
            ProjectMember.objects.filter(
                member_id=account.bot_user_id, is_active=True
            ).update(is_active=False, updated_at=timezone.now())

            # Reassign tokens. APIToken.user stays pointing at the bot so
            # the legacy auth path can still resolve; ``principal_type``
            # and the ``service_principal`` FK flip the request onto the
            # SP branch in the dual-read shim.
            tokens_updated = APIToken.objects.filter(
                user_id=account.bot_user_id,
                is_service=True,
                principal_type=PrincipalType.USER,
            ).update(
                principal_type=PrincipalType.SERVICE,
                service_principal=sp,
                user_id=account.owner_id,  # so request.user = owner for
                                            # downstream plumbing once the
                                            # dual-read shim routes through
                                            # the SP branch
            )

            AIAccountMigrationRecord.objects.create(
                ai_account=account,
                service_principal=sp,
                scopes_migrated=scopes_created,
                grants_migrated=grants_created,
                tokens_migrated=tokens_updated,
            )

        self.stdout.write(
            f"  + {account.id} ({account.name}): "
            f"scopes={scopes_created}, grants={grants_created}, "
            f"tokens={tokens_updated}"
        )
        return True
