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

## 测试用例是交付的一部分（2026-11-12，用户定规）

**每做完一件用户要求的事，都要在 pbot 的「测试」功能里留一条真实执行过的用例。** 这不是可选文档，是验收标准的一部分：没有用例、没有实跑、没有留下证据的改动，只能称为"已合并"，不能称为"已完成"。

必须做到的三件事：

1. **新建用例**——建在受影响的项目的「测试」里，`pbot tc create` 即可（`--module/--title/--purpose/--object-overview/--priority/--precondition/--environment` 都是服务端强制的，缺一个就拒；步骤用 `--step "操作 | 预期"` 或 `--steps-file`）。编号由服务端分配（`TC-<模块码>-<NNN>`），模块码要先 `pbot tc module create` 注册。用例要 `pbot tc issues add` 关联到对应工作项。**若 CLI 报 `lacks scope for ... test_case`，说明当前 SP 没有测试用例 scope——不要卡在这，走 Web「测试」页面建（或用 workspace admin 会话授权后重试），不要为此改代码。**
2. **实际执行**——在 Web「测试」页面的测试批次里跑，逐条填实测值，通过/不通过都据实填。**注意 CLI 目前没有 run/batch 子命令**（`pbot tc` 只有 list/show/create/update/steps/issues/delete/module），所以执行这一步只能走 Web UI 或直接打接口，不要去找不存在的命令。
3. **保留证据**——截图 + 运行日志都要留下，附在用例或实测值里：图片作为附件上传；纯文本日志贴成围栏代码块（附件输入只收图片时，这是唯一可行方式），日志要包含能复现的命令与关键输出，不能只写"测过了"。

补充约束：

- **如实标注**：做不到端到端实测的部分（例如需要造垃圾数据、需要动用户真实工作区），在回复里和用例里都写明"未实测 + 原因 + 现有证据"，不要用"应该没问题"糊过去。
- 历史遗留的「需人工」条目不要因为这条规矩被顺手改写成通过；要通过就真跑一遍。
- 这条规矩同时约束 agent 与人类：人类改完也让 agent 补用例，否则下次回归没有任何东西可依。

## 界面必须被「人类」看过（2026-11-12，用户定规）

**编译通过、字段对了、按钮能点、两轮代码评审 clean —— 都不算"看过了"。** 凡涉及可见界面的改动（页面、弹窗、开关、空状态、文案），部署后必须派一个**独立的人类视角审核者**：一个 agent 扮演真实用户（不是代码评审员），到真实实例上像人一样使用并评判。后端改动同样适用：真实打接口、把响应体读出来看命名与错误码是否自洽。

### 角色：`human-reviewer`

- 用 WebBridge 打开**部署后的真实地址**（不是本地 dev），在常见宽度（约 1440 和 1280）各截全屏图，**真的看图**（`ReadMediaFile`），小字、对齐、间距必要时局部放大再读。
- **必须与同族页面比对**：新设置页对照 `周期`/`估算`/`自动化` 设置页；新弹窗对照「新建项目」等既有弹窗。判断"是否符合现有设计"必须有对照物，不能凭感觉。
- 回答四个问题，逐条给证据：
  1. **好不好看**：间距、对齐、层级、颜色、字号是否与既有规范一致；有没有孤零零的一行、错位的按钮、过宽的留白。
  2. **符不符合设计意图**：和任务书/设计页描述的是同一件事吗，有没有实现成了别的东西。
  3. **是否冗余**：有没有两块在讲同一件事、两个入口指向同一页、重复的说明文字、可以合并却没合并的行。
  4. **文案是否准确自然**：中英双份都读一遍，是否有机器翻译味、是否是内部术语、数字/单位/时态是否对。
- 输出是**问题清单 + 截图证据 + 建议**，不是"通过/不通过"四个字。发现丑和冗余必须报，不许因为"小问题/不影响功能"放过。
- 结论贴到对应 Plane 任务评论（带截图），并作为"这一步是否成立"的判据之一；与代码评审是两回事，二者都要过。

### 为什么写这条（真实代价）

项目设置「测试」页最初放了两块：上面是「启用测试」开关（真状态），下面是一个「测试用例」盒子（其实只是一个跳转链接）。代码评审两轮 clean、`tsc` 通过、开关功能也真实验证过（关→侧栏入口消失、开→恢复）。**但从没人在真实页面上看过它**，于是没人发现第二块是重复入口——用户打开页面第一句话就是"这俩功能一样不？"。一行代码就能修的事，因为没人看一眼而多走了一轮。

判据记牢：**功能正确 ≠ 界面成立**。"能编译、能点通、字段对了"只是必要条件。

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
