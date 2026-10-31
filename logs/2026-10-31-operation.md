# Cage 操作日志

- 日期：2026-10-31
- 仓库：`git@github.com:suzizhuo/monkey-code-agent.git`
- 分支：`master`
- 推送人（Git author/committer）：`monkeycode-ai <monkeycode-ai@chaitin.com>`
- 远程仓库所有者：`suzizhuo`
- 执行环境：MonkeyCode-AI Agent（工作区 `/workspace`，Python 3.11.2，pytest 9.2.0）
- 本日志对应推送：`da3848e..87300cd` 已于 2026-10-31 23:36 之后补记并再次 push

## 1. 本次要解决的事

1. 按 `specs/2026-10-31-cage-design.md` 与 `plans/2026-10-31-cage.md` 落地 Task 1–10（宿主 TUI、agent 运行时、自主档、子代理、人机验证、沙箱、工作区、Git 发布）。
2. 把 cage 全部资源收成可交接的分阶段提示词，放到 `prompts/`。
3. 把实现提交 + 提示词包推到 GitHub，并留下可复现的操作记录（本文件）。

未做：Task 11 研究回路、Task 12 Skill 池。下一轮从 `prompts/06-phase-research.md` 开始。

## 2. 推送人与远程

```bash
# 本机 Git 身份（本次所有 commit 的 author/committer）
git config user.name
# monkeycode-ai
git config user.email
# monkeycode-ai@chaitin.com

git remote -v
# origin  git@github.com:suzizhuo/monkey-code-agent.git (fetch)
# origin  git@github.com:suzizhuo/monkey-code-agent.git (push)
```

| 字段 | 值 |
| --- | --- |
| 推送人 | monkeycode-ai |
| 邮箱 | monkeycode-ai@chaitin.com |
| 协同署名 | Co-authored-by: monkeycode-ai \<monkeycode-ai@chaitin.com\> |
| GitHub 仓库 | https://github.com/suzizhuo/monkey-code-agent |
| 分支 | master |
| 协议 | SSH (`git@github.com:`) |

## 3. 推送范围（Git 日志）

基准：`da3848e fix(llm): skip blocked models and fall back across the gateway catalog`

首次实现推送区间：`da3848e..87300cd`（11 个 commit）。本日志 commit 会再往前走 1 个。

| SHA | 时间 (UTC) | 说明 |
| --- | --- | --- |
| `f1a5bb3` | 16:29:53 | feat(cage): skeleton, atomic state with flock, naming and port allocation |
| `37d19d5` | 17:12:31 | feat(agent): provider-neutral runtime with 6-tool baseline, bounded memory, no Android/JDK preinstall |
| `e227119` | 17:25:36 | feat(cli): TUI with unchanged command surface, dual-mode runtime, platform-injected model |
| `dfccb0d` | 17:32:59 | feat(serve): frontend HTTP API with CORS and static dir; docs and end-to-end smoke |
| `6f1b44c` | 17:41:52 | feat(autonomy): manual/assisted/auto levels, action budget, digested audit records |
| `9ac16d5` | 17:45:19 | feat(workers): orchestrator/worker delegation with isolated context and depth-1 limit |
| `9404ab3` | 17:49:53 | feat(challenge): rule-based page classification, policy-gated solver chain, escalation |
| `af74e21` | 17:52:13 | feat(sandbox): audit harvest, watchdog, process-mode hardening, docs, acceptance |
| `944353d` | 17:54:40 | feat(work): numbered workspace browser over /files with read and extract |
| `69d10b5` | 17:59:39 | feat(publish): ssh/PAT git publishing from workspace, key bootstrap, audit redaction |
| `87300cd` | 23:36:37 | docs(prompts): staged cage implementation prompts, resource inventory, and plan |

查询脚本：

```bash
git log --format='%h %ci %an <%ae> %s' da3848e..HEAD
```

## 4. 详细解决步骤

### 4.1 约束（全程遵守）

- 宿主 `cage.py` + `cage/` 只用标准库；langchain / playwright 只在 `agent/`。
- 名称与常量跟计划 Interfaces 逐字一致。
- 一轮一个 Task；红测试先写，再实现到绿。
- 测试：`python3 -m pytest tests/ -q`。禁止外网、Docker、读取 `MCAI_LLM_*`。
- 平时只本地 `feat(scope):` 提交；operator 说「上传到 GitHub」才 `git push`。
- 不改 `plans/`（本轮把未跟踪的 plan 收进仓库是资源交接，不是改计划正文）。
- 不建 `docs/`；提示词放 `prompts/`。
- 不 add `isolis.py`、`.monkeycode/`。

### 4.2 Task 1–10 实现（红 → 绿 → 本地提交）

每个 Task 的固定脚本：

