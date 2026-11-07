# Monkey Code 0 -> 部署 Agent（Isolis）

本目录保存从空工作区到 `isolis-default` 上线运行的完整记录，目标是**可完美还原**。

归档时间：2026-11-06
宿主环境：Debian bookworm、Python 3.11.2、Docker 20.10.24、overlay2

---

## 目录结构

```
monkey code 0-> 部署agent/
├── README.md                      本文件，总索引
├── 01-步骤/
│   ├── 完整步骤.md                从 0 到现在的按时间顺序全流程
│   └── 自定义预设与提示词.md      改 profile / 提示词的快捷路径（只改一个 JSON）
├── 02-资源/
│   ├── isolis.py                  宿主 orchestrator 快照
│   ├── agent/
│   │   ├── server.py              agent 运行时快照
│   │   ├── Dockerfile             镜像构建快照
│   │   ├── requirements.txt       LangChain + Playwright 依赖
│   │   ├── install-android-sdk.sh Android SDK 安装器
│   │   └── .dockerignore
│   ├── tests/
│   │   ├── test_isolis_agent.py   agent 契约测试（20 个）
│   │   └── test_isolis_host.py    宿主 orchestrator 测试（10 个）
│   ├── 项目文档/
│   │   ├── ISOLIS_PROMPT.md       权威规格（864 行）
│   │   ├── QUICKSTART.md          上手说明
│   │   ├── README.md              项目原始 README
│   │   ├── 提示词.txt              Isolis 提示词规格压缩版
│   │   ├── plans/                 Cage 实现计划
│   │   ├── specs/                 Cage 设计规格
│   │   └── prompts/               分阶段提示词包（00-07）
│   ├── 源码/
│   │   ├── cage/                  旧 Cage 运行时全部模块
│   │   ├── tests-all/             仓库全部测试（17 个文件）
│   │   ├── base-repo.bundle       初始提交的完整 git 仓库（163K）
│   │   ├── .monkeycode/MEMORY.md  项目记忆
│   │   └── gitignore.txt          .gitignore 快照
│   ├── 平台指令/
│   │   ├── rules/                 平台 29 条规则原文
│   │   ├── skills/                expanding-fuzzy-instructions 全量
│   │   └── suzizhuonb666/         用户规则包 + 拦截脚本 + 原始 zip
│   ├── 环境/
│   │   ├── python-version.txt     Python 版本
│   │   ├── pip-freeze.txt         pip 依赖清单
│   │   ├── dpkg-packages.txt      系统包清单
│   │   ├── uname.txt / cpu-cores / memory / disk
│   │   ├── docker-version-full.txt / docker-info-full.txt
│   │   ├── image-tooled-inspect.json / image-strict-inspect.json
│   │   ├── image-tooled-history.txt
│   │   ├── network-isolis-net.json
│   │   ├── container-isolis-default-inspect.json
│   │   └── container-env-redacted.txt   （密钥已脱敏）
│   ├── 配置/
│   │   ├── daemon.json            Docker 镜像源配置
│   │   ├── state.json.example     .isolis/state.json 样例
│   │   ├── isolis.config.example.json   自定义预设/提示词的完整模板
│   │   ├── agent-prompt.example.md      提示词文件示例
│   │   ├── current-changes.patch  相对初始提交的完整补丁
│   │   ├── dockerd-startup.log    daemon 启动日志
│   │   ├── dockerd.log.tail       完整 daemon 日志尾部
│   │   └── 当前快照/              运行状态快照（见下）
│   └── 脚本/
│       ├── restore.sh             从零还原环境（幂等）
│       ├── verify.sh              校验当前环境是否一致
│       └── snapshot.sh            保存当前状态快照
├── 03-失误点/
│   └── 错误与经验.md              所有踩过的坑、原因、处理、教训
└── 示例环境/
    ├── 示例环境引导.md           按当前状态逐步引导：看环境、发指令、验隔离、读返回
    ├── capture.sh                 刷新实测输出（末尾自动脱敏）
    └── 实测输出/                   10 份真实命令输出（归档时刻抓取）
```

---

## 先看哪个

- **想快速上手**：直接读 `示例环境/示例环境引导.md`，它带真实输出，
  照抄命令即可复现当前状态。
