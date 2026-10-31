# Cage

隔离的 LangChain agent：宿主机 TUI 负责生命周期，容器（或无 Docker 时的进程）跑 `agent/server.py`。平台通过 `MCAI_LLM_*` 注入网关；本仓库不内置厂商或模型名。

## 三分钟上手

```bash
# 检查本机环境（无 MCAI_LLM_* 时 doctor 以 [fail] 退出 1，这是正确行为）
python3 cage.py doctor

# 准备镜像与网络（不写 daemon.json；无 Docker 时会提示进程模式）
python3 cage.py setup

# 构建并启动隔离 agent（默认名 default，档位 tooled）
python3 cage.py init

# 进入对话
python3 cage.py chat
```

交互式：`python3 cage.py`，提示符 `cage>`。命令：`setup` `doctor` `init` `chat` `use` `status` `rebuild` `stop` `profiles` `help` `serve` `work` `key` `publish`。

## TUI 命令

| 命令 | 作用 |
| --- | --- |
| `setup` | 准备镜像与网络，不写 daemon.json |
| `doctor` | 检查环境；无 LLM 注入时 exit 1 |
| `init [name]` | 构建并启动隔离 agent |
| `chat` | 与当前 agent 对话 |
| `use <name>` | 切换当前 agent |
| `status` | 列出 agent、watchdog 与 `/work` 占用 |
| `rebuild` | 重建镜像与当前 agent |
| `stop [name]` | 停止并移除 agent |
| `profiles` | 列出 tooled / strict 档位 |
| `serve` | 启动宿主 HTTP 接口 |
| `work [name]` | 序号浏览 `/work`：进目录、`r` 读、`x` 抽出、`p` 发布、`q` 退出 |
| `key [name]` | 打印 SSH 公钥，拿去 GitHub 加 Deploy Key |
| `publish` | 把工作区 commit+push 到 `--repo` |
| `help` | 显示帮助 |

## serve 接入

`python3 cage.py serve` 只绑 `127.0.0.1:18079`（`CAGE_SERVE_PORT` 可改，避开 agent 池 18080-18119）。每个响应带 `Access-Control-Allow-Origin: *`。未起 agent 时 `POST /api/chat` 会自动 init。

同源（把前端放到 `CAGE_STATIC_DIR`，serve 同时提供静态文件）：

```javascript
const res = await fetch("/api/chat", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ message: "你好" }),
});
const data = await res.json();
console.log(data.reply);
```

跨域（前端跑在别的源，CORS 常开）：

```javascript
const res = await fetch("http://127.0.0.1:18079/api/chat", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ message: "你好" }),
});
const data = await res.json();
console.log(data.reply);
```

HTTP 契约：

```
GET    /api/health
GET    /api/agents
POST   /api/agents            body {name?, profile?, source?}
GET    /api/agents/<name>
DELETE /api/agents/<name>
POST   /api/chat              body {message, agent?}
GET    /api/history?agent=
```

`serve` 前台占用当前 shell（与 `chat` 相同）。另开终端，或用下面方式常驻。

```bash
# nohup 后台
nohup python3 cage.py serve > ~/.cage/serve.log 2>&1 &
```

systemd 用户单元示例：

```ini
[Unit]
Description=cage serve

[Service]
Type=simple
WorkingDirectory=/path/to/cage
ExecStart=/usr/bin/python3 cage.py serve
Restart=on-failure

[Install]
WantedBy=default.target
```

state 在 `~/.cage/state.json`（`CAGE_HOME` 可覆盖），TUI 与 serve 共用，读写带 `fcntl.flock`。

## 自主档位与预算

`CAGE_AUTONOMY` 取值 `auto`（默认）、`assisted`、`manual`。`install_packages`、`solve_challenge`、`browse`、`git_publish` 在 `assisted`/`manual` 下会返回 `pending`，需 `confirm` 后才执行。`CAGE_ACTION_BUDGET`（默认 500）超额后该 agent 自动降到 `assisted`。

## 子代理

默认 `CAGE_AGENT_MODE=orchestrator`：主代理只持 `delegate`、`challenge_probe`、`solve_challenge`。具体干活派给 worker（深度 1，并发 `CAGE_WORKER_CONCURRENCY`，默认 3）。`bare` 仅调试用。

## 人机验证

`challenge_probe` 把页面分成 `captcha|login|waf|rate_limit|none`。`solve_challenge` 只对 `CAGE_CHALLENGE_HOSTS` 白名单 host 尝试本地 OCR / `CAGE_SOLVER_CMD`；未命中返回 `host not in policy` 并升级给人。不集成付费打码。`CAGE_URL_POLICY=all` 或 `allowlist:a.com,b.com`；LLM 网关恒放行。

## 沙箱与审计

