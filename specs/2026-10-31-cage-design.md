# cage 设计文档 — 云环境自用 Agent 部署套件

- 日期：2026-10-31
- 状态：已获 operator 口头批准（对话版设计），待 builder 复读检查后进入计划与实现
- 先例（prior art）：`/Users/yin/Desktop/当前物/monkey-code-agent`（Isolis，1474 行宿主 orchestrator + 927 行容器 runtime）

## 1. 意图与为什么

operator 在研究 Isolis 的「从 0 部署 Agent」模式后，决定**复刻一套自己的**，用于自己的云环境。核心诉求：

1. 命令形态不变：TUI 的 11 条命令与容器操作的映射关系保持与 Isolis 一致，不重新学一套。
2. 模型走平台注入：云环境平台（MonkeyCode / DSH / GPT / Mini M3 等，随环境变）通过环境变量提供 LLM 接入，套件只透传、不配置、不存储 key。
3. 双入口：命令行聊天（继承）+ `serve` HTTP 接口（新增，给前端调）。
4. 能跑容器就跑容器，跑不了容器就降级成进程模式，命令面不变。

成功标准：在云环境里三分钟内从零起到一个能对话、能干活（写文件/跑命令/装环境）的 Agent；前端能通过本地 HTTP 拿到同样能力。

## 2. 目标 / 非目标 / 反目标

**目标（Goals）**

- G1 全量继承 Isolis 的 TUI 命令面与容器隔离契约（详见 §6、§9）。
- G2 `MCAI_LLM_*` 透传 + `/models` 目录探测 + OpenAI/Anthropic 协议自动协商。
- G3 三维记忆管理（条数 / 单条字符 / token 预算）+ `[compressed history]` 自动压缩。
- G4 `serve` 命令：宿主机 HTTP 前端接口，复用自愈逻辑。
- G5 无 Docker 时自动降级为进程模式，`status/stop/chat/use` 语义不变。
- G6 只删预置 Android/JDK 工具链，保留 `install_packages`，其余 Isolis 能力全保留。

**非目标（Non-goals）**

- 不做登录 / 鉴权 / 多租户（部署在 operator 自己的云环境，边界由环境保证）。
- 不做 daemon 自启、不自动修改宿主机系统文件（`/etc/docker/daemon.json` 等）。
- 不做 macOS 适配（目标云环境为 Linux；保留 docker 缺失提示）。
- 不做 Android SDK / JDK / Flutter 预置（交给 Agent 用 `install_packages` 自装）。
- 不追求多网关并发/负载均衡；模型选择沿用单模型 + 兜底模型机制。

**反目标（Anti-goals，出现即失败）**

- 为了"支持某厂商"把模型名、base_url、协议细节写死在代码里。
- 把平台 key 落到磁盘（state 文件、日志、`.env`）。
- 命令面分裂：Docker 模式和进程模式给出不同的命令/输出语义。
- TUI 命令改名、改选项、改语义（operator 明确要求"指令不想发生太大改变"）。

## 3. 约束

- C1 **模型来源唯一**：仅平台注入的 `MCAI_LLM_BASE_URL` / `MCAI_LLM_API_KEY` / `MCAI_LLM_MODEL`。缺失即失败并给出明确一行报错；不允许换厂商、不允许自造 key、不读 `.env`。
- C2 **宿主机控制层零第三方依赖**：只用 Python 标准库 + `docker` CLI 子进程（与 Isolis 一致）。
- C3 **云环境现实未知项**：可能非 root、Docker 不一定可用、端口可能受限、外网可能要走代理。所有此类不确定都走「探测 → 可用就用，不可用降级并说明」，不假设、不硬写系统文件。
- C4 中英文混排安全：模型路由给的中文内容含坏字节时必须可显示（沿用 `safe_text` 的 surrogateescape + gb18030 兜底）。
- C5 仓库语言与文档全中文；标识符、命令、日志保持英文原样。

## 4. 方案定型（operator 批准的 A 底座 + 7 条改造）

**A 底座**：Isolis 的全量结构（宿主 orchestrator + 容器 runtime + Dockerfile + 文档）原样继承。

| # | 改造 | operator 决定 |
|---|---|---|
| 1 | 改名不改命令：新仓库名 `cage`，主程序 `cage.py`；11 条 TUI 命令一字不改 | 采纳（名字可再议） |
| 2 | 新增唯一命令 `serve`：宿主机 HTTP 前端接口 | 采纳 |
| 3 | Docker 探测 + 降级进程模式，state 记 `mode` 字段 | 采纳 |
| 4 | 删 daemon 自启、删自动写 `/etc/docker/daemon.json`（只检查+告知） | 采纳 |
| 5 | 模型黑名单改为默认空 + `MCAGE_BLOCKED_MODELS` 环境变量配置，不再写死平台模型名 | 采纳 |
| 6 | 删预置 Android/JDK 工具链：删 `install_android_sdk` 工具、`install-android-sdk.sh`、Dockerfile Android 段；保留 `install_packages`（apt/pip/npm）让 Agent 自装 Flutter/SDK/JDK | 采纳（operator 明确删除） |
| 7 | 文档全中文重写：README + 云环境三步上手 + 前端接入示例 | 采纳 |

**连带动刀（第 6 条的后果，builder 必须执行）**

- `agent/Dockerfile`：删 `ANDROID_HOME / ANDROID_SDK_ROOT / JAVA_HOME / GRADLE_USER_HOME` 与 Android PATH 注入、删预建 `/opt/android-sdk`；不预置 JDK。
- `agent/server.py`：删 `install_android_sdk` 工具；`/info` 删 `android_home` 字段；工具数 tooled 7→6、strict 5。
- tooled 档位 6 个 capability（CHOWN/DAC_OVERRIDE/FOWNER/SETUID/SETGID/SETFCAP）**保留**——`install_packages` 的 apt/pip/npm 依赖它们。
- `agent/install-android-sdk.sh`：整个文件删除。

