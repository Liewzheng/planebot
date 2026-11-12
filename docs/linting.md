# Linting in Plane - How It Works

We use [OxLint](https://oxc.rs/docs/guide/usage/linter) for linting across the entire monorepo. OxLint is a single Rust binary that's 50-100x faster than ESLint, with zero Node.js dependencies at runtime.

## Key Points

1. **Single Root Config** - One `.oxlintrc.json` at the repo root handles all packages and apps
2. **No Build Required** - OxLint doesn't need TypeScript build artifacts, so lint runs independently of build
3. **Plugin Coverage** - react, typescript, jsx-a11y, import, promise, unicorn, oxc

## How to Run

From the root of the repo:

```bash
# Check for lint errors
pnpm check:lint

# Auto-fix lint errors
pnpm fix:lint
```

To lint a specific package:

```bash
pnpm turbo run check:lint --filter=@plane/ui
```

## VS Code Integration

Install the [OxLint extension](https://marketplace.visualstudio.com/items?itemName=nicolo-ribaudo.vscode-oxlint) for inline errors/warnings as you type.

## What Gets Linted

The config applies to all TypeScript and JavaScript files across:

- `apps/web`, `apps/admin`, `apps/space`, `apps/live`
- All packages in `packages/`

**Ignored paths:**

- `node_modules/`, `dist/`, `build/`, `.next/`, `.turbo/`
- Config files (`*.config.{js,mjs,cjs,ts}`)
- Public folders, coverage, storybook-static

## Rules Overview

OxLint uses category-based configuration:

| Category        | Level | What It Catches                          |
| --------------- | ----- | ---------------------------------------- |
| **correctness** | error | Real bugs that will cause runtime errors |
| **suspicious**  | warn  | Code patterns that are likely mistakes   |
| **perf**        | warn  | Performance anti-patterns                |

Additional rule overrides:

- `react/prop-types` off (TypeScript handles prop validation)
- `no-unused-vars` warns with `_` prefix pattern ignored
- Several noisy unicorn rules disabled

## Backward Compatibility

OxLint supports `eslint-disable` comments, so existing inline suppressions continue to work.

## Suppressing Warnings

```typescript
// Single line
// eslint-disable-next-line no-unused-vars
const data = response;

// Block
/* eslint-disable no-unused-vars */
// ... code
/* eslint-enable no-unused-vars */
```

**Please use sparingly** - most warnings indicate real issues that should be fixed.

## Pre-commit Hook

Lint-staged runs automatically on commit via Husky:

- oxfmt formats your staged files
- OxLint fixes what it can (errors only — the hook does not reject warnings)

If the commit fails due to lint errors, fix them before committing.

### The hook does not gate warnings

The hook runs OxLint over the whole staged file, not over your diff. It
therefore sees warnings that were already in the file before you touched it, so
it deliberately does **not** fail the commit on warnings (`--deny-warnings` is
not used here). Warnings that `--fix` can fix are still fixed automatically;
leaving a pre-existing warning alone is not a commit blocker.

Because `.oxlintrc.json` sets every rule category to `warn`, rule findings
never fail the hook; only parse errors (a syntax error in a staged file) do.

The warning budget is enforced by each package's own `check:lint` script
(`oxlint --max-warnings=N .`), which is what CI runs. That budget is the gate —
do not treat the hook's tolerance of warnings as a misconfiguration to "fix",
and do not raise a package's `--max-warnings` budget to make the hook happy.

If a package is over its budget, `pnpm check:lint` (or
`pnpm turbo run check:lint --filter=@plane/<package>`) reports the exact
warnings to fix.

## Reference Files

- [.oxlintrc.json](../.oxlintrc.json) - OxLint configuration
- [package.json](../package.json) - Available scripts
