# AIAccount → ServicePrincipal migration (M12) — deployment checklist

This deliverable ships the M12 migration tooling: the conversion
management command, the dual-read shim, the kill switch, the model +
migration record, and the retired policy/views/urls/mixin code paths.
The actual cutover from "old bots" to "service principals" is a phased
roll-out that this checklist walks through.

## 1. Phase 1 — Deploy the code

Push the M12 branch to `integration/selfhost`. The migration tooling is
present, the dual-read shim is active by default, the kill switch
(`PLANE_APIKEY_DISABLE_LEGACY_DUAL_READ`) defaults to `False`, and the
legacy ai_accounts views/urls/signals/policy/mixin files have been
deleted.

State of the system right after the deploy (no command run yet):

* `AIAccount` rows + `AIScopePolicy` rows + tokens all unchanged.
* An old `plane_api_<hex>` token still resolves through the user-token
  path → `request.user` is the bot User, the legacy
  `AIScopeEnforcementMixin`-driven branch fires.
* A new `plane_svc_<hex>` token resolves through the SP path →
  `request._sp_principal` is set → the authorization chain runs.
* No regressions on the human / SP / bot paths that existed before.

This first deploy is *not* a behavior change for existing tokens. It is
purely additive.

## 2. Phase 2 — Run the conversion command

```bash
# Dry run first to confirm scope
docker exec api python manage.py migrate_ai_accounts_to_service_principals --dry-run

# Apply. Idempotent: re-running on a converted DB is a no-op.
docker exec api python manage.py migrate_ai_accounts_to_service_principals
```

The command:

1. Iterates every `AIAccount` row.
2. For each account that does **not** yet have an
   `AIAccountMigrationRecord`:
   * creates a `ServicePrincipal` with `id == ai_account.id` (so the
     deterministic id keeps future FK lookups trivial);
   * mirrors `name` / `description` / `owner` / `is_active`;
   * copies the bot's `avatar_asset.asset_url` into `SP.avatar`
     (truncated to 800 chars; falls back to `User.avatar` when no asset);
   * creates one `ServiceScope` per `AIScopePolicy`;
   * for project-scoped policies, creates one `ProjectGrant` per
     project with `role_cap` taken from the bot's
     `ProjectMember.role` for that project (coerced to 20/15/5);
   * soft-deletes the bot's `WorkspaceMember` and `ProjectMember`
     rows;
   * reassigns every `is_service=True` legacy token whose
     `principal_type` is still `USER`: flips
     `principal_type` → `SERVICE`, sets `service_principal`,
     repoints `user` → owner;
   * records the metrics (`scopes_migrated`, `grants_migrated`,
     `tokens_migrated`) in `AIAccountMigrationRecord`.

Mark this command's stdout (it prints per-account lines) — ops needs
the "Converted N, skipped M, errors K" line for the runbook.

If any row fails, the per-account work is wrapped in
`transaction.atomic()` so failures are local; the loop logs the error
and continues with the next row.

## 3. Phase 3 — Observe (dual-read window)

The dual-read shim and the kill switch run from this point onward. The
shim has two material behaviors:

* `principal_type = SERVICE` (migrated) tokens (`plane_api_*` or
  `plane_svc_*`) → SP branch in `APIKeyAuthentication`. The bot's old
  `request.user.is_bot` is **not** re-evaluated.
* `principal_type = USER` tokens with the default setting → legacy
  bot path (no-op today, would 403 from any URL where the bot is not a
  member).

Suggested observation signals (operators should pin a Grafana chart per
release):

| Signal                                               | Healthy |
|------------------------------------------------------|---------|
| `api_auth_outcome{outcome="migrated_sp"}` rising     | yes     |
| `api_auth_outcome{outcome="legacy_bot"}` → 0         | yes     |
| `4xx_403_from_v1_endpoint` not climbing              | yes     |
| `bot_token_last_used_at` for the affected workspaces | tracked |
| `AIAccount` rows whose `migration_record` is missing | → 0     |

Run a fresh conversion pass before flipping the kill switch:

```bash
docker exec api python manage.py migrate_ai_accounts_to_service_principals
# expected output: "Converted 0, skipped N, errors 0"
```

A non-zero "Converted" count means some bot was created mid-window —
re-run until it stabilizes.

## 4. Phase 4 — Flip the kill switch

When the observation signals have been quiet for an agreed window
(suggested 24h minimum, longer if pre-migration traffic was heavy):

1. Add to the API container's env (Caddy / docker-compose / k8s
   `ConfigMap`):
   ```
   PLANE_APIKEY_DISABLE_LEGACY_DUAL_READ=True
   ```
2. Roll the API.
3. Watch `api_auth_outcome{outcome="legacy_bot"}` — it must remain at
   0. A non-zero value means there are live `plane_api_*` bot tokens
   that the conversion command did not reach, which is a follow-up to
   investigate (re-run the conversion with a fix-forward patch, or
   rotate the bot tokens by hand).

After the kill switch is on, an `principal_type = USER`
`plane_api_<hex>` token will fail with 401/403 — no silent fallback to
the bot path. That is the documented fail-closed contract.

## 5. Phase 5 — Drop the legacy tables (follow-up PR)

The M12 branch keeps the `ai_accounts` app registered so the
management command is discoverable, and keeps the `AIAccount` /
`AIScopePolicy` / `AIAccountMigrationRecord` tables so rollback / audit
queries still resolve. Once the kill switch has been on for a few weeks
and there are no signs of leftover usage:

* Delete `plane/ai_accounts/` entirely (app, migrations, model,
  management command).
* Drop the `ai_accounts`, `ai_scope_policies`, and
  `ai_account_migration_records` tables in a follow-up cleanup
  migration.
* Remove `plane.ai_accounts` from `INSTALLED_APPS`.
* Re-run the API test stack to confirm nothing depended on the AI
  account tables.

This phase is intentionally out of scope for M12 to keep the
cutover reversible during Phase 3/4.

## Rollback plan

* **Phase 1 → Phase 2:** revert the deploy. No data was changed.
* **Phase 2 → Phase 3:** the command wrote `ServicePrincipal` /
  `ServiceScope` / `ProjectGrant` rows and soft-deleted bot memberships.
  To roll back, *do not* re-run the command (that would re-skip
  migrated accounts anyway). Manually:
  1. Re-activate `WorkspaceMember` / `ProjectMember` rows.
  2. Re-flip `APIToken.principal_type` to `USER` and clear
     `service_principal_id`.
  3. Delete the `ServicePrincipal` / `ServiceScope` /
     `ProjectGrant` rows that were just created.
  4. Leave `AIAccountMigrationRecord` rows in place so a follow-up
     conversion is a no-op until you decide what to do.
  The revert is functional but laborious; it is why phases 3 and 4 are
  observed before 5.
* **Phase 4 → Phase 3:** unset
  `PLANE_APIKEY_DISABLE_LEGACY_DUAL_READ`. Re-migrated tokens keep
  working through the SP branch; any stale
  `principal_type = USER` token re-resolves to its bot User again.
  No data touched, no deploy required (settings change only).

## Verification anchors

* The M12 contract test suite at
  `apps/api/plane/tests/contract/app/test_migration_dual_read_shim.py`
  exercises every step above (dry-run, first-run idempotency, second-
  run idempotency, late-arrival pickup, workspace-wide scope
  preservation, migrated-token SP resolution, legacy-token default
  behavior, kill-switch fail-closed). The full suite must stay green
  through every phase above.

* `apps/api/plane/service_principals/views.py` already exposes the
  management surface that the migrated SPs will land on — no further
  wiring is needed once Phase 2 has run.