## 5. 架构与文件清单

```
├── cage.py                  # 宿主机入口：stdio + 派发到 cage.cli / serve
├── cage/
│   ├── __init__.py
│   ├── state.py             # ~/.cage/state.json + flock
│   ├── naming.py            # 名字、镜像 tag、端口池
│   ├── runtime.py           # Docker / 进程双模式
│   ├── model.py             # MCAI_LLM_* 探测与模型链
│   ├── cli.py               # TUI 命令
│   └── httpapi.py           # serve：127.0.0.1 HTTP API + 静态目录
├── agent/
│   ├── server.py            # 容器/进程内 runtime：HTTP :8080 + LangChain + 工具 + 记忆
│   ├── Dockerfile           # 镜像构建（无 Android/JDK 预置）
│   ├── requirements.txt     # langchain-core / langchain-openai / langchain-anthropic / playwright
│   └── .dockerignore
├── specs/2026-10-31-cage-design.md
└── README.md                # 中文：云环境三步上手 + serve 接入示例
```

数据流：

```
前端浏览器 ──HTTP──> cage.py serve（宿主，仅绑 127.0.0.1:18079；`CAGE_SERVE_PORT` 可改，**避开 agent 端口池 18080-18119**）
                            │
命令行 chat ──TUI──> cage.py ──docker run──> agent/server.py :8080
                            │                        │
                            └──MCAI_LLM_* 透传──────> 平台网关（MonkeyCode/DSH/GPT/Mini M3...）
                                                     （OpenAI/Anthropic 协议自动协商）
```

## 6. TUI 命令契约（与 Isolis 一致，除 `serve` 外零改动）

```
python3 cage.py
cage> setup | doctor | init [name] | chat | use <name> | status | rebuild | stop [name] | profiles | help | serve
one-shot: python3 cage.py <上述命令> [args]
chat 内: 文本 -> POST /chat | /back | /quit | /help
选项（init/setup/rebuild）: --profile strict|tooled（默认 tooled） | --source <local|git-url>（默认 local） | --model <name>
环境: CAGE_PROFILE, CAGE_AGENT_SOURCE, CAGE_MODEL, CAGE_DEFAULT_MODEL,
      CAGE_CONTEXT_TOKENS, CAGE_MAX_MESSAGE_CHARS, CAGE_MAX_TURNS, CAGE_BLOCKED_MODELS
```

> 环境变量命名：Isolis 用 `ISOLIS_*`，本套件改前缀 `CAGE_*` 以避免与先例共存时串味；`MCAI_LLM_*` 三个变量名保持不动（平台注入的约定名）。

新增 `serve` 的 HTTP 契约（宿主机侧，仅绑 `127.0.0.1`）：

```
GET  /api/health          -> {ok, agents:[...], current}
GET  /api/agents          -> 列表（name/port/mode/profile/model/status）
POST /api/agents          -> 起一个 agent（body: {name?, profile?, source?}）—— 复用 init 逻辑
GET  /api/agents/<name>   -> 单agent详情 + /info 透传
POST /api/chat            -> body {message, agent?}；内部走 ensure_chat_agent + POST 容器 /chat，返回 reply+tools+memory
GET  /api/history?agent=  -> 透传容器 /history
DELETE /api/agents/<name> -> 等价 stop
```

`serve` 与 TUI 共用同一 state 文件与自愈逻辑；容器未起时 `POST /api/chat` 自动 init。

## 7. 容器运行时契约（继承 Isolis §8，按第 6 条裁剪）

- 端口：容器内 8080；宿主映射 127.0.0.1:18080-18119。
- 端点：`GET /health`、`GET /info`、`GET /history`、`POST /chat`；每个 JSON 回包带 `{agent, hostname, pid, cwd}`。
- `/info` 字段：framework/source/profile/install/model/llm_host/via=`mc-direct`/injected/protocol/tools[]/workspace + 记忆六元组（history_turns/history_tokens/context_tokens/context_hard_limit/max_message_chars/max_turns/compress_batch/compressed_turns/archived_turns）。**无 android_home**。
- 工具集（6 tooled / 5 strict）：`run_command`（`/bin/sh -c` 非登录 shell，cwd=/work，stdout+stderr+exit，120s）、`write_file`/`read_file`/`list_files`（realpath 前缀校验，越界拒）、`browse`（headless Chromium，可截图落 /work）、`install_packages`（apt/pip/npm，strict 拒绝，1800s）。
- 记忆：MAX_TURNS 400 / MAX_TOOL_STEPS 8 / SHELL_TIMEOUT 120 / INSTALL_TIMEOUT 1800 / MAX_OUTPUT 20000 / MAX_MESSAGE_CHARS 900000 / ARCHIVE_MAX 4000。（`CONTEXT_TOKENS/CONTEXT_HARD_LIMIT/COMPRESS_BATCH/SUMMARY_*` 见 §20 v2 修订后的定位）
- system prompt：`CAGE_SYSTEM_PROMPT` 可覆盖，否则内置（声明容器隔离、无宿主 FS/兄弟容器/docker.sock、跟随用户语言、先跑命令再报成功、tooled 可自装环境）。

## 8. 模型与协议协商

- 透传：`MCAI_LLM_BASE_URL` / `MCAI_LLM_API_KEY` / `MCAI_LLM_MODEL` 原样进容器；宿主任何输出只报 key 长度 + 前 3 位。
- 模型选择优先级：`CAGE_MODEL` > `MCAI_LLM_MODEL` > 目录中第一个未被黑名单命中者 > `CAGE_DEFAULT_MODEL` > 全部落空则以 C1 的一行报错失败。
- **不设"平台前缀"档、不设内置模型常量**：先例里写死的 `MODEL_PREFIX="monkeycode-pro/"` 与 `DEFAULT_MODEL="monkeycode-pro/gpt-5.6-terra"` 属平台知识硬编码，与反目标冲突，本期去除（Builder 检查 #8）。黑名单 `CAGE_BLOCKED_MODELS`（默认空、逗号分隔）命中即跳过而非失败。
- 协议协商：探 `/chat/completions`（1 token / 6s），404/405 → Anthropic 客户端（剥尾 `/v1`），否则 OpenAI；进程内缓存；404/405 即时翻转重试；403 且有兜底模型时换模型重试。两端参数一致：timeout 60s、max_retries 1、temperature 0.4、Anthropic max_tokens 4096。
- **保留并文档化先例的已知缺口**：OpenAI 协议路径不绑定工具（Isolis 工具循环仅在 Anthropic 路径启用）。本套件同样实现，但在 README「已知限制」明写，后续可作增强项，不在本期范围。

