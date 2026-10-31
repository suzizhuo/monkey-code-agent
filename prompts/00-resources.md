# 资源清单

所有实现 cage 需要的材料都在本仓库。不要假设本机还有 Isolis 原仓库路径。

## 权威规格

| 路径 | 内容 | 用法 |
| --- | --- | --- |
| `specs/2026-10-31-cage-design.md` | cage 设计：目标/非目标、7 条改造、§14–§23 扩展、验收 1–51 | 行为对错以它为准 |
| `plans/2026-10-31-cage.md` | Task 1–12 实施计划：文件、接口、失败测试原文、提交信息 | 按 Task 施工以它为准 |
| `README.md` | 中文上手、serve、自主、worker、验证码、沙箱、work、publish、环境变量 | 给人看的入口 |

## 先例（Isolis）

| 路径 | 内容 |
| --- | --- |
| `ISOLIS_PROMPT.md` | Isolis 完整实现提示词（MUST/SHOULD，864 行级规范） |
| `提示词.txt` | Isolis 压缩版（<10k 字），命令/隔离/LLM/HTTP/工具/验收 1–29 |
| `QUICKSTART.md` | Isolis 大白话上手 |
| `isolis.py` | Isolis 宿主 orchestrator 源码（先例实现，cage 不要改它来完成 cage 任务） |
| `agent/install-android-sdk.sh` | Isolis 遗留；cage 设计要求删除预置 Android，不要在 cage 路径里调用 |

## 当前代码（Task 1–10 已落地）

| 路径 | 职责 |
| --- | --- |
| `cage.py` | 薄入口：`configure_stdio` + `cage.cli.main` |
| `cage/__init__.py` | 版本、`CageError` |
| `cage/state.py` | `~/.cage`、flock、原子 state、PROFILES、端口常量 |
| `cage/naming.py` | `sanitize_name` / `source_slug` / `image_tag` / `allocate_port` |
| `cage/runtime.py` | Docker/Process 双后端、`docker_run_args`、`process_spawn_args`、`log_source` |
| `cage/model.py` | `detect_llm` / `pick_model` / `parse_blocked` |
| `cage/httpapi.py` | `serve`：`/api/*`、CORS `*`、静态目录、`/api/workers` |
| `cage/cli.py` | TUI dispatch、11+4 命令、doctor/init/chat/serve/work/key/publish |
| `cage/autonomy.py` | `decide` / `effective_autonomy` / `consume` / `audit_record` / `harvest_audit_lines` |
| `cage/workers.py` | orchestrator 工具集、clamp、spawn、并发、`watchdog_start` |
| `cage/challengerules.py` | `classify_page` / `policy_allows` / `url_allows` |
| `cage/workspace.py` | `render_listing` / `parse_selection` / `extract` / `host_dest_allowed` |
| `cage/publish.py` | `ensure_ssh_key` / `remote_for` / `git_env` / `publish_plan` / `run_publish` |
| `agent/server.py` | 容器/进程 runtime：工具、记忆、orchestrator/worker、challenge、`/files`、`git_publish` |
| `agent/Dockerfile` | 基础镜像 pin；无 Android/JDK 预置 |
| `agent/requirements.txt` | langchain-core / langchain-openai / langchain-anthropic / playwright |
| `tests/` | 全部夹具与单测；入口 `python3 -m pytest tests/ -q` |

## 测试夹具

| 路径 | 用途 |
| --- | --- |
| `tests/helpers.py` | `load_agent`、`http_get`/`http_post`/`http_get_json`、`agent_info_via_http` |
| `tests/fixtures/challenge_*.html` | none/captcha/login/waf/rate_limit 五页 |

## 尚未创建（Task 11–12）

| 路径 | 阶段 |
| --- | --- |
| `cage/research.py` | 06 |
| `cage/playbook.py` | 06 |
| `cage/risk.py` | 06 |
| `tests/test_research.py` | 06 |
| `tests/test_playbook.py` | 06 |
| `tests/fixtures/search.html` | 06 |
| `cage/skills.py` | 07 |
| `tests/test_skills.py` | 07 |
| `tests/fixtures/skills-pool/` | 07 |

## 不要提交

| 路径 | 原因 |
| --- | --- |
| `.monkeycode/` | Agent 工作记忆，不是产品 |
| `.cage/` `.isolis/` | 运行时状态，已在 `.gitignore` |
| `isolis.py` 工作区脏改动 | 与 cage 提交分离 |
| `handoff-package.zip` | 压缩包，`.gitignore` 忽略 zip |
