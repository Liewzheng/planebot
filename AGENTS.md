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

## Self-host images (integration/selfhost only)

The deployment in `~/docker/plane` runs images built from the `integration/selfhost` branch. Rebuild and redeploy procedure:

```bash
git checkout integration/selfhost

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
- When upstream `preview` moves forward, re-merge the source branches (`feat/*`, `fix/*`) into `integration/selfhost` before rebuilding.