## 9. 隔离契约

公共不变量（所有 profile、两种模式共通语义）：

- 专用 bridge `cage-net`（icc=false），alias=agent 名；容器间不可互访。
- 单端口，仅绑 127.0.0.1。
- tmpfs `/tmp` `/work` `/dev/shm`；无宿主机挂载、无 docker.sock。
- `no-new-privileges:true`；`--cap-drop ALL` + profile 白名单；pids/memory/cpus 三限。
- 一容器一 agent。绝不用 `--privileged` / `SYS_ADMIN` / docker.sock。

| profile | root fs | user | caps | mem/pids//work//tmp//shm | install |
|---|---|---|---|---|---|
| strict | 只读 | nobody | 无 | 512m/128/512m/64m/64m | 关 |
| tooled（默认） | 可写 | 0:0 | CHOWN,DAC_OVERRIDE,FOWNER,SETUID,SETGID,SETFCAP | 4g/512/2g/512m/512m | 开 |

**进程降级模式**（ Docker 不可用时的等价退化，非等价安全）：同端口段、同 state schema（多 `mode:"process"`）、同 `/chat` 契约；隔离降级为：非 root 运行 + 工具层 `/work` 目录白名单 + `install_packages` 仅在显式 `--profile tooled` 时开放，且 README 明示"进程模式隔离弱于容器模式，云环境本身可信时使用"。

## 10. 交付里程碑

- **M1 控制面**：`cage.py` 的 doctor/setup/init/status/use/stop/rebuild/profiles/help + 端口分配 + state 原子写 + Docker/进程双模式 + `MCAI_*` 探测。可在云环境跑通 `init` + `status`。
- **M2 容器运行时**：`agent/` 全量 + `/chat` 端到端 + 工具循环 + 压缩。命令行 `chat` 可用。
- **M3 serve 与收尾**：`serve` HTTP + README + 已知限制。

## 11. 验收标准（自 Isolis 29 条裁剪 + 新增）

1. `doctor` 无注入时 exit 非零且一行说明；有注入 exit 0。
2. `profiles` 输出两档且数值正确（tooled install on / strict off）。
3. `init` 默认起 tooled：容器 root、恰好 6 caps、`install:true`、6 工具；strict：nobody、read-only、无 caps、5 工具。
4. `init --profile strict` 后 `install_packages` 被拒且容器不变。
5. tooled 下 `install_packages apt <pkg>` 成功且 `run_command` 可执行产物（**本期验收用非 JDK 包，如 `tree`/`imagemagick`，顺带证明"Agent 自配环境"路线**）。
6. 端口 18080-18119 三重避让；state 原子写在中断后不损坏。
7. 同 profile+source 二次 `init` 复用；改 profile/source 重建并更新 state。
8. `chat` 端到端中文往返（含 GBK 坏字节场景）。
9. `MAX_TURNS` 超出后仍可应答且 `history_turns` 有界；`CAGE_CONTEXT_TOKENS=0` 关闭压缩且轮数上限仍生效；预算 >1e6 clamp + notice。
10. `serve` 下 `POST /api/chat` 未起 agent 时自动 init 并返回 reply；`GET /api/history` 与容器一致。
11. 无 Docker 的机器 `init` 走进程模式，`status/stop/chat` 语义与容器模式一致。
12. TUI 11 命令名、选项、输出格式与 Isolis 逐条比对一致（diff 只允许 `serve` 新增与 `ISOLIS_`→`CAGE_` 前缀替换）。

## 12. 留给 builder 的决定项（不影响验收，builder 自定并在文档记录）

- 仓库对外名字最终是否叫 `cage`（operator 未最终定名，先用 cage）。

## 13. Builder 复读检查结果与裁定（2026-10-31）

复读 builder 提出 11 个问题，逐条裁定。**全部为 builder 调用（非 operator 决定），operator 可逐条否决**；被否决项回到 §4 重议。

