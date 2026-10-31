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

交互式：`python3 cage.py`，提示符 `cage>`。命令：`setup` `doctor` `init` `chat` `use` `status` `rebuild` `stop` `profiles` `help` `serve`。

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

## 进程模式

没有可用 Docker 时自动降级：同一用户下起 `agent/server.py` 子进程，工作目录 `~/.cage/work/<name>`，日志 `~/.cage/logs/<name>.log`。隔离弱于容器（无 namespace/cgroup），仅绑 `127.0.0.1`。

## 已知差异

- 进程模式下 `/work` 落在 `~/.cage/work/<name>`，`stop` 不删除该目录。operator 要清就自己删。
- 容器 tooled 档的 `/work` 是容器内 tmpfs，随容器消失。

## 已知限制

- OpenAI 协议路径没有工具；需要 Anthropic 风格网关才会挂工具。
- `serve` 无鉴权，只绑回环，给单用户自用。
- 本机未注入 `MCAI_LLM_BASE_URL` / `MCAI_LLM_API_KEY` 时，`doctor` / `init` / `chat` 会失败。

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