- **想改 Agent 预设或提示词**：读 `01-步骤/自定义预设与提示词.md`，
  只改 `.isolis/isolis.config.json` 一个文件。
- **想从零重装**：读 `01-步骤/完整步骤.md`。
- **想查踩过的坑**：读 `03-失误点/错误与经验.md`。
- **想找某个文件在哪**：读 `02-资源/README.md`。

---

## 三分钟快速上手

```bash
# 进入工作区
cd /workspace

# 1. 环境体检（无 LLM 注入时最后一行是 not ready，属正常）
python3 isolis.py doctor

# 2. 准备 Docker、镜像源、镜像、网络（首次约 17 分钟建镜像）
python3 isolis.py setup

# 3. 构建并启动隔离 agent（默认名 default，档位 tooled）
python3 isolis.py init

# 4. 查看是否起来了
python3 isolis.py status

# 5. 进入对话
python3 isolis.py chat
```

交互式入口（提示符 `isolis>`）：

```bash
python3 isolis.py
```

对话内提示符 `you>`，输入 `/back` 回到 `isolis>`，`/quit` 退出，`/help` 看帮助。

**关键前提**：`MCAI_LLM_*` 由平台注入到你的 TUI 会话。在自动化 shell 里跑
`doctor` 会看到一条 LLM `[fail]`，这是设计行为，不是故障。

---

## 全部命令一览

### TUI 与命令行共用

`python3 isolis.py <命令>` 与在 `isolis>` 里输入命令完全等价。

| 命令 | 作用 | 备注 |
|---|---|---|
| `setup` | 准备 Docker daemon、镜像源、镜像、网络 | 不启动容器 |
| `doctor` | 环境体检，13 项检查 | 无 LLM 注入时退出码 1 |
| `init [name]` | 构建镜像并启动 agent | 默认名 `default` |
| `chat` | 与当前 agent 对话 | 会先确保 agent 就绪 |
| `use <name>` | 切换 current agent | 名字不存在报错 |
| `status` | 列出所有 agent 与隔离摘要 | `*` 标记 current |
| `rebuild` | 重建镜像并重启当前 agent | 改了镜像内容后用 |
| `stop [name]` | 停止并删除容器（默认 current） | 保留 state 记录 |
| `profiles` | 列出档位限制与自定义配置 | `*` 表示被配置文件覆盖 |
| `help` | 显示帮助 | |
| `quit` / `exit` | 退出 TUI | |

### 命令选项

`setup`、`init`、`rebuild` 支持：

| 选项 | 取值 | 默认 | 作用 |
|---|---|---|---|
| `--profile` | `strict` \| `tooled`（或自定义名） | `tooled` | 沙箱能力档位 |
| `--source` | `local` \| git URL | `local` | agent 源码位置 |
| `--model` | 模型名 | 注入值 | 覆盖模型 |

```bash
python3 isolis.py init --profile strict
python3 isolis.py init myagent --profile tooled --model monkeycode-pro/gpt-5.6-terra
python3 isolis.py init --source https://example.com/agent.git
```

未知选项或缺少取值会一行报错：

```
error: unknown option: --bogus
error: --profile requires a value
```

### 对话内命令（`you>` 提示符）

| 输入 | 作用 |
|---|---|
| 任意文本 | 发给 agent，返回回复与使用的工具 |
| `/back` | 返回 `isolis>` |
| `/quit` / `/exit` | 直接退出 TUI |
| `/help` | 显示对话内帮助 |

---

## 环境变量

### 宿主机侧（`isolis.py`）

| 变量 | 默认 | 作用 |
|---|---|---|
| `ISOLIS_PROFILE` | `tooled` | 默认档位 |
| `ISOLIS_AGENT_SOURCE` | `local` | 默认源码 |
| `ISOLIS_MODEL` | 注入值 | 最高优先级模型 |
| `ISOLIS_DEFAULT_MODEL` | `monkeycode-pro/gpt-5.6-terra` | 兜底模型 |
| `ISOLIS_CONTEXT_TOKENS` | `900000` | 压缩触发阈值，0 关闭 |
| `ISOLIS_MAX_MESSAGE_CHARS` | `900000` | 单条消息字符上限 |
| `ISOLIS_MAX_TURNS` | `400` | 对话轮次上限 |
| `ISOLIS_ARCHIVE_MAX` | `4000` | 归档条数上限 |