| # | 问题 | 裁定 |
|---|---|---|
| 1 | `serve` 与 TUI 能否同时跑、state 如何仲裁 | `serve` 前台运行（同 `chat` 占据当前 shell；要多开就另开终端或用 `nohup`/systemd，README 给出示例）。**state 文件加排他锁（`fcntl.flock`）**：所有 read-modify-write 与 `allocate_port` 在锁内完成，双进程不再互相覆盖 |
| 2 | 进程模式：子进程还是线程 | **子进程**。`start_new_session` 脱离、stdout/stderr 落 `~/.cage/logs/<name>.log`、进程组可杀——否则 `stop`/`rebuild` 杀不掉跑着 1800s 安装的子任务，违反反目标"命令面不变"。存活语义与 `docker run -d` 对齐：cage 退出后 agent 继续跑 |
| 3 | 进程模式宿主依赖（Python/langchain/Chromium）缺失怎么办 | `doctor` 逐项检查并报告；`setup` 提供 `.cage/venv` 本地虚拟环境安装（触碰的只有 `~/.cage/`，不写系统文件）；依赖不齐时进程模式 `init` 一行报错并指名 `setup`。Chromium 缺失只影响 `browse`（按调用失败），`doctor` 出 `[warn]` |
| 4 | 进程模式绑 `0.0.0.0` 还是 `127.0.0.1` | **一律 `127.0.0.1`**。容器里的 `0.0.0.0` 之所以安全，全靠宿主侧"仅回环映射"那一层；进程模式没有这层映射，绑全网卡等于把无鉴权的 `run_command` 暴露给公网 |
| 5 | 记录的 `mode` 与现实不符谁赢 | 每次 `init`/`chat` 自愈都重新探测 Docker。探测结果与记录不符 → 复用既有的 "profile or source changed" 分支：打印 notice、按新模式重建、尽量沿用同名同端口。`rebuild` 为显式迁移路径。**探测不静默改 state**，只在重建时写 |
| 6 | state 放 CWD 还是 HOME | **`~/.cage/`**（`CAGE_HOME` 可覆盖）。先例的 CWD 相对路径会让 `nohup` 起的 `serve` 与 TUI 看到两个世界，非技术用户只会看到"agent 不见了" |
| 7 | `serve` 自身端口与 agent 端口池冲突 | **`serve` 固定 18079**（池下方一档，`CAGE_SERVE_PORT` 可改），`allocate_port` 显式排除它；§5 数据流图已照此修 |
| 8 | "平台前缀"与"内置模型常量"从哪来 | **两档删除**，选择链退化为 `CAGE_MODEL` > `MCAI_LLM_MODEL` > 目录首个非黑名单 id > `CAGE_DEFAULT_MODEL` > C1 一行报错。§8 已照此修 |
| 9 | 进程模式 `/work` 在哪、`stop`/`rebuild` 擦不擦 | `~/.cage/work/<name>/`；**不擦**——容器模式 tmpfs 消亡是临时盘的物理属性，不是命令语义；真目录被命令静默删除在云机器上是错误的默然。差异写进 README「已知差异」一节，用户想清就自己删目录 |
| 10 | 前端同源还是跨域 | **两者都成立**：`serve` 提供一个静态目录（`CAGE_STATIC_DIR`，放个 `index.html` 就能同源访问），同时**始终返回宽松 CORS 头**（无鉴名单用户自用）。README 给同源与跨域两份 fetch 示例 |
| 11 | Docker 基础镜像可否选、tag 是否含它 | **`CAGE_BASE_IMAGE`（默认 `python:3.12-slim-bookworm`）必须进镜像 tag 摘要**——否则换基础镜像后 `setup`/`rebuild` 会静默复用旧镜像，白改 |

## 14. 能力扩展总览（operator 追加，四项诉求 → 五节契约）

| 诉求 | 契约 |
|---|---|
| 1 全面激活、全能激活、全自动化 | §15 自主级别（manual/assisted/auto，默认 auto）+ 动作预算 + 断路器 + kill switch |
| 2 全自动绕过人机验证（爬虫场景） | §16 验证挑战探测 + 可插拔 solver 链 + host 白名单策略 + 工程边界 |
| 3 子代理机制（主代理派发，上下文不涨） | §17 orchestrator/worker 分离：主代理唯一工具是 `delegate`，worker 一次性任务、独立上下文、深度 1 |
| 4 沙箱等功能 | §18 沙箱强化：审计日志、工具层 URL 策略、watchdog、进程模式降级沙箱 |

**设计原则（四项共用）**：新能力全部走"策略数据 + 纯函数 + 可测接口"，与既有 TUI 命令面零冲突；所有新常量进 Global Constraints 表；worker 与主代理共用同一 runtime，不引第二个框架。

## 15. 自主级别（诉求 1）

- **三档**：`manual`（每个工具调用前需人确认）、`assisted`（破坏性/外网类调用需确认：`install_packages`、`browse`、`solve_challenge`）、`auto`（零确认，全速跑）。取值优先级 `--autonomy` 参数 > `CAGE_AUTONOMY` > state 记录 > 默认 `auto`（operator 个人云环境自用，默认全自动）。
- 档位写进 state 记录（`autonomy` 字段）与 `/info`（`autonomy`、`auto_actions_used`）。
- **确认协议**：manual/assisted 下，agent 侧返回 `{"reply":..., "pending": {"tool","args","reason"}}`，宿主 chat/HTTP 侧收到后向人请求确认，确认后以 `/chat` 二次提交 `{"confirm": true}` 继续同一步；HTTP 侧同一请求体支持 `confirm`。auto 档不经此路。
- **动作预算**：`CAGE_ACTION_BUDGET`（默认 500 次/agent/天）计数所有工具调用（含 worker），超额自动降档为 assisted 并要求确认——防失控烧钱/烧机器。计数按 UTC 日重置，存 state。
- **断路器与 kill switch**：`stop`（TUI）、`DELETE /api/agents/<name>`（HTTP）立即杀容器/进程组；`CAGE_WATCHDOG_SECS`（默认 0=关）开启后单次 `/chat` 超时即杀该次 worker。
- **审计**：每次工具调用落一条 JSONL 到容器 stdout（宿主收集进 `~/.cage/logs/audit-<agent>.jsonl`）：`ts/agent/tool/args_digest/output_len/exit_or_err/duration_ms`。无参数明文中的敏感项（key 类参数只记 sha256 前 8 位）。

## 16. 人机验证自处理（诉求 2）

