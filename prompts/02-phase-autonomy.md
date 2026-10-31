# 阶段 02：自主档与子代理（Task 5–6）

状态：**已完成**。核验用。必读 `00-constraints.md`、设计 §15 §20、计划 Task 5–6。

## Task 5 自主档、预算、审计

创建：`cage/autonomy.py`、`tests/test_autonomy.py`。改 `agent/server.py` 的工具闸门与 `/chat` pending。

接口：

- `decide(tool, args, autonomy) -> "run"|"confirm"`
- `effective_autonomy(autonomy, usage, budget, today) -> str`（超额降 assisted）
- `consume(state, today, budget) -> bool`（超预算 True）
- `audit_record(ts, agent, tool, args, output_len, status, duration_ms) -> dict`
- `harvest_audit_lines(lines, path) -> int`

`DESTRUCTIVE_TOOLS = {install_packages, solve_challenge, browse, git_publish}`。

`manual`：一律 confirm。`assisted`：破坏性 confirm。`auto`：直接 run。key/token/cookie/secret/password 参数只留 sha256 前 8 位。

无 LLM 时可用 `_inferred_install("apt 装")` 打样 pending。`/chat` body 可带 `confirm: true`。

红：`cannot import name 'autonomy'`。绿后：

`feat(autonomy): manual/assisted/auto levels, action budget, digested audit records`

落地：`6f1b44c`。

## Task 6 orchestrator / worker

创建：`cage/workers.py`、`tests/test_workers.py`。改 `agent/server.py`、`tests/helpers.py`、`cage/httpapi.py`（`/api/workers`）。

接口：

- `orchestrator_tools() -> ["delegate", "challenge_probe", "solve_challenge"]`
- `worker_allowed_tools(requested)`：与基线交集，去掉 `delegate`
- `clamp_worker_result(result)`：summary≤800、facts≤20、files≤50、errors≤10
- `spawn` / `harvest_worker_result` / `spawn_and_wait`
- `concurrency_limit()` 读 `CAGE_WORKER_CONCURRENCY`
- `watchdog_start(pid, seconds)`：≤0 返回 None；否则 Timer + `os.killpg`

默认 `CAGE_AGENT_MODE=orchestrator`。`/info.mode` 与 `tools` 三项。worker `--worker --task ... --tools ...`：fresh HISTORY，stdout 末行 JSON。

主上下文：10 次派发后 `history_turns <= 10`，`SUMMARIZE_CALLS == []`。深度 1。

测试钩子：无注入时若 `spawn_worker.__name__ != "spawn_worker"` 才走委派，否则仍 500 `not injected`。

`agent_info_via_http()` 无参时自起 Handler。

红：`cannot import name 'workers'`。绿后：

`feat(workers): orchestrator/worker delegation with isolated context and depth-1 limit`

落地：`9ac16d5`。

## 验收

20–21 自主/预算；24–26、31、35 orchestrator 默认、派发有界、worker 不进主 HISTORY。核验：`python3 -m pytest tests/test_autonomy.py tests/test_workers.py tests/test_agent_server.py -q`