优先级：`命令行 --model` > `ISOLIS_MODEL` > 配置文件 `model` > `MCAI_LLM_MODEL`。

### 容器内（`agent/server.py`，由宿主自动注入）

| 变量 | 默认 | 作用 |
|---|---|---|
| `ISOLIS_AGENT_NAME` | — | 设置后走 Isolis 运行时 |
| `ISOLIS_PROFILE` | `tooled` | 影响提示词描述与工具集 |
| `ISOLIS_INSTALL` | `1` | 是否启用装包工具 |
| `ISOLIS_SOURCE` | `local` | 出现在 `/info` |
| `ISOLIS_SYSTEM_PROMPT` | 空 | 自定义提示词（整段替换） |
| `ISOLIS_CONTEXT_TOKENS` | `900000` | 上下文预算 |
| `ISOLIS_CONTEXT_HARD_LIMIT` | `1000000` | 上游硬上限，超出会 clamp |
| `ISOLIS_MAX_MESSAGE_CHARS` | `900000` | 单条消息截断阈值 |
| `ISOLIS_MAX_TURNS` | `400` | 轮次上限 |
| `ISOLIS_ARCHIVE_MAX` | `4000` | 归档上限 |
| `ISOLIS_MAX_OUTPUT` | `20000` | 工具输出截断 |
| `ISOLIS_MAX_TOOL_STEPS` | `8` | 单轮最多工具调用次数 |
| `ISOLIS_SHELL_TIMEOUT` | `120` | `run_command` 超时秒数 |
| `ISOLIS_INSTALL_TIMEOUT` | `1800` | 装包超时秒数 |
| `ISOLIS_COMPRESS_BATCH` | `40` | 每次压缩折叠的轮数 |
| `ISOLIS_SUMMARY_MAX_CHARS` | `8000` | 摘要字符上限 |
| `ISOLIS_SUMMARY_INPUT_CHARS` | `60000` | 摘要输入字符上限 |
| `ANDROID_HOME` | `/opt/android-sdk` | Android SDK 路径 |

---

## 配置文件（推荐的自定义方式）

`/workspace/.isolis/isolis.config.json`，不存在则用内置默认：

```json
{
  "default_profile": "tooled",
  "model": "",
  "prompt": "自定义系统提示词",
  "prompt_file": "agent-prompt.md",
  "profiles": {
    "tooled": { "memory": "8g", "cpus": "4.0", "pids": "1024" }
  }
}
```

- `prompt_file` 优先于 `prompt`；相对路径基于 `/workspace`
- `profiles.<name>` 可覆盖内置字段，也可新增 profile
- 改完执行 `stop` + `init`，**不需要 rebuild**
- 只有改 `agent/` 下的文件（Dockerfile、server.py、requirements）才需要 `rebuild`

完整字段表与场景见 `01-步骤/自定义预设与提示词.md`。

---

## HTTP 接口（`127.0.0.1:18080`）

宿主 TUI 走的就是这组接口，可直接 curl：

```bash
# 存活检查
curl -sS http://127.0.0.1:18080/health

# 运行信息（profile、模型、协议、工具、预算）
curl -sS http://127.0.0.1:18080/info | python3 -m json.tool

# 对话历史与归档
curl -sS http://127.0.0.1:18080/history | python3 -m json.tool

# 发一条消息
curl -sS -X POST http://127.0.0.1:18080/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "列出 /work 下的文件"}'
```

| 接口 | 方法 | 返回 |
|---|---|---|
| `/health` | GET | `ok`、`agent`、`hostname`、`pid`、`cwd` |
| `/info` | GET | 运行时全量信息与工具列表 |
| `/history` | GET | 对话历史、归档、token 占用 |
| `/chat` | POST | `reply`、`tools_used`、`memory` 统计 |
| 其他 | 任意 | 404 `not found` |
| `/chat` 空消息 | POST | 400 `invalid json` / 空消息 |

---

## Docker 层直接操作