- **新工具 `challenge_probe(url)`**：headless Chromium 打开页面，分类 `none|captcha|login|waf|rate_limit|error`，返回分类 + 证据（标题、命中规则、表单字段名摘要）。规则表是数据（`cage/challengerules.py`），可扩展。
- **新工具 `solve_challenge(url, kind)`**：按 kind 走 solver 链，回传 cookie/令牌摘要或"已解除"信号，由调用方（agent 自身或爬虫脚本）续用。
- **solver 链（可插拔）**：`local-ocr`（截图 → 经典 CV 预处理（灰度/二值/去噪/连通域分割）→ OCR）。OCR 后端默认 `pytesseract`（容器内**由 agent 自己** `install_packages apt tesseract-ocr` + `pip pytesseract` 落地——符合 §4 第 6 条"自己配环境"路线）；`CAGE_SOLVER_CMD`（外部命令钩子，stdin 收 PNG 路径、stdout 回文本）供 operator 接自研 solver；链式失败即上报，不重试爆破。
- **策略（默认保守）**：`CAGE_CHALLENGE_HOSTS`（逗号分隔，默认空）——**只有命中的 host 才允许自动破解**；未命中时 `solve_challenge` 返回 "host not in policy" 并升级给人（TUI 提示 / HTTP `pending`）。白名单由 operator 显式维护，这是有意的摩擦。
- **工程边界（契约，非道德宣导）**：本能力的设计目标是 operator 拥有或获授权测试的目标（自站、授权渗透/红队 scope、CTF）。基线实现**不集成付费打码服务、不伪造第三方站点的登录态/凭据**；确有需要走 `CAGE_SOLVER_CMD` 外接，由 operator 自负其责。
- **爬虫场景接线**：`run_command` 跑爬虫脚本遇验证码时，agent 的常规动作序列是 `challenge_probe` → 策略判定 → `solve_challenge` → 重跑脚本；该序列在 system prompt 的 `auto` 档下自动执行。

## 17. 子代理机制（诉求 3）

- **主代理默认即 orchestrator（v2：默认激活，无开关）**：`init` 产出的主代理**一开机就是编排者**，工具集恒为 `delegate` + `challenge_probe` + `solve_challenge` 三项，**没有** `run_command/write_file/read_file/list_files/browse/install_packages`——脏活累活全部派给 worker，主代理自己不动手。裸机模式（`CAGE_AGENT_MODE=bare`）仅为调试保留，README 明示"这不是正常用法"。`delegate(task: str, tools: list[str] = ["run_command","write_file","read_file","list_files"], max_steps: int = 8, budget_tokens: int = 200000) -> dict`。
- **worker 形态**：同容器内以 `agent/server.py --worker` 子进程运行（容器模式）或同用户子进程（进程模式），**全新 HISTORY、单任务、跑完即退**；回传结构化结果：`summary`（≤800 字）、`facts`（键值，≤20 条）、`files`（写了哪些路径）、`errors`、`steps_used`、`transcript_digest`。
- **深度 1**：worker 内 `delegate` 不可用（防递归派发爆炸）；并发 `CAGE_WORKER_CONCURRENCY`（默认 3）；单 worker 限 `CAGE_WORKER_MAX_TURNS`（默认 40 对话轮）与 `MAX_TOOL_STEPS`。
- **上下文收益（本特性的目的）**：主代理 HISTORY 只增 `delegate` 入参 + worker 摘要，O(1)；worker 完整过程不进主上下文——叠加 §20 v2 的"原文淘汰+索引"后，主代理上下文不含任何 LLM 改写段。
- **归档**：worker transcript 落 `~/.cage/logs/workers/<ts>-<slug>.jsonl`（容器模式经 stdout 回传宿主，进程模式直接写）；`/history` 的 archive 增补 worker 摘要条目（reason=`worker`），原文只在外置日志。
- **与记忆压缩的关系**：worker 摘要按普通 assistant 消息进入主 HISTORY，参与既有压缩；压缩摘要里保留 worker 产物的路径与结论。

## 18. 沙箱强化（诉求 4）

- **审计**：见 §15 末条（JSONL，宿主侧留存，制品级可回放）。
- **网络**：维持 `cage-net`（icc off）；**出口不在 Docker 层做域白名单**（需 NET_ADMIN，破坏 cap 最小化红线），改为**工具层 URL 策略**：`browse`/`challenge_probe` 只允许 `http/https`，`CAGE_URL_POLICY`（默认 `all`；可设 `allowlist:a.com,b.com`）在工具层拦截；LLM 网关 host 恒放行（否则 agent 无法思考）。
- **进程模式降级沙箱**（Docker 不可用时）：工具层 `/work` 白名单照旧；`install_packages` 默认**关闭**（`CAGE_PROCESS_INSTALL=1` 显式开，README 明示风险）；worker 子进程以同一非 root 用户运行；资源上限以 `CAGE_PROCESS_NICE`（默认 10）降低优先级。
- **watchdog/看门狗**：`CAGE_WATCHDOG_SECS` 到点杀当次请求的 worker 进程组；`doctor` 显示 watchdog 状态。
- **配额**：容器模式 tmpfs size 即配额（既有）；进程模式无磁盘配额（云机器自担），`status` 打印 `/work` 实际占用提醒。
- **`/info` 扩展**：新增 `autonomy`、`workers_spawned`、`challenge_policy_hosts`（计数不吐明文）、`action_budget_left`、`audit_stream`（布尔）。

## 19. 新验收标准（追加，纳入总体验收）

20. `manual/assisted` 档下破坏性调用返回 `pending` 且不执行；确认后执行；`auto` 档零确认直接执行。
21. `CAGE_ACTION_BUDGET` 超额后该 agent 自动降档 assisted。
22. `challenge_probe` 对四种注入的本地夹具页（none/captcha/login/waf）分类正确率 4/4（fixtures 由仓库自带，不依赖外网）。
23. `solve_challenge` 对夹具图片验证码命中白名单 host 时返回解除信号、未命中时拒绝并升级。
24. 主代理文本任务中全程只见 `delegate` 工具（`/info` tools 为 orchestrator 集）；worker 内工具按白名单生效且 `delegate` 缺席。
25. 派发 10 个任务后主代理 `history_turns` 增长 ≤10 对（ worker 摘要不进过程）。
26. worker 并发受 `CAGE_WORKER_CONCURRENCY` 限制；nesting 深度为 1。
27. 每次工具调用产生一条审计 JSONL；key 类参数只留 sha256 前 8 位。
28. `browse` 对非 http(s) 与非白名单 host（设了 allowlist 时）在工具层被拒。
29. 进程模式默认 `install_packages` 被拒；显式开后可用。
30. 上述新能力的验收对容器/进程两种模式都成立（Fixtures 与 solver 测试不依赖 Docker）。

