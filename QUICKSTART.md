# Cage 快速上手（大白话版）

这份文档按 2026-10-31 仓库现状写：Task 1–10 已落地（72 个测试绿），入口是 `python3 cage.py`。照着敲就行。

权威规格：`specs/2026-10-31-cage-design.md`、`plans/2026-10-31-cage.md`。分阶段提示词：`prompts/README.md`。

---

## 一、这是个啥

Cage 是隔离的 LangChain agent。

你在终端敲命令，宿主机 TUI 管生命周期；真正干活的 Agent 跑在 Docker 容器里（没有 Docker 时自动改成进程模式）。你跟它聊天，它可以写文件、跑命令、装软件、看网页、过白名单里的人机验证、把 `/work` 推到你的 GitHub。

平台用 `MCAI_LLM_BASE_URL` / `MCAI_LLM_API_KEY` / `MCAI_LLM_MODEL` 注入网关。仓库不内置厂商或模型名。

默认主代理是 **orchestrator**：自己只拿 `delegate`、`challenge_probe`、`solve_challenge` 三件工具，具体读写文件、跑命令派给 worker。

---

## 二、第一次用，三步

打开终端，进到项目目录：

```bash
cd /workspace
```

**第一步：体检**

```bash
python3 cage.py doctor
```

最后一行 `result: ready` 才能往下走。

没有 `MCAI_LLM_BASE_URL` 和 `MCAI_LLM_API_KEY` 时，会打出 `[fail] MonkeyCode LLM was not injected...`，并以退出码 1 结束。这是正确行为，去平台确认注入，不要自己填别的厂商 Key。

没有 Docker 时是 `[warn]`，`init` 会走进程模式，体检可以过。

**第二步：初始化**

```bash
python3 cage.py init
```

第一次构建镜像可能较慢。成功会长这样：

```
agent 'default' ready on 127.0.0.1:18080 (image cage-agent:...)
```

已有同名、同档位、同源的 agent 会复用，打印 `reused`。

**第三步：聊天**

```bash
python3 cage.py chat
```

看到 `you>` 就可以打字。退出输入 `/quit`。

---

## 三、常用命令速查

都是 `python3 cage.py` 后面接一个词。

| 命令 | 干啥 | 什么时候用 |
|---|---|---|
| `doctor` | 检查环境 | 第一次用，或报错时 |
| `setup` | 准备镜像与网络（不写 daemon.json） | 换机器后第一次 |
| `init` | 建一个 Agent 并启动 | 第一次用，或开新的 |
| `chat` | 跟当前 Agent 聊天 | 干活 |
| `status` | 列出 Agent、watchdog、`/work` 占用 | 忘了开了几个 |
| `use 名字` | 切换当前 Agent | 多个 Agent 时 |
| `stop 名字` | 关掉并移除 | 不用了 |
| `rebuild` | 重做镜像并重建当前 Agent | 改了 `agent/` 之后 |
| `profiles` | 看 tooled / strict | 想知道权限 |
| `serve` | 宿主 HTTP 接口，绑 `127.0.0.1:18079` | 给前端调 |
| `work` | 序号浏览工作区 | 看 `/work`、抽出、转发布 |
| `key` | 打印 SSH 公钥 | 去 GitHub 加 Deploy Key |
| `publish` | 把工作区 commit+push | 上传到你的仓库 |
| `help` | 看帮助 | 忘了命令 |

不带参数进交互模式：

```bash
python3 cage.py
```

提示符是 `cage>`，上面这些命令直接敲。

---

## 四、怎么聊天

```bash
python3 cage.py chat
```

没 `init` 过会自动建。进去先打两行状态，默认 orchestrator 长这样：

```
chat: default | profile tooled | hostname ... | model ... | llm_host ... | via mc-direct
tools: delegate, challenge_probe, solve_challenge
```

`via` 必须是 `mc-direct`，否则会报错并拒绝聊。

然后 `you>` 说人话即可：

```
you> 帮我在 /work 写一个按文件大小排序的 Python 脚本
```

主代理会 `delegate` 给 worker 去 `write_file` / `run_command`。

| 输入 | 作用 |
|---|---|
| 普通文字 | 发给 Agent |
| `/back` | 退回 `cage>` |
| `/quit` 或 `/exit` | 退出整个程序 |
| `/help` | 看这几个指令 |

破坏性工具（`install_packages`、`solve_challenge`、`browse`、`git_publish`）在 `assisted` / `manual` 下会先 `pending`。屏幕会问 `type y to confirm`，输入 `y` 才执行。

