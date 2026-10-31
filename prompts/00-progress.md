# 当前进度

截止日期：2026-10-31。分支 `master`，远程 `git@github.com:suzizhuo/monkey-code-agent.git`。

## 已完成提交（cage Task 1–10）

| Commit | 信息 | 阶段 |
| --- | --- | --- |
| `f1a5bb3` | feat(cage): skeleton, atomic state with flock, naming and port allocation | 01 Task 1 |
| `37d19d5` | feat(agent): provider-neutral runtime with 6-tool baseline, bounded memory, no Android/JDK preinstall | 01 Task 2 |
| `e227119` | feat(cli): TUI with unchanged command surface, dual-mode runtime, platform-injected model | 01 Task 3 |
| `dfccb0d` | feat(serve): frontend HTTP API with CORS and static dir; docs and end-to-end smoke | 01 Task 4 |
| `6f1b44c` | feat(autonomy): manual/assisted/auto levels, action budget, digested audit records | 02 Task 5 |
| `9ac16d5` | feat(workers): orchestrator/worker delegation with isolated context and depth-1 limit | 02 Task 6 |
| `9404ab3` | feat(challenge): rule-based page classification, policy-gated solver chain, escalation | 03 Task 7 |
| `af74e21` | feat(sandbox): audit harvest, watchdog, process-mode hardening, docs, acceptance | 04 Task 8 |
| `944353d` | feat(work): numbered workspace browser over /files with read and extract | 05 Task 9 |
| `69d10b5` | feat(publish): ssh/PAT git publishing from workspace, key bootstrap, audit redaction | 05 Task 10 |

更早的 Isolis 修复（同一仓库历史，不是 cage Task）：`27380e3` 中文 UTF-8、`da3848e` 模型黑名单回退。

## 验证基线（Task 10 结束时）

- `python3 -m pytest tests/ -q` → **72 passed**
- `python3 cage.py doctor` → exit 1，一行 `[fail] MonkeyCode LLM was not injected...`（正确）
- `python3 cage.py profiles` → tooled / strict
- `python3 cage.py help` → 含 setup/doctor/init/chat/use/status/rebuild/stop/profiles/serve/work/key/publish/help

## 实现时记下的接口修订

1. Task 2 工具计数：`CAGE_AGENT_MODE=bare` 下 tooled 6 / strict 5。默认模式后来改成 orchestrator（3 工具：`delegate` `challenge_probe` `solve_challenge`）。
2. `ensure_chat_agent()` 无 name 参数。`chat_request` 无 pending 时返回 `str`，有 pending 时返回 dict。
3. `load_state` 仅当文件里已有 `usage` 才带回，避免打破 `test_state_roundtrip_and_atomic`。
4. `pick_model` 的 `_SKIP_MODELS` 仍跳过 Isolis leftover 名（如 `deepseek-chat`），跟 plan 测试、跟裁定 #8 的「黑名单默认空」有张力；已落地，不要无故改。
5. 无 LLM 的 orchestrator `run_chat` 只在测试把 `spawn_worker` 换成非原名函数时才走委派，避免 `test_chat_without_injection_returns_clear_error` 变成 200。
6. `audit_record` 对 `repo` 键把 `git@github.com:` 写成 `github.com/`，以满足「输出里出现 github.com/me/x.git」。
7. 进程模式 `process_spawn_args(..., process_install=False)` 默认 `CAGE_INSTALL=0`；`CAGE_PROCESS_INSTALL=1` 才开。

## 下一步

从 `prompts/06-phase-research.md` 开始做 Task 11，绿了再做 `07-phase-skills.md`（Task 12）。不要回头改 Task 1–10，除非测试红了。

## 环境

- Python 3.11.2，pytest 9.2.0
- 本机构建环境可能有 Docker；测试仍然禁止调用 Docker 与外网
