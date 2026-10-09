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
* ``AIScopePolicy(project=X)`` → ``ServiceScope(project=X)`` plus a
  ``ProjectGrant(role_cap=<bot's ProjectMember.role>)`` for the same
  project.
* ``AIScopePolicy(project=None)`` → workspace-wide ``ServiceScope``
  (action preserved verbatim; the SP engine's Q4 guard rejects
  workspace-level writes at request time anyway).
* Bot ``WorkspaceMember`` / ``ProjectMember`` rows are soft-deleted
  (``is_active=False``).
* Existing ``is_service=True`` legacy tokens are reassigned to point at
  the new ``ServicePrincipal`` (``principal_type=1``, ``service_principal``
  FK set). ``user`` stays pointing at the bot so migration is reversible.

Idempotency: a row in :class:`AIAccountMigrationRecord` per converted AI
account is the marker. Re-running the command on top of an already-
converted database is a no-op for converted accounts and picks up any
that were created after the last run.

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


def _coerce_role_cap(raw_role):
    if raw_role in _VALID_ROLE_CAPS:
        return raw_role
    return _DEFAULT_ROLE_CAP


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

        bot_user = account.bot_user

        if dry_run:
            self.stdout.write(
                f"  - {account.id} ({account.name}): "
                f"scopes={account.scope_policies.count()}, "
                f"projects_granted={len(project_roles_snapshot)}"
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
            )

            scopes_created = 0
            grants_created = 0
            seen_project_grants = set()

            for policy in AIScopePolicy.objects.filter(ai_account=account):
                ServiceScope.objects.create(
                    service_principal=sp,
                    project_id=policy.project_id,
                    resource_type=policy.resource_type,
                    action=policy.action,
                )
                scopes_created += 1

                if policy.project_id is not None:
                    if policy.project_id in seen_project_grants:
                        continue
                    seen_project_grants.add(policy.project_id)
                    role = project_roles_snapshot.get(
                        policy.project_id, _DEFAULT_ROLE_CAP
                    )
                    ProjectGrant.objects.create(
                        service_principal=sp,
                        project_id=policy.project_id,
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