每次 `chat` 连同一个 Agent，它记得上下文。想清空：`stop` 再 `init`。

**默认上限（写在程序里的）：**

| 限制 | 数值 | 意思 |
|---|---|---|
| 记忆轮数 | 400 | `CAGE_MAX_TURNS`，更早的丢掉 |
| 单次工具步数 | 8 | 一次提问最多连调 8 次工具 |
| 单条命令超时 | 120 秒 | worker 里 `run_command` |
| 装软件超时 | 30 分钟 | `install_packages` |
| 单次返回长度 | 20000 字符 | 超了截断 |
| 聊天 HTTP 超时 | 120 秒 | 宿主机 `chat_request` |
| token 预算 | 900000 | 超了压缩；硬上限 1000000 |
| 单条消息字符 | 900000 | 超了留头尾、中间截 |
| 归档条数 | 4000 | `/history` 里的 ARCHIVE |

改环境变量后必须 `stop` 再 `init` 才会进容器/进程。`rebuild` 不会单独刷新已有环境变量。

```bash
export CAGE_CONTEXT_TOKENS=200000
export CAGE_MAX_TURNS=100
export CAGE_MAX_MESSAGE_CHARS=200000
```

关掉自动压缩：`export CAGE_CONTEXT_TOKENS=0`（只留 400 轮阀门）。

看压缩次数和完整历史：

```bash
curl -s http://127.0.0.1:18080/info
curl -s http://127.0.0.1:18080/history | python3 -m json.tool | less
```

端口以 `status` 为准，默认第一个是 18080。

---

## 五、怎么初始化

```bash
python3 cage.py init
```

默认名字 `default`，档位 `tooled`，自主档 `auto`，模式 `orchestrator`。

自己起名：

```bash
python3 cage.py init myproject
```

名字只留字母、数字、下划线、短横线，最长 40。被改了会有 `notice: agent name sanitized to '...'`。

指定档位 / 模型 / 自主档：

```bash
python3 cage.py init safe --profile strict
python3 cage.py init --model 某个模型名
python3 cage.py init --autonomy assisted
```

一次开几个：

```bash
python3 cage.py init backend
python3 cage.py init frontend
python3 cage.py status
python3 cage.py use backend
```

每个 Agent 独立，端口从 18080 到 18119，共 40 个。`serve` 占用 18079，不进这个池。用完 `stop`。

成功行：`agent '<name>' ready on 127.0.0.1:<port> (image ...)`。

---

## 六、工作区在哪、怎么看

| 模式 | 路径 | `stop` 之后 |
|---|---|---|
| 容器 tooled | 容器内 `/work`（tmpfs） | 一起消失 |
| 进程 | `~/.cage/work/<name>` | 目录还在，要自己删 |

**容器名** = `cage-` + Agent 名，例如 `cage-default`。

推荐用 TUI，别手搓 docker：

```bash
python3 cage.py work
```

`work>` 提示符：

| 输入 | 作用 |
|---|---|
| 数字 | 进目录，或读那个文件 |
| `r 3` | 读第 3 项 |
| `x 3 ./out.txt` | 抽到**当前工作目录之下**（出 cwd 会拒） |
| `p` | 转 `publish` |
| `q` | 退出 |

聊天里也可以让它 `list_files` / `read_file`（由 worker 执行）。

容器模式想进容器看：

```bash
docker exec -it cage-default ls -la /work
```

**死亡线：** 容器 `stop` / `rebuild` 后 tmpfs `/work` 不可恢复。先 `publish` 或 `work` 里 `x` 抽出。

进程模式日志：`~/.cage/logs/<name>.log`。状态：`~/.cage/state.json`（`CAGE_HOME` 可改）。

---

## 七、权限档位和装软件

| 档位 | 装软件 | 内存 / CPU / 用户 | 适合 |
|---|---|---|---|
| `tooled`（默认） | 能 | 4g / 2.0 / root | 正常开发 |
| `strict` | 不能 | 512m / 1.0 / nobody，只读根 | 不信任的代码 |

```bash
python3 cage.py profiles
python3 cage.py init safe --profile strict
```

tooled 下在聊天里说人话即可（主代理会派给 worker 的 `install_packages`）：

```
you> 用 apt 装 imagemagick
you> 用 pip 装 requests
you> 用 npm 装 typescript
```

没有 `install_android_sdk`，镜像也不预装 Android/JDK。要 Flutter/SDK/JDK，用 `install_packages` 自装。

进程模式默认 `CAGE_INSTALL=0`，装包会被拒。显式打开：

