# 全局硬约束

每一阶段开始前把本文件当作系统提示的一部分。违反任一条即失败。

## 产品边界

Cage 是 Isolis 的自用复刻：宿主机 TUI 管生命周期，容器（无 Docker 则进程）跑 `agent/server.py`。模型只经平台注入的 `MCAI_LLM_BASE_URL` / `MCAI_LLM_API_KEY` / `MCAI_LLM_MODEL`。缺任一则 `doctor`/`init`/`chat` 一行失败，exit 1。禁止换厂商、禁止自造 key、禁止读 `.env`、禁止把 key 写入 state 或日志（诊断只报长度+前 3 位）。

仓库名 `cage`，入口 `python3 cage.py`。TUI 命令名与 Isolis 一致：`setup doctor init chat use status rebuild stop profiles help`。后来加法命令：`serve work key publish`（Task 12 再加 `skills`）。旧命令语义不许改。

环境前缀 `ISOLIS_*` 全部换成 `CAGE_*`。`MCAI_LLM_*` 不动。

## 隔离红线

容器：`--cap-drop ALL` + profile 白名单；tooled 恰好 6 个 cap：`CHOWN DAC_OVERRIDE FOWNER SETUID SETGID SETFCAP`；strict 只读根 + `nobody` + 无 cap；无宿主挂载、无 docker.sock、无 `--privileged`/`SYS_ADMIN`；单端口只绑 `127.0.0.1`；tmpfs `/tmp /work /dev/shm`；`no-new-privileges`。

进程模式一律绑 `127.0.0.1`；`start_new_session=True`，stop 杀进程组；默认 `CAGE_INSTALL=0`。

## 常量（逐字）

| 名 | 值 |
| --- | --- |
| `MAX_TURNS` | 400 |
| `MAX_TOOL_STEPS` | 8 |
| `SHELL_TIMEOUT` | 120 |
| `INSTALL_TIMEOUT` | 1800 |
| `MAX_OUTPUT` | 20000 |
| `CONTEXT_TOKENS` | 900000 |
| `CONTEXT_HARD_LIMIT` | 1000000 |
| `MAX_MESSAGE_CHARS` | 900000 |
| `ARCHIVE_MAX` | 4000 |
| 端口池 | 18080–18119 |
| 容器内端口 | 8080 |
| `SERVE_PORT` | 18079 |
| `NETWORK` | cage-net |
| `DEFAULT_PROFILE` | tooled |
| `CAGE_AUTONOMY` | auto（manual/assisted/auto） |
| `CAGE_ACTION_BUDGET` | 500 |
| `CAGE_WORKER_CONCURRENCY` | 3 |
| `CAGE_WORKER_MAX_TURNS` | 40 |
| `CAGE_AGENT_MODE` | orchestrator（默认；bare 仅调试） |
| `CAGE_SUMMARIZE` | 0 |
| `CAGE_INDEX_MAX_CHARS` | 20000 |
| `CAGE_RISK_LEVEL` | aggressive（operator 选定） |
| `CAGE_PROCESS_INSTALL` | 0 |
| `CAGE_GH_CREATE_REPO` | 0（本期不实现建库） |

破坏性工具集合：`install_packages`、`solve_challenge`、`browse`、`git_publish`。

## 代码与测试

宿主零第三方依赖。名称/常量与 `plans/2026-10-31-cage.md` 的 Interfaces 逐字一致。只创建当前阶段列出的文件。

测试禁止打外网、禁止起 Docker、禁止使用真实 `MCAI_LLM_*`。无 LLM 时 `doctor` 非零退出是正确行为，不要修成成功。

本机 Python 是 3.11.2；计划写 3.12+。继续用 3.11，除非 operator 改口。

输出 ASCII 风格：一行一事实，失败指明下一步。中文走 `safe_text`（surrogateescape + gb18030）。不要 emoji。

state 在 `~/.cage/`（`CAGE_HOME` 可覆盖）：tmp + flush + fsync + `os.replace`；并发 `fcntl.flock`。

## 工作流

无子代理、无并行阶段。spec 与 plan 冲突则停问。不要改 `plans/`。`specs/` 仅当计划 Step 明确要求（Task 4 已改过 §5 树）。不要建 `docs/`。提示词只放 `prompts/`。

提交：`git add` 当前阶段文件后 `feat(scope):` 本地提交。不要 push / force / rebase / amend，除非 operator 明确说上传到 GitHub。

不要把 `isolis.py` 的未提交改动打进 cage 提交。

## 明确不实现

代理池 / 分布式规避封禁。付费打码服务。`CAGE_GH_CREATE_REPO` 建库。登录鉴权多租户。自动写 `/etc/docker/daemon.json`。macOS 适配。预置 Android SDK / JDK / Flutter。