```bash
# 看容器
docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'

# 看镜像
docker images --format '{{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}'

# 看隔离参数
docker inspect isolis-default --format \
  'User={{.Config.User}} RO={{.HostConfig.ReadonlyRootfs}} \
Mem={{.HostConfig.Memory}} NanoCpus={{.HostConfig.NanoCpus}} \
Pids={{.HostConfig.PidsLimit}} Mounts={{len .Mounts}}'

# 看容器日志
docker logs --tail 50 isolis-default

# 进容器看一眼（调试用）
docker exec -it isolis-default sh

# 验证工具链（一次性容器，不联网）
docker run --rm --network none isolis-agent:tooled-ca22788ffe sh -c \
  'java -version; node -v; python3 -V'
```

---

## 归档自带的脚本

```bash
# 校验当前环境是否与归档一致（12 项）
bash "monkey code 0-> 部署agent/02-资源/脚本/verify.sh"

# 刷新运行状态快照（自动脱敏）
bash "monkey code 0-> 部署agent/02-资源/脚本/snapshot.sh"

# 刷新示例环境的实测输出
bash "monkey code 0-> 部署agent/示例环境/capture.sh"

# 从零还原代码 + Docker 环境 + 两个镜像（幂等）
bash "monkey code 0-> 部署agent/02-资源/脚本/restore.sh"

# 还原时额外补齐平台 rules/skills
bash "monkey code 0-> 部署agent/02-资源/脚本/restore.sh" --with-platform

# 只还原代码与环境，跳过耗时的镜像构建
bash "monkey code 0-> 部署agent/02-资源/脚本/restore.sh" --no-build

# 校验归档完整性
cd "monkey code 0-> 部署agent" && sha256sum -c CHECKSUMS.sha256
```

---

## 测试

```bash
# 全套
cd /workspace && python3 -m pytest tests/ -q

# 只跑 Isolis 相关
python3 -m pytest tests/test_isolis_host.py tests/test_isolis_agent.py -q
```

期望：全套 112 passed。

---

## 三条还原路径

### 路径 A：最省事，用补丁

前提：工作区 git HEAD 为 `19436e4 Initial commit`。

```bash
cd /workspace
git apply "monkey code 0-> 部署agent/02-资源/配置/current-changes.patch"
cp "monkey code 0-> 部署agent/02-资源/tests/test_isolis_agent.py" tests/
cp "monkey code 0-> 部署agent/02-资源/tests/test_isolis_host.py" tests/
python3 -m pytest tests/ -q
```

该补丁已在干净副本上验证：可 `git apply --check`，应用后
`py_compile` 通过、44 个相关测试通过、产物与快照逐字节一致。

### 路径 B：用归档文件直接覆盖

```bash
cd /workspace
cp -r "monkey code 0-> 部署agent/02-资源/agent/." agent/
cp "monkey code 0-> 部署agent/02-资源/isolis.py" isolis.py
cp "monkey code 0-> 部署agent/02-资源/tests/"*.py tests/
```

注意：这条路径不包含 `tests/test_agent_server.py` 的那处修改。那条测试在初始
提交里断言 Dockerfile 不得含 JDK/Android，与规格冲突，需要手动改成
`test_dockerfile_pins_base_and_ships_dev_toolchain`（内容见补丁或 `01-步骤` 阶段 5）。

### 路径 C：全自动（推荐）

```bash
cd /workspace
bash "monkey code 0-> 部署agent/02-资源/脚本/restore.sh"
bash "monkey code 0-> 部署agent/02-资源/脚本/verify.sh"
```

`restore.sh` 现在覆盖**代码 + 项目文档 + Docker 环境 + 两个镜像**，
一条命令即可回到归档状态。幂等，可反复运行。

可选参数：

```bash
# 同时把平台 rules/skills 补齐到 /root/.codingmatrix/project-tpl/.ai-ready
bash "monkey code 0-> 部署agent/02-资源/脚本/restore.sh" --with-platform

# 只恢复代码与环境，跳过耗时的镜像构建
bash "monkey code 0-> 部署agent/02-资源/脚本/restore.sh" --no-build
```

注意：`restore.sh` 不覆盖 `tests/test_agent_server.py`（该文件的冲突修复
记录在 `current-changes.patch` 里）。若从纯初始提交还原，请先用路径 A 应用补丁，
或手工改那条测试。

---

## 完整性校验

归档内含 `CHECKSUMS.sha256`，覆盖全部 172 个文件：

```bash
cd "monkey code 0-> 部署agent"
sha256sum -c CHECKSUMS.sha256
```

全部 `OK` 说明归档未被意外修改。重新生成：