```bash
export CAGE_PROCESS_INSTALL=1
```

然后重新 `init`。

装的东西都在容器或该进程工作目录里，`stop` 容器档会丢。

---

## 八、主代理、worker、自主档

默认 `CAGE_AGENT_MODE=orchestrator`。主代理工具只有三项：`delegate`、`challenge_probe`、`solve_challenge`。worker 默认有 `run_command`、`write_file`、`read_file`、`list_files`、`browse`，tooled 再加 `install_packages`；`git_publish` 在 worker 白名单里。

深度 1：worker 不能再 `delegate`。并发默认 `CAGE_WORKER_CONCURRENCY=3`。单 worker 轮次默认 `CAGE_WORKER_MAX_TURNS=40`。

只调试才用裸机：

```bash
export CAGE_AGENT_MODE=bare
```

这时主代理直接拿那 6 个基线工具。这不是正常用法。

自主档 `CAGE_AUTONOMY`（也可用 `init --autonomy`）：

| 档 | 行为 |
|---|---|
| `auto`（默认） | 直接跑 |
| `assisted` | 破坏性工具要确认 |
| `manual` | 所有工具要确认 |

`CAGE_ACTION_BUDGET` 默认 500，超额后该 agent 当天降到 `assisted`。

---

## 九、人机验证

`challenge_probe` 把页面分成 `captcha` / `login` / `waf` / `rate_limit` / `none`。

`solve_challenge` 只对 `CAGE_CHALLENGE_HOSTS` 白名单里的 host 走本地 OCR 或 `CAGE_SOLVER_CMD`。未命中返回 `host not in policy`，升级给你。不集成付费打码。

```bash
export CAGE_CHALLENGE_HOSTS=example.com,other.com
export CAGE_URL_POLICY=allowlist:example.com
```

`CAGE_URL_POLICY=all` 时非 LLM 网关的普通 URL 都允许探测；LLM 网关 host 恒放行。

---

## 十、换模型、换源

优先级：`--model` / `CAGE_MODEL` > `MCAI_LLM_MODEL` > 网关目录里非黑名单 > `CAGE_DEFAULT_MODEL`。

```bash
python3 cage.py init --model 某个模型名
export CAGE_MODEL=某个模型名
export CAGE_DEFAULT_MODEL=兜底模型名
export CAGE_BLOCKED_MODELS=逗号分隔
```

名字不认识时会从目录里挑，不会直接崩。

默认用仓库里的 `agent/`。也可以拉别人的 Git：

```bash
python3 cage.py init myagent --source https://github.com/某人/某个仓库.git
```

会浅克隆到 `~/.cage/agent-src/<slug>/`，要有 `Dockerfile`，再单独打镜像。再跑会 `git pull --ff-only`。回到本地：`--source local`。

别人的 Dockerfile 想干啥就干啥，用之前先看一眼。

---

## 十一、看源码（Cage 自己的）

| 路径 | 里面是啥 |
|---|---|
| `cage.py` | 入口 |
| `cage/` | 宿主 TUI、runtime、state、work、publish |
| `agent/server.py` | 容器/进程里的 Agent |
| `agent/Dockerfile` | 镜像 |
| `agent/requirements.txt` | langchain / playwright 等，只给 agent |
| `README.md` | 中文上手 + 环境变量全表 |
| `QUICKSTART.md` | 本文件 |
| `prompts/` | 分阶段实现提示词 |
| `plans/2026-10-31-cage.md` | Task 1–12 计划 |
| `specs/2026-10-31-cage-design.md` | 设计 |

宿主 `cage.py` + `cage/` 只用标准库。仓库里仍有 `isolis.py`、`ISOLIS_PROMPT.md`，那是旧入口，日常用 `cage.py`。

---

## 十二、把结果推到 GitHub

三种认证：`CAGE_GH_TOKEN`（PAT）、`key` 打出来的 SSH Deploy Key、`CAGE_SSH_KEY_B64`。

```bash
python3 cage.py key default
# 把公钥贴到 GitHub 仓库 Settings → Deploy keys
python3 cage.py publish --repo git@github.com:me/x.git -m "init"
```

`--branch` 默认 `main`。没 `--repo` 时读 `CAGE_PUBLISH_REPO`。空仓库地址会拒绝，防止推到不明处。

聊天里 orchestrator 经 `delegate` 调 worker 的 `git_publish`。

当前 `publish` 命令读的是宿主 `~/.cage/work/<name>`（进程模式工作区）。容器 tmpfs 要先 `work` 抽出，或走 agent 里的 `git_publish`。

