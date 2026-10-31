# 阶段 01：底座（Task 1–4）

状态：**已完成**。核验用；空仓库重建时按本文件从头做。

必读：`prompts/00-constraints.md`、`specs/2026-10-31-cage-design.md` §1–§13、`plans/2026-10-31-cage.md` Task 1–4。

## 目标

让 `python3 cage.py` 能 doctor / init / chat / serve。无 Docker 降级进程模式。Agent 无 Android/JDK 预置。模型只走 `MCAI_LLM_*`。

## Task 1 骨架

创建：`cage.py`、`cage/__init__.py`、`cage/state.py`、`cage/naming.py`、`tests/test_state.py`、`tests/test_naming.py`。

产出接口：

- `CageError`
- `state.home()` → `CAGE_HOME` 或 `~/.cage`
- `state.state_lock()` → `fcntl.flock` 排他
- `state.load_state()` / `save_state()` 原子写
- `naming.sanitize_name` / `source_slug` / `image_tag` / `allocate_port`

常量：`SERVE_PORT=18079` `CONTAINER_PORT=8080` `NETWORK=cage-net` `DEFAULT_PROFILE=tooled`，PROFILES 两档与隔离表一致。

红：`from cage import state, naming` 失败。绿后提交：

`feat(cage): skeleton, atomic state with flock, naming and port allocation`

落地提交：`f1a5bb3`。

## Task 2 Agent 运行时

修改：`agent/server.py`、`agent/Dockerfile`、`agent/requirements.txt`。创建：`tests/test_agent_server.py`、`tests/helpers.py`。

裁剪：删除 `install_android_sdk`、`/info.android_home`、Dockerfile Android/JDK。保留 `install_packages`。

基线工具 tooled 6：`run_command write_file read_file list_files browse install_packages`。strict 5（无 install）。后来默认 orchestrator 会改 `/info.tools`；本阶段测试用 `CAGE_AGENT_MODE=bare` 断言 6/5。

HTTP：`GET /health` `/info` `/history`，`POST /chat`。记忆：条数/字符/token；默认 `CAGE_SUMMARIZE=0` 只淘汰+索引。

红：agent 模块/工具数不对。绿后：

`feat(agent): provider-neutral runtime with 6-tool baseline, bounded memory, no Android/JDK preinstall`

落地：`37d19d5`。

## Task 3 TUI 与双模式 runtime

创建：`cage/runtime.py`、`cage/model.py`、`cage/cli.py`。`cage.py` 接到 `cli.main`。

`detect_mode()`：`docker info` 成功则 docker，否则 process。`docker_run_args` / `process_spawn_args` 纯函数。`pick_model`：`CAGE_MODEL` > `MCAI_LLM_MODEL` > 目录非黑名单 > `CAGE_DEFAULT_MODEL` > 一行失败。

命令：setup/doctor/init/chat/use/status/rebuild/stop/profiles/help。setup 不写 daemon.json、不起 dockerd。

红：`cannot import name 'runtime'|'model'|'cli'`。绿后：

`feat(cli): TUI with unchanged command surface, dual-mode runtime, platform-injected model`

落地：`e227119`。

## Task 4 serve

创建：`cage/httpapi.py`。改 README、按计划更新 `specs/` §5 树。

`python3 cage.py serve` 绑 `127.0.0.1:18079`，CORS `*`。路由：

```
GET    /api/health
GET    /api/agents
POST   /api/agents
GET    /api/agents/<name>
DELETE /api/agents/<name>
POST   /api/chat
GET    /api/history?agent=
```

未起 agent 时 `POST /api/chat` 调 `ensure_chat_agent`。`CAGE_STATIC_DIR` 同源静态。

红：`cannot import name 'httpapi'`。绿后：

`feat(serve): frontend HTTP API with CORS and static dir; docs and end-to-end smoke`

落地：`dfccb0d`。

## 本阶段验收

- doctor 无注入 → 非零 + 一行说明
- profiles 两档
- help 含 11 命令 + serve
- docker_run_args 满足隔离不变量（`tests/test_runtime.py`）
- `/info.via == mc-direct`，无 `android_home`

核验：`python3 -m pytest tests/test_state.py tests/test_naming.py tests/test_agent_server.py tests/test_cli.py tests/test_runtime.py tests/test_model.py tests/test_httpapi.py -q`