```bash
find . -type f ! -name 'CHECKSUMS.sha256' -print0 \
  | sort -z | xargs -0 sha256sum > CHECKSUMS.sha256
```

关键文件的指纹：

| 文件 | SHA-256 |
|---|---|
| `agent/server.py` | `ca8912ee2f65df0f...` |
| `agent/Dockerfile` | `a5c4a90c78884d66...` |
| `isolis.py` | `ee8f6ad43f68ded0...` |
| `current-changes.patch` | `71278cf37b85859c...` |

---

## 当前状态（归档时刻）

| 项 | 值 |
|---|---|
| Docker | 20.10.24+dfsg1，overlay2，root `/var/lib/docker` |
| 镜像 | `isolis-agent:tooled-ca22788ffe`、`isolis-agent:strict-c2ba43eb87`（2.23GB，同 ID `7f61f25b2cab`） |
| 基础镜像 | `python:3.12-slim-bookworm`（ID `313cf8f190a1`） |
| 网络 | `isolis-net` bridge，icc off，ID `23ab7ed0b269` |
| 容器 | `isolis-default` running，`127.0.0.1:18080 -> 8080` |
| Agent | `profile=tooled`、`install=true`、7 工具、`protocol=anthropic` |
| 模型 | `monkeycode-basic/deepseek-flash`（`ISOLIS_MODEL` 优先） |
| state | `.isolis/state.json`，`current=default` |
| 测试 | 102 passed |

完整快照见 `02-资源/配置/当前快照/`。

---

## 隔离契约（当前容器实测）

| 项 | tooled | strict |
|---|---|---|
| 用户 | `0:0` | `nobody` |
| 根文件系统 | 可写 | 只读 |
| CapDrop | `ALL` | `ALL` |
| CapAdd | CHOWN, DAC_OVERRIDE, FOWNER, SETUID, SETGID, SETFCAP | 无 |
| 内存 / CPU / pids | 4g / 2.0 / 512 | 512m / 1.0 / 128 |
| tmpfs | `/work` 2g、`/tmp` 512m、`/dev/shm` 512m | 512m / 64m / 64m |
| 端口 | `127.0.0.1:18080` 单个 | 单个 loopback |
| 宿主挂载 | 无 | 无 |
| Docker socket | 无 | 无 |
| no-new-privileges | 是 | 是 |
| 工具数 | 7 | 5 |

---

## 已知限制

1. **LLM 只有用户会话可用**。表单验证必须在 `isolis>` 里做；自动化 shell 无法
   触发真实对话。
2. **`/work` 是 tmpfs**。`stop` / `rebuild` 后内容消失，SDK 和构建缓存也要
   重新下载。这是隔离设计的取舍。
3. **镜像源是本环境适配**。`daemon.json` 里的 daocloud 镜像只在 Docker Hub
   不可达时需要，Hub 可达时应删除。
4. **Cage 运行时仍在**。`agent/server.py` 通过 `ISOLIS_AGENT_NAME` 分流，
   未设置时走旧 Cage 路径。剥离需要单独任务和用户确认。
5. **构建耗时约 17 分钟**（2 核 8G）。主要是 Playwright Chromium 下载。
   第二个 profile 因缓存命中只需数秒。

---

## 资源清单说明

### 平台指令（`02-资源/平台指令/`）

这份归档不假设读者见过本环境，因此把当时生效的**全部平台规则与 skill 原文**
一并收录：

- `rules/`：29 条规则，包括 `guardrail.md`（安全护栏）、
  `no-read-llm-env.md`（不得读取环境 LLM Key）、
  `background-terminal-resource-limits.md`（编译必须走受管后台终端）、
  `no-delete-operations.md`、`no-system-admin-commands.md` 等。
  这些规则直接决定了本次实现的操作方式（例如为何用 background terminal 启 daemon、
  为何绝不打印 Key）。
- `skills/expanding-fuzzy-instructions/`：模糊指令展开 skill 的完整目录
  （SKILL.md、脚本、模板、场景测试）。
- `suzizhuonb666/`：用户自带规则包，含 5 条个人规则、`suzizhuo拦截.sh`
  （守护 rules 目录、逐字节回滚、权限锁定）、以及原始的
  `expanding-fuzzy-instructions` 与打包 zip。