## 20. v2 修订：子代理默认激活 + 压缩污染治理（2026-10-31，operator 追加）

### 20.1 诊断：什么是"压缩污染"
v1 第三维记忆 = 超 token 预算时把最旧 40 轮交给 LLM 重写成 ≤8000 字摘要、替换 HISTORY 头。三个病灶：
1. **有损且不可逆**：摘要丢路径/版本/错误细节，也会自作概括；模型后续把它当真实史实引用——幻觉源头。
2. **位置权重最高**：改写段位于 HISTORY 最前，最像"事实"。
3. **对 orchestrator 是错配**：主代理不需要旧散文，需要的是"派过什么、拿回什么、产物在哪"。

### 20.2 v2 记忆模型（主代理默认走这套）
- **默认零 LLM 压缩**：`CAGE_SUMMARIZE=0`。超 `CAGE_MAX_TURNS`（400 对）或 `CAGE_CONTEXT_TOKENS`（900k）时只做**原文淘汰 + 结构化索引**：
  1. 最旧若干对**整体**移入 archive（一字不改，`reason=evicted|indexed`），`/history` 逐字 full 恢复；
  2. HISTORY 头维护**一条确定性索引**（非模型生成）：每行 `序号|角色|首80字|产物路径/worker_id`，上限 `CAGE_INDEX_MAX_CHARS`（20000），超限丢最旧索引行（仅指针，原文仍在 archive）；
  3. prompt 组装 = SYSTEM_PROMPT + INDEX + 最近原文对 + 新消息。模型看到的是账本，不是改写史实。
- **LLM 摘要仅应急可选**：`CAGE_SUMMARIZE=1` 且"索引+最近对"仍超预算（单条巨型消息场景）才折叠最旧对，条目带 `[summary-lossy]` 前缀，`/info` 增 `lossy_in_context` 布尔——模型永远知道面前哪段不可靠。
- **worker 摘要不属污染**：那是 worker 在自己上下文里对新劳动的产物概括（新信息，非旧历史改写），`clamp_worker_result` 保证 ≤800 字。

### 20.3 v2 prompt 组装流
```
每次 /chat：
  [SystemMessage(SYSTEM_PROMPT)]
+ [INDEX 条（确定性生成，零模型调用）]
+ [最近 ≤CAGE_MAX_TURNS 对的原文]
+ [HumanMessage(新消息)]
超限时：淘汰最旧对 → archive（原文）+ 重建索引（纯字符串操作）
```

### 20.4 新增/变动常量
`CAGE_SUMMARIZE`（默认 0）、`CAGE_INDEX_MAX_CHARS`（默认 20000）、`CAGE_AGENT_MODE`（默认 `orchestrator`；`bare` 仅调试）。

### 20.5 新增验收（接续编号）
31. `init` 后 `/info.mode == "orchestrator"` 且 `tools` 恰为 `delegate/challenge_probe/solve_challenge` 三项——无需任何 env，默认激活。
32. 主代理超 `CAGE_MAX_TURNS`：archive reason=evicted、原文可经 `/history` 逐字恢复、上下文出现索引、**summarizer 零调用**（打桩计数断言）。
33. 仅当 `CAGE_SUMMARIZE=1` 才可能出现 `[summary-lossy]` 条目；`/info.lossy_in_context` 如实反映。
34. 索引有界：注入 500 对后索引字符数 ≤ `CAGE_INDEX_MAX_CHARS`。
35. worker 与主代理上下文零串台：worker 的 user/assistant 对不出现在主 HISTORY（除 digest）。

## 21. v3 追加：工作区浏览器与 Git 自动发布（2026-10-31，operator 追问）

### 21.1 工作区在哪、有几种看法

| 模式 | 位置 | 生命周期 |
|---|---|---|
| 容器模式 | 容器内 `/work`（tmpfs） | **随容器消失**；`stop`/`rebuild` 前必须 `publish` 或 `work x N` 抽出 |
| 进程模式 | 宿主 `~/.cage/work/<name>/` | 持久，重启不丢 |

三种看法（按推荐序）：
1. **`work` 命令**（新增，本节 21.2）：序号导航，不用记 docker 命令；
2. agent 自己的 `list_files`/`read_file`（聊天里直接问）；
3. `docker exec -it cage-<name> ls -la /work` / `docker cp`（命令行老办法）。

### 21.2 `work [name]` 工作区浏览器（TUI 新增命令）

```
cage> work default
  1  d  4.0K  10-31 12:03  .git/
  2  d  4.0K  10-31 12:05  src/
  3  f  812B  10-31 12:05  README.md
> 2                # 输入序号进入目录（文件则直接读）
  r 3              # 读 3 号文件内容（截断到 MAX_OUTPUT）
  x 3 ./README.md  # 抽出到宿主当前目录（docker cp / 直接拷贝）
  p                # 发布整个工作区到 GitHub（走 21.3）
  q                # 退出
```

- 清单来源：容器模式走 agent 新端点 `GET /files?path=rel`（或 `&read=1` 取内容）；进程模式直接 `os.scandir`。渲染与序号解析是纯函数（`render_listing`、`parse_selection`），可测。
- 安全：路径仍走 `_workspace_path` 校验（越界拒）；`x` 抽出的目标路径只允许宿主 cwd 之下。

### 21.3 Git 自动发布（worker 工具 `git_publish` + TUI `publish`）

**认证三模式**（worker 内按优先级解析，PAT 与私钥都只回显长度+前 3 位，与 LLM key 同规矩）：