---

## 十三、serve 给前端

```bash
python3 cage.py serve
```

只绑 `127.0.0.1:18079`（`CAGE_SERVE_PORT` 可改）。CORS 常开。没 agent 时 `POST /api/chat` 会自动 init。无鉴权，单用户自用。

```
GET    /api/health
GET    /api/agents
POST   /api/agents            body {name?, profile?, source?}
GET    /api/agents/<name>
DELETE /api/agents/<name>
POST   /api/chat              body {message, agent?}
GET    /api/history?agent=
```

前端静态目录：`CAGE_STATIC_DIR`。`serve` 占当前 shell，另开终端再 `chat`。

---

## 十四、进程模式

`docker info` 不通就自动进程模式：同一用户下起 `agent/server.py`，工作目录 `~/.cage/work/<name>`，日志 `~/.cage/logs/<name>.log`，只绑 `127.0.0.1`。没有 namespace/cgroup，隔离弱于容器。

`setup` 发现没 Docker 会继续，并打印 `setup will continue in process mode`。它不改 `daemon.json`、不起 dockerd。Hub 不通只警告，镜像源要自己配。

---

## 十五、常见操作

```bash
python3 cage.py status
```

```
* default              running  127.0.0.1:18080  tooled     xxx
  backend              running  127.0.0.1:18081  strict     xxx
watchdog: 0s
work: ...
```

`*` 是当前。下面还有 isolation 摘要、context budget、mode。

```bash
python3 cage.py use backend
python3 cage.py stop backend
python3 cage.py rebuild
```

`stop` 不写名字就关当前的。容器档 `/work` 会没。`rebuild` 会强制重建镜像再 `init`，数据同样会丢。

换机器：

```bash
python3 cage.py setup
python3 cage.py doctor
python3 cage.py init
```

进程模式还需要 agent 依赖时：`python3 cage.py setup --venv`（建 `~/.cage/venv` 并装 `agent/requirements.txt`）。

---

## 十六、出问题了

**`MonkeyCode LLM was not injected`**

```bash
echo ${MCAI_LLM_BASE_URL:+有}
echo ${MCAI_LLM_API_KEY:+有}
```

都没输出就是没注入。找平台，不要填别的厂商 Key。

**`docker CLI not found` / daemon 没起来**

`doctor` 给 `[warn]`，`init` 走进程模式。想用容器再装 Docker。

**`agent 'xxx' failed health check`**

```bash
docker logs cage-default
# 或进程模式
less ~/.cage/logs/default.log
```

**`no free port in 18080-18119`**

`status` 看哪些不用了，`stop` 掉。

**`host not in policy`**

这个站不在 `CAGE_CHALLENGE_HOSTS` 里，验证码不会自动过。把 host 加进白名单再 `stop`/`init`，或自己过。

**聊天里工具只有三项**

这是 orchestrator 默认。干活靠 `delegate`。要主代理自己动手：`CAGE_AGENT_MODE=bare` 后重建（仅调试）。

**OpenAI 协议路径没有工具**

需要 Anthropic 风格网关才会挂工具。这是已知限制。

**完全搞不清**

```bash
python3 cage.py doctor
python3 cage.py help
```

---

## 十七、现状与未做

已落地：骨架、agent 六工具、TUI 双模式、serve、自主档、orchestrator/worker、人机验证、沙箱审计、`work`、`key`/`publish`。测试：`python3 -m pytest tests/ -q`（当前 72 passed）。

还没做：研究回路 / playbook / 风险档（Task 11）、Skill 池（Task 12）。`init --risk` 参数解析了，矩阵和扫描还没接上。提示词在 `prompts/06-phase-research.md`、`prompts/07-phase-skills.md`。

本环境 Python 3.11.2；计划写的是 3.12+，按 3.11 继续用。

---

## 十八、命令一句话

- `doctor` —— 先体检（没注入 LLM 会 fail，这是对的）
- `setup` —— 镜像和网络
- `init` —— 开个 Agent
- `chat` —— 跟它说话
- `status` —— 看看有啥
- `use` —— 换 Agent
- `stop` —— 关掉
- `rebuild` —— 重做
- `profiles` —— 权限档
- `work` —— 看/抽工作区
- `key` / `publish` —— 推到 GitHub
- `serve` —— 给前端 HTTP
- `help` —— 忘了就看

顺序：`doctor` → `setup` → `init` → `chat`。容器停之前先 `publish` 或 `work x`。