### 项目文档（`02-资源/项目文档/`）

- `ISOLIS_PROMPT.md` 是**唯一权威规格**，第 13 节是 29 条验收清单。
  任何实现争议以它为准。
- `提示词.txt` 是同一规格的压缩版，便于快速检索关键常量与接口。
- `prompts/` 是 Cage 阶段的分阶段提示词，记录了 Task 1-12 的推进方式；
  `plans/`、`specs/` 是对应的实现计划与设计规格。

### 源码（`02-资源/源码/`）

- `cage/`：旧 Cage 运行时全部模块。归档时它仍与 Isolis 共存于
  `agent/server.py`，理解分流逻辑需要读这些文件。
- `tests-all/`：仓库全部 17 个测试文件，便于在还原后跑完整套件。
- `base-repo.bundle`：初始提交 `19436e4` 的完整 git 仓库。没有原始仓库时，
  可以先 `git clone` 这个 bundle 得到干净基线，再套用 `current-changes.patch`，
  等价于路径 A。验证：

  ```bash
  git clone "monkey code 0-> 部署agent/02-资源/源码/base-repo.bundle" /tmp/restored
  cd /tmp/restored
  git apply "/workspace/monkey code 0-> 部署agent/02-资源/配置/current-changes.patch"
  ```

- `.monkeycode/MEMORY.md`：截至归档时刻的项目记忆，含 operator 的工作流指令
  （一轮一 Task、绿了本地提交、不 push 等）。

### 环境（`02-资源/环境/`）

这不是文档，是**可 diff 的指纹**。还原后逐项比对即可判断是否一致：

| 文件 | 比对什么 |
|---|---|
| `python-version.txt` | Python 3.11.2 |
| `pip-freeze.txt` | 宿主依赖（仅标准库 + pytest） |
| `dpkg-packages.txt` | 系统包（含 docker.io、openssh-client） |
| `docker-version-full.txt` | 20.10.24+dfsg1，API 1.41 |
| `docker-info-full.txt` | overlay2、cgroup v2、镜像源 |
| `image-*-inspect.json` | 镜像 ID、大小、Entrypoint、Env |
| `image-tooled-history.txt` | 分层历史，定位耗时环节 |
| `network-isolis-net.json` | bridge 参数、icc=false |
| `container-isolis-default-inspect.json` | 完整运行时配置（密钥已脱敏） |
| `container-env-redacted.txt` | 容器环境变量，密钥显示为 `<redacted>` |

**安全说明**：`docker inspect` 的原始输出含明文 `MCAI_LLM_API_KEY`。
归档中的所有 inspect 文件、状态快照与 `snapshot.sh` 均已做脱敏处理，
并在脚本末尾加了兜底扫描，防止再次泄漏。

---

## 关键文件职责

| 文件 | 作用 |
|---|---|
| `isolis.py` | 宿主 TUI：命令解析、profile 表、端口分配、LLM 检测、镜像/容器生命周期、state |
| `agent/server.py` | 容器内 HTTP 运行时：LangChain 客户端、协议协商、工具循环、三轴记忆 |
| `agent/Dockerfile` | 镜像：JDK 17、构建工具链、Node、Playwright、Android SDK 目录 |
| `agent/install-android-sdk.sh` | 幂等的 Android 命令行工具安装 |
| `.isolis/state.json` | 运行状态（gitignored，原子写） |
| `.isolis/agent-src/` | git 源浅克隆目录（gitignored） |

---

## 还原验证清单

逐项打勾即视为与归档时刻一致：

- [ ] `docker --version` 输出 20.10.24
- [ ] `docker info` 的 driver 为 overlay2
- [ ] `/etc/docker/daemon.json` 含 daocloud 镜像源
- [ ] `docker network inspect isolis-net` 的 icc 为 false
- [ ] 两个 isolis-agent 镜像存在且 tag 匹配
- [ ] `python3 -m pytest tests/ -q` 输出 102 passed
- [ ] `python3 isolis.py profiles` 显示 tooled/strict 正确限制
- [ ] `bash 02-资源/脚本/verify.sh` 全部 `[ok]`
- [ ] `curl 127.0.0.1:18080/info` 的 `via=mc-direct`、`injected=true`
- [ ] `docker inspect isolis-default` 无 mounts、无 docker.sock、单 loopback 端口