每次工具调用写 `CAGE_AUDIT` JSON 行，宿主收割到 `~/.cage/logs/audit-<agent>.jsonl`。key/token 类参数只留 sha256 前 8 位。`CAGE_WATCHDOG_SECS>0` 时卡住的 worker 会被杀。进程模式默认 `CAGE_INSTALL=0`，显式 `CAGE_PROCESS_INSTALL=1` 才允许装包。

## 工作区在哪/怎么看

| 模式 | 路径 | 生命周期 |
| --- | --- | --- |
| 容器 tooled | 容器内 `/work`（tmpfs） | 容器 stop 即消失 |
| 进程 | `~/.cage/work/<name>` | `stop` 不删 |

`cage> work` 出序号清单：数字进目录或读文件，`r 3` 读，`x 3 ./out.txt` 抽到宿主 cwd 下，`p` 转 `publish`，`q` 退出。

## 上传到我的 GitHub

三种认证：`CAGE_GH_TOKEN`（PAT）、SSH Deploy Key（`key` 打印公钥）、`CAGE_SSH_KEY_B64`。流程：`key default` → GitHub 仓库 Settings → Deploy keys → `publish --repo git@github.com:me/x.git -m "init"`。聊天里也可 `delegate(git_publish)`。`stop` 前必须 `publish` 或 `work x`，否则容器 tmpfs 工作区会丢。

## 进程模式

没有可用 Docker 时自动降级：同一用户下起 `agent/server.py` 子进程，工作目录 `~/.cage/work/<name>`，日志 `~/.cage/logs/<name>.log`。隔离弱于容器（无 namespace/cgroup），仅绑 `127.0.0.1`。

## 已知差异

- 进程模式下 `/work` 落在 `~/.cage/work/<name>`，`stop` 不删除该目录。operator 要清就自己删。
- 容器 tooled 档的 `/work` 是容器内 tmpfs，随容器消失。
- tmpfs 死亡线：容器 stop/重建后 `/work` 不可恢复，先 publish 或抽出。

## 已知限制

- OpenAI 协议路径没有工具；需要 Anthropic 风格网关才会挂工具。
- `serve` 无鉴权，只绑回环，给单用户自用。
- 本机未注入 `MCAI_LLM_BASE_URL` / `MCAI_LLM_API_KEY` 时，`doctor` / `init` / `chat` 会失败。
- 进程模式隔离弱于容器（无 namespace/cgroup）。

## 环境变量

| 变量 | 作用 |
| --- | --- |
| `MCAI_LLM_BASE_URL` | 平台注入的网关地址 |
| `MCAI_LLM_API_KEY` | 平台注入的密钥 |
| `MCAI_LLM_MODEL` | 平台注入的模型名（可空，走探测链） |
| `CAGE_HOME` | 状态目录，默认 `~/.cage` |
| `CAGE_PROFILE` | `tooled` 或 `strict`，默认 tooled |
| `CAGE_AGENT_SOURCE` | `local` 或 git URL，默认 local |
| `CAGE_MODEL` | 强制选用的模型 |
| `CAGE_DEFAULT_MODEL` | 探测链最后一档 |
| `CAGE_BLOCKED_MODELS` | 逗号分隔黑名单，默认空 |
| `CAGE_CONTEXT_TOKENS` | 记忆压缩预算，默认 900000，上限 1000000 |
| `CAGE_MAX_MESSAGE_CHARS` | 单条消息字符上限 |
| `CAGE_MAX_TURNS` | 对话轮次上限 |
| `CAGE_BASE_IMAGE` | Docker 基础镜像，默认 `python:3.12-slim-bookworm` |
| `CAGE_SERVE_PORT` | serve 端口，默认 18079 |
| `CAGE_STATIC_DIR` | serve 静态目录；未设则只提供 API |
| `CAGE_BIND` | 进程模式监听地址，默认 `127.0.0.1` |
| `CAGE_AUTONOMY` | `auto` / `assisted` / `manual`，默认 auto |
| `CAGE_ACTION_BUDGET` | 每日自动动作上限，默认 500 |
| `CAGE_AGENT_MODE` | `orchestrator`（默认）或 `bare` / `worker` |
| `CAGE_WORKER_CONCURRENCY` | worker 并发，默认 3 |
| `CAGE_CHALLENGE_HOSTS` | 允许自动过验证的 host 白名单，逗号分隔 |
| `CAGE_URL_POLICY` | `all` 或 `allowlist:host` |
| `CAGE_SOLVER_CMD` | 外接验证码求解命令，stdin 为 PNG 路径 |
| `CAGE_WATCHDOG_SECS` | worker 超时秒数，0 表示关闭 |
| `CAGE_PROCESS_INSTALL` | 进程模式是否允许装包，默认 0 |
| `CAGE_GH_TOKEN` | GitHub PAT，注入 agent 但不打印 |
| `CAGE_SSH_KEY_B64` | 预置 SSH 私钥（base64） |
| `CAGE_PUBLISH_REPO` | 默认 publish 远程 |