1. `CAGE_GH_TOKEN`（PAT，HTTPS：remote = `https://x-access-token:<PAT>@github.com/<owner>/<repo>.git`）
2. `CAGE_SSH_KEY_B64`（base64 私钥，`init` 时经 env 注入，落在 `/work/.cage/ssh/id_ed25519`，chmod 600；remote = `git@github.com:<owner>/<repo>.git`，`GIT_SSH_COMMAND` 指向它 + `-o StrictHostKeyChecking=accept-new`）
3. **现场生成**：`cage> key [name]` 或 worker 内 `ssh_keygen` 在容器/工作区生成 ed25519 对并打印公钥 → operator 去 GitHub 仓库加 Deploy Key → 回 `publish` 续跑（同一次会话内可续）

**发布流程**（worker 内，幂等可重跑）：
```
1. /work 不是 git 仓库则 git init（author 用镜像内配好的 Isolis Agent 身份）
2. git add -A && git commit -m <message 或自动生成>
3. 按模式配 remote（token 不落 shell history：经 env GIT_ASKPASS 或一次性 URL）
4. git push -u <remote> HEAD:<branch>   # branch 默认 main，可指定
5. 失败分类回传：auth | remote-not-found | rejected | network —— 每种给一句下一步
```
- **仓库地址必须显式**：`--repo` / `CAGE_PUBLISH_REPO`，默认空——空即拒绝执行（防 agent 自作主张推代码到不明处）。
- 可选增强（默认关）：`CAGE_GH_CREATE_REPO=1` 且 PAT 有 scope 时，目标仓库不存在则经 GitHub API 建库。
- 审计：每次发布一条 audit（repo/branch/commit sha/变更文件数；token 与私钥只留 sha256 前 8 位）。

### 21.4 与记忆/沙箱的关系

- 发布是**持久化出口**，README「已知差异」补：容器模式 `stop` 前 `publish` 或 `work x N`。
- SSH 私钥不进 state、不进日志、不进 `/history`；容器模式经 env 注入（不破"无宿主挂载"红线），进程模式落 `~/.cage/ssh/<name>/`（chmod 700）。
- `git_publish` 属破坏性工具，进 `DESTRUCTIVE_TOOLS`（autonomy 档管控与审计覆盖它）。

### 21.5 新增验收

36. `work` 渲染序号清单、进入目录、读文件、越界路径拒绝；`x` 抽出后宿主文件内容与容器内一致。
37. `publish` 无 `--repo` 时一行拒绝；有 repo 时走完 commit+push（用本地 bare 仓库当远端打桩验证参数，不连真 GitHub）。
38. PAT 模式下 remote 含 token、但 audit 与任何输出只见 repo/branch/sha；SSH 模式 `GIT_SSH_COMMAND` 指向注入的私钥且 `accept-new`。
39. `key` 生成幂等（二次调用不覆盖既有密钥），公钥可打印、私钥不打印。
40. 上述对容器/进程两模式都成立（Git 测试用本地 bare 远端，不依赖外网）。

## 22. v4 追加：自主网络研究与风险档位（2026-10-31，operator 追问）

### 22.1 诊断：为什么会"识别不到"
固定工具打固定站点：目标页面改版、接口非标准、工具从未见过 → 直接卡死回报失败。解法：给 worker **研究回路**（搜→评→装→验→记），让技巧可被发现、可被复用，而不是每次从零撞墙。

### 22.2 `web_search` 工具（worker 侧）
- `web_search(query: str, n: int = 5) -> list[{title, url, snippet}]`。
- 后端链：`CAGE_SEARCH_CMD`（operator 的搜索 API 钩子：stdin 收 `{"query","n"}` JSON，stdout 回 JSON 数组）→ 未配则尽力而为：DuckDuckGo HTML 端点（urllib 抓取+解析）→ 都不可用回 `error: search unavailable`（不崩、不重试爆破）。
- 与 `browse` 分工：search 出候选清单，browse 深入读具体页面（GitHub README、教程、issue）。

### 22.3 研究回路（worker 标准动作序列）
```
1. 直达：现有工具先试一次（yt-dlp / you-get / curl）
2. 失败 → web_search("<站点名> 下载 接口 / github <站点> downloader / <站点> 解析")
3. browse 读前 2-3 个结果，抽取：工具名、安装方式、关键参数、cookie 要求
4. install_packages / git clone 装候选
5. 小样验证（单个视频/单页），成功才放大
6. playbook 写回（22.4）→ ≤800 字摘要回报主代理
```

### 22.4 Playbook（host 级技巧复用记忆）
- 位置：容器 `/work/.cage/playbook/<host>.md`（进程模式即 `~/.cage/playbook/<host>.md`）。
- 内容：可用工具+命令模板、cookie 要求、限速经验、失败模式、最后验证时间。
- worker 开工**先读** playbook（不重复试错）；成功后**写回**。主代理只感知"worker 用了/更新了某 host 的 playbook"这一件事，技巧细节不进主上下文。
- 幂等合并：同 host 条目去重，按"最后验证时间"保留最新模板。

### 22.5 风险档位（把"无视风险"变成显式选择，而非默认隐藏行为）
`CAGE_RISK_LEVEL = strict | balanced | aggressive（默认，operator 选定）`；`init/setup/rebuild` 加 `--risk`；写 state、报 `/info`。白名单闸门（`CAGE_CHALLENGE_HOSTS`）不随档位改变。

| 动作 | strict | balanced | aggressive |
|---|---|---|---|
| 公开下载工具（yt-dlp/you-get/aria2） | ✓ | ✓ | ✓ |
| 限速、UA/Referer 伪装 | ✗ | ✓ | ✓ |
| operator 注入的 cookie（SESSDATA 等） | ✗ | ✓ | ✓ |
| 自动装第三方下载器（非官方源） | ✗ | ✓ | ✓ |
| 自动求解验证码/风控页（§16 链） | ✗ | ✗ | ✓ |
| 第三方解析接口/镜像站 | ✗ | ✗ | ✓ |
| 代理池/分布式规避封禁 | ✗ | ✗ | **不实现**（明确 out of scope） |