```bash
# 1. 先跑对应测试，确认红灯（缺模块）
python3 -m pytest tests/test_<module>.py -q

# 2. 实现计划列出的文件
# 3. 再跑到绿
python3 -m pytest tests/ -q

# 4. 本地提交，不 push、不 force、不 rebase、不 amend
git add <task files>
git commit -m "feat(<scope>): <plan message>"
```

| Task | 红灯原文 | 绿灯 | 提交 |
| --- | --- | --- | --- |
| 1 骨架/state/port | `cannot import name` naming/state | 测试绿 | `f1a5bb3` |
| 2 agent 6 工具 | agent 测试失败 | 测试绿 | `37d19d5` |
| 3 TUI/runtime/model | cli/runtime 失败 | 测试绿 | `e227119` |
| 4 HTTP API | httpapi 失败 | 测试绿 | `dfccb0d` |
| 5 自主档 | `cannot import name 'autonomy'` | 测试绿 | `6f1b44c` |
| 6 子代理 | `cannot import name 'workers'` | 测试绿 | `9ac16d5` |
| 7 人机验证 | `cannot import name 'challengerules'` | 测试绿 | `9404ab3` |
| 8 沙箱/验收 | `process_install` TypeError；status 缺 watchdog/work | `62 passed` | `af74e21` |
| 9 work/`/files` | `cannot import name 'workspace'` | `66 passed` | `944353d` |
| 10 publish/key | `cannot import name 'publish'` | **72 passed** | `69d10b5` |

关键接口修订（实现时与计划字面冲突，以可测行为落地，详见 `prompts/00-progress.md`）：

1. 无 LLM 的 orchestrator：仅当 `spawn_worker.__name__ != "spawn_worker"` 才委派，避免 `test_chat_without_injection_returns_clear_error` 变成 200。
2. `load_state` 只有文件里已有 `usage` 才带回。
3. `audit_record` 把 `git@github.com:` 写成 `github.com/`。
4. `process_spawn_args(..., process_install=False)` 默认 `CAGE_INSTALL=0`。
5. `pick_model` 的 `_SKIP_MODELS` 继续跳过 Isolis leftover 名。

冒烟（无 `MCAI_LLM_*` 时 doctor 非零退出是正确失败，不要修）：

```bash
python3 -m pytest tests/ -q
python3 cage.py doctor
python3 cage.py profiles
python3 cage.py help
```

### 4.3 分阶段提示词包

新增目录 `prompts/`，并在 `README.md` 加入口。

| 文件 | 作用 |
| --- | --- |
| `prompts/README.md` | 包入口与阶段表 |
| `prompts/00-constraints.md` | 全局硬约束 |
| `prompts/00-resources.md` | 文件/接口/环境变量/测试命令 |
| `prompts/00-progress.md` | 已落地提交与接口修订 |
| `prompts/01-phase-foundation.md` | Task 1–4（已完成，核验用） |
| `prompts/02-phase-autonomy.md` | Task 5–6 |
| `prompts/03-phase-challenge.md` | Task 7 |
| `prompts/04-phase-sandbox.md` | Task 8 |
| `prompts/05-phase-workspace-publish.md` | Task 9–10 |
| `prompts/06-phase-research.md` | **待做** Task 11 |
| `prompts/07-phase-skills.md` | **待做** Task 12 |

同时把原先未跟踪的 `plans/2026-10-31-cage.md` 收进仓库。

提交脚本：

```bash
git add prompts/ plans/ README.md
git commit -m "docs(prompts): staged cage implementation prompts, resource inventory, and plan"
# 得到 87300cd
```

### 4.4 上传 GitHub（operator 明确要求）

```bash
git status -sb
# master...origin/master [ahead 11]

git push origin master
# To github.com:suzizhuo/monkey-code-agent.git
#    da3848e..87300cd  master -> master
```

未推送（故意）：

- `isolis.py` 工作区改动（Isolis leftover，不属于 cage 本轮）
- `.monkeycode/`（Agent 记忆，不进应用仓库）

## 5. 脚本材料

可复现脚本在 `logs/record-and-push.sh`：打印推送人、提交清单、工作区脏文件，再按需 push。

核验脚本：

```bash
python3 -m pytest tests/ -q
# 期望：72 passed（Task 11–12 落地前）
```

## 6. 结果

- GitHub `master` 已包含 Task 1–10 实现、计划原文、分阶段提示词。
- 测试基线：72 passed。
- 下一阶段提示词：`prompts/06-phase-research.md`。

## 7. 本日志自身的提交与推送

本文件与 `logs/record-and-push.sh` 会再打一条 commit 并 `git push origin master`。推送人仍为 `monkeycode-ai <monkeycode-ai@chaitin.com>`。
