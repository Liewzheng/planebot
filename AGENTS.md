# Agent Development Guide

## Commands

- `pnpm dev` - Start all dev servers (web:3000, admin:3001)
- `pnpm build` - Build all packages and apps
- `pnpm check` - Run all checks (format, lint, types)
- `pnpm check:lint` - OxLint across all packages
- `pnpm check:types` - TypeScript type checking
- `pnpm fix` - Auto-fix format and lint issues
- `pnpm turbo run <command> --filter=<package>` - Target specific package/app
- `pnpm --filter=@plane/ui storybook` - Start Storybook on port 6006

## Code Style

- **Imports**: Use `workspace:*` for internal packages, `catalog:` for external deps
- **TypeScript**: Strict mode enabled, all files must be typed
- **Formatting**: oxfmt, run `pnpm fix:format`
- **Linting**: OxLint with shared `.oxlintrc.json` config
- **Naming**: camelCase for variables/functions, PascalCase for components/types
- **Error Handling**: Use try-catch with proper error types, log errors appropriately
- **State Management**: MobX stores in `packages/shared-state`, reactive patterns
- **Testing**: All features require unit tests, use existing test framework per package
- **Components**: Build in `@plane/ui` with Storybook for isolated development

## Backend tests (Docker)

The Django/pytest suite for `apps/api` runs in an isolated stack defined by `docker-compose-test.yml` at the repo root.

Prereq (once): `./setup.sh` — generates `apps/api/.env` from `.env.example`.

- Full suite: `docker compose -f docker-compose-test.yml up --build --abort-on-container-exit --exit-code-from api-tests`
- Subset: `docker compose -f docker-compose-test.yml run --rm api-tests pytest -m unit`
- Teardown: `docker compose -f docker-compose-test.yml down -v`

See `apps/api/tests/RUNNING_TESTS.md` for the full walkthrough and troubleshooting; see `apps/api/tests/TESTING_GUIDE.md` for test conventions and fixtures.

Known test-stack issue: `requirements/test.txt` pins httpx 0.24.1, which breaks with anyio ≥ 4.15 (openai import crashes). Work around per run:

```bash
docker compose -f docker-compose-test.yml run --rm api-tests sh -c 'pip install -q "anyio<4.15" && pytest <args>'
```

Known suite issue: running the v1 contract tests in bulk without raising the API-key throttle produces exactly **61 failures in `plane/tests/contract/api/`**, all `assert 429 == 404`. Cause is not the code: every v1 contract test shares one API token, and `API_KEY_RATE_LIMIT` defaults to `60/minute` (`plane/api/rate_limit.py`, keyed on `X-Api-Key`). Raise it for the run and they pass:

```bash
docker compose -f docker-compose-test.yml run --rm -e API_KEY_RATE_LIMIT=10000/minute \
  api-tests pytest plane/tests/contract plane/tests/unit -q --create-db
# → 1928 passed
```

Do not go hunting those 61 as regressions; a change that introduces new failures will show up on top of this pattern, not as this pattern.

Also note `pytest.ini` uses `--nomigrations`, so the suite never runs the migration chain. Migration correctness (defaults, backfills) has to be proven separately: assert the migration's operations directly, and/or do a real `manage.py migrate` up/down/up against the test database.

## Repo topology and branch policy (planebot)

This fork's own home is **https://github.com/Liewzheng/planebot** (git remote `planebot`) — it is our repo, independent of `makeplane/plane`. Do not confuse the two nearby names: `planebot` is this fork's home repo, `planebotcli` (`Liewzheng/planebotcli`) is the Rust CLI that gets driven as `pbot`.

Remotes on this checkout: `planebot` = our home repo, `origin` = upstream `makeplane/plane`, `fork` = `Liewzheng/plane` (a ~600-branch mirror of upstream — never work or clean branches there).

Branch policy:

- `main` — stable line: what is deployed and verified on the self-host instance. Fast-forward only.
- `dev` — development line: this checkout's `dev` branch tracks `planebot/dev`, and this checkout sets `push.default=upstream`, so a plain `git push` from `dev` lands on `planebot/dev`. (Historical note: the branch used to be named `integration/selfhost`; it was renamed to `dev` on 2026-11-11 so local names match the home repo.)
- Any other branch on `planebot` is a short-lived topic branch and gets deleted once merged.

```bash
# ordinary development push (upstream is already configured)
git push
# promote the current tip to stable, fast-forward only
git push planebot dev:main
```

## Upstream is no longer maintained (2026-11-09)

We do **not** maintain the official `makeplane/plane` repo any more — not the code, not the PRs, not the issues. The only line we maintain is **pbot: this fork**.

- Do not open, update, reply on, or re-submit anything upstream (PRs, issues, discussions). The upstream PRs filed earlier (#9761, #9780, #9798, …) are archived history; leave them as they are.
- **But do not dismiss upstream review comments either.** A finding written against one of those PRs describes code that also lives here. Treat it as a lead that must be verified against **this** repository — locate the file:line, check whether the described behaviour actually exists here, and only then decide. Two examples from 2026-11-09 where the lead was real: PR #9780's comment exposed an authorization bypass in `apps/live`'s `issue-events` channel (tracked as PLANE-115, fixed by mission M69), and PR #9798's two CodeRabbit items were both present in our locale files (PLANE-116).
- Every fix lands here: topic branch → review → `dev` (deploys) → `planebot/dev`, with `main` fast-forward only. Nothing goes upstream.

## API surface policy (2026-11-12)

Three surfaces, three different promises. Do not mix them up:

| Surface                                   | Callers                                               | Promise                                                                                                                                                         |
| ----------------------------------------- | ----------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/api/v1/...`                             | pbot CLI, scripts, external tools, service principals | **Additive only.** Fields and semantics may be added, never changed or removed; a deprecation runs at least two minor releases. This is the only external face. |
| `/api/workspaces/...` (the "app" surface) | our own web UI                                        | No external promise — it moves with the UI. **The CLI must not call it**, and internals may change without notice.                                              |
| `/api/assets/v2/...`                      | file upload/download                                  | The _asset subsystem's_ own version number, unrelated to API versioning.                                                                                        |

Rules:

- A capability the **CLI** needs ships on **v1 in the same change**. If it exists only on the app surface, either add the thin v1 mirror or do not use it from the CLI.
- A rule that both surfaces must enforce lives in **one implementation** (for example `check_authoring`), called from both — never copy-pasted. We have already paid for this twice (M9: internal API bypassed the SP scope gate; M94: the add-to-batch authoring gate landed on the app surface only).
- A new **major** version (`/api/v2/`) is reserved for an unavoidable incompatible change, and it comes with a written migration window in which v1 keeps working. Never open one for tidiness.
- pbot CLI: **v1 only.**

## Self-host images (dev only)

The deployment in `~/docker/plane` runs images built from the `dev` branch. Rebuild and redeploy procedure:

```bash
git checkout dev

# Build only what changed (repo root is the build context for the frontends)
docker build -f apps/api/Dockerfile.api -t plane-api:latest apps/api
docker build -f apps/web/Dockerfile.web -t plane-web:latest .
docker build -f apps/space/Dockerfile.space -t plane-space:latest .
docker build -f apps/admin/Dockerfile.admin -t plane-admin:latest .

# Recreate the affected services and apply DB migrations
cd ~/docker/plane
docker compose up -d --force-recreate api worker beat-worker web space admin
docker exec api python manage.py migrate
```

Notes:

- The web/admin Caddy static rate limit is raised to 3000 req/min on this branch (`apps/*/caddy/Caddyfile`); rebuilding from any other branch restores the upstream 300/min and cold-cache page loads will 429.
- When upstream `preview` moves forward, re-merge the source branches (`feat/*`, `fix/*`) into `dev` before rebuilding.