两条不变的红线：① balanced/aggressive 的自动解与解析仍受 `CAGE_CHALLENGE_HOSTS` 白名单约束——**"对哪些站点可以对抗"永远由 operator 显式列出，档位只决定列名的站点对抗到多深**；② 所有动作进审计。工程契约：aggressive 是对第三方站点防护的主动对抗，后果（限流/封禁/账号风控）operator 自负，agent 不替 operator 判断该不该做。

### 22.6 B站下载链在新设计下的实际走法
```
主代理 delegate → worker：
  playbook[bilibili.com] 有记录 → 直接套模板
  → yt-dlp --flat-playlist 枚举 UP 主全部视频（公开路径，免登录）
  → 单视频试下载：720P 公开直达；1080P+ 需 SESSDATA
     · balanced：有注入的 SESSDATA 就用，没有就降级 + 明确上报
     · aggressive 且 bilibili 在 CAGE_CHALLENGE_HOSTS：自动尝试第三方解析/镜像候选
  → 风控 412：challenge_probe 分类 → balanced 限速重试+上报；aggressive 白名单内解
  → playbook 写回：本次成功的命令模板、限速参数、失败模式
  → ≤800 字摘要 + 产物路径回报
```

### 22.7 新增验收
41. `web_search` 在 stub 后端下返回结构化结果；无后端时一行报错不崩。
42. 研究回路可重放：对本地夹具页（假"工具市场"），worker 走完 search→install（打桩）→validate→playbook 写回全链。
43. playbook 幂等合并：同 host 二次研究不产生重复条目。
44. 风险矩阵可测：`risk_allows(level, action)` 逐格与 22.5 表一致；aggressive 下白名单外的自动解仍被拒。
45. `/info` 报 `risk_level` 与 playbook 条目数。

## 23. v5 追加：Skill 自动扫描、采用与改进提案（2026-10-31，operator 追加）

### 23.1 诉求
operator 自有 skill 仓库 + GitHub 高分 skill = 能力池。每次新 agent/新对话自动盘点；本地仓库的能直接用、网上有的能微改用；发现 skill 有缺口或改动能更快 → 必须告诉 operator。

### 23.2 Skill 格式与两个池
- 兼容本会话 skill 目录形态：`<name>/SKILL.md`（frontmatter `name/description`）+ 可选 `scripts/`、`resources/`。
- **本地池** `~/.cage/skills-repo/`：git 克隆 operator 仓库（默认 `https://github.com/suzizhuo/suzizhuo-skill-repository`，`CAGE_SKILLS_REPO` 可换），每次扫描 `git pull --ff-only`。
- **远程候选池** `~/.cage/skills-repo-remote/<slug>/`：GitHub 高分 skill 仓库（有 `CAGE_GH_TOKEN` 走 Search API：`topic:skill sort:stars`；否则走 `web_search`），**按需 clone**，不预先全量拉。
- 登记表 `~/.cage/skills/index.json`：`{name, description, path, source: local|remote, stars, verified_at, used_count, last_used_at, proposals}`。

### 23.3 新对话自动扫描（宿主侧，不耗 agent 上下文）
- 触发：`init` 建新 agent 时；或距上次扫描超 `CAGE_SKILLS_REFRESH_HOURS`（默认 24）。`init --no-scan` 跳过。
- 动作：pull 本地池 → GitHub 高分扫描 → 重建 index.json（同源去重，按 stars/verified 排序）。
- chat 首行打印一行盘点：`skills: 12 local, 8 remote (top: foo ★2.3k), matched: bili-dl, repo-audit`。
- 扫描失败不阻塞：Hub/搜索不可用 → 用旧 index 并打印 `skills: (stale index, N local)`。

### 23.4 采用与微改（worker 侧）
- worker 开工：`match_skills(task, index, k=3)`（关键词重叠 + 描述匹配，纯函数）→ 命中则把 skill 目录**复制**进 `/work/.cage/skills/<name>/`（副本隔离，原件只读），读 SKILL.md 照做。
- 微改：参数/路径不适配 → 只改副本，改动记入该任务的 memory 摘要（operator 可见）；`~/.cage/skills-repo` 原件永不被 worker 写。
- 未命中 → 走 §22 研究回路从零搭 → 成功则 `package_skill()` 产出候选 skill 落 `~/.cage/skills-repo/candidates/<name>/`（状态 candidate，待 operator 确认入正式池）。

### 23.5 改进提案（发现不足 → 告诉你）
- worker 调 `skill_propose(name, gap, suggestion, evidence, est_gain)` → 落 `~/.cage/skills/proposals/<ts>-<name>.md` + index 计数 +1。
- 露出面：`/info.skill_proposals`；chat 启动行「有 N 条 skill 改进提案待看」；TUI 新命令 `skills`：列池/列提案；`skills adopt <name>` 采用候选；`skills proposals` 专列提案（批准 = 把建议改动应用到本地池副本，operator 一句话确认）。
- 提案必含四段：缺口（哪一步卡了/多花几步）、建议改动、证据（审计 id / 日志行）、预估收益（少几步/少几分钟）。

### 23.6 上下文隔离
SKILL.md 正文只在把副本拖进 `/work` 后按需进入 worker 上下文；主代理只看到「用了/更新了/提案了哪个 skill」一行；index.json 与 proposals 是宿主侧文件，零上下文成本。

### 23.7 新增验收
46. 扫描产出 index.json：字段齐全、同源重复扫描去重。
47. `match_skills("下载B站视频", index)` 命中含 bilibili/yt-dlp 的 skill；空池返回 []。
48. 微改隔离：worker 改 `/work` 副本后，`~/.cage/skills-repo` 原件 md5 不变。
49. `skill_propose` 落盘 + `/info.skill_proposals` 递增 + 四段齐全。
50. `init` 后 chat 首行出现 skills 盘点行（打桩扫描器，断格式不断内容）。
51. 默认 risk = aggressive（state 记录）；白名单外 auto_solve 仍被拒（44 不破）。
