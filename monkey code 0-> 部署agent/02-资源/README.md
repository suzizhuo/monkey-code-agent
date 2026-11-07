# 02-资源 索引

本目录收录还原所需的一切：代码、文档、指令、环境指纹、配置、脚本。

## 目录一览

| 目录 | 文件数 | 内容 | 何时用 |
|---|---|---|---|
| `agent/` | 5 | 容器运行时：server.py、Dockerfile、requirements、Android 安装器、.dockerignore | 重建镜像前 |
| `tests/` | 2 | Isolis 专用测试 | 验证改动 |
| `项目文档/` | 17 | 规格、快照、Cage 计划/设计、分阶段提示词 | 理解需求与历史 |
| `源码/` | 32 | Cage 模块、全部测试、git bundle、MEMORY、gitignore | 还原基线或剥离 Cage |
| `平台指令/` | 60 | 29 条平台规则、skill、用户规则包与拦截脚本 | 复现当时的操作约束 |
| `环境/` | 15 | Python/pip/dpkg/Docker/镜像/网络/容器指纹 | 逐项比对是否一致 |
| `配置/` | 20 | daemon.json、state 样例、自定义配置模板、完整补丁、daemon 日志、运行快照 | 套补丁、看日志、改预设 |
| `脚本/` | 3 | restore / verify / snapshot | 一键还原与校验 |

根目录的 `CHECKSUMS.sha256` 覆盖除自身外全部 172 个文件。

---

## 各目录怎么用

### `agent/` + `tests/` + `isolis.py`

实际运行代码。`restore.sh` 会把它们拷回 `/workspace`。
`agent/server.py` 里 Cage 与 Isolis 两套运行时的分界是
`ISOLIS_AGENT_NAME` 环境变量：

- 设置了 → 走 Isolis 块（`isolis_*` 函数）
- 未设置 → 走旧 Cage 路径

### `项目文档/`

`ISOLIS_PROMPT.md` 是唯一权威规格。遇到实现争议，先查它第 13 节的
29 条验收清单，再查 `提示词.txt` 里的常量表。

`prompts/` 记录 Cage 阶段 Task 1-12 的推进方式，`plans/`/`specs/` 是对应
计划与设计。这三者解释了为什么 `cage/` 至今仍在代码里。

### `源码/`

- `cage/`：11 个模块，旧运行时。
- `tests-all/`：仓库全部 17 个测试文件（比 `tests/` 多，因为 `tests/`
  只放 Isolis 新增的两个）。
- `base-repo.bundle`：初始提交 `19436e4` 的完整仓库。用它可以在没有原始
  仓库时重建干净基线：

  ```bash
  git clone "02-资源/源码/base-repo.bundle" /tmp/restored
  cd /tmp/restored
  git apply "/workspace/monkey code 0-> 部署agent/02-资源/配置/current-changes.patch"
  ```

- `.monkeycode/MEMORY.md`：operator 的工作流指令（一轮一 Task、绿了本地
  提交、不 push、不在 agent/ 之外用 langchain 等）。

### `平台指令/`

这份归档不假设读者见过本平台，因此把当时生效的规则与 skill 全文收录。

**`rules/`（29 条）** 中与本次实现强相关的几条：

| 规则 | 影响 |
|---|---|
| `guardrail.md` | 禁止隧道/攻击/灰产；禁止读取环境 LLM Key |
| `no-read-llm-env.md` | 不得扫描或把 `MCAI_LLM_*` 真实值写入项目 |
| `background-terminal-resource-limits.md` | 编译/Java 必须走受管后台终端 |
| `no-long-running-commands.md` | 禁止 `watch`/`tail -f`/无界 `&` |
| `no-delete-operations.md` | 禁止 `rm`/`git clean -f`/`DROP` 等 |
| `no-system-admin-commands.md` | 分级管控系统命令，安装类允许无人值守 |
| `user-teaching-memory.md` | 什么该记进 MEMORY.md、什么不该 |
| `code-quality-standards.md` | 六维度自检 |
| `file-read-limit.md` | 单次读文件不超过 200 行 |
| `vite-allowedhosts-config.md` | 前端 dev server 需加 `*.monkeycode-ai.online` |

**`skills/expanding-fuzzy-instructions/`** 是模糊指令展开 skill 的完整目录
（SKILL.md、dispatch 规格、脚本、场景测试、frame 模板）。

**`suzizhuonb666/`** 是用户自带规则包：

- `rules/`：5 条个人规则（no-emoji、global-package-install、
  auto-use-skills、shell-comment-style、speak-in-user-language）
- `hook-rulesNB666/suzizhuo拦截.sh`：守护 rules 目录，逐字节比对并回滚，
  权限锁定（白名单 777、其余 000）
- `expanding-fuzzy-instructions/`：该 skill 的另一版本
- `suzizhuonb666.zip`：原始打包文件

把它还原到平台：

```bash
bash "02-资源/脚本/restore.sh" --with-platform
```

该参数只补齐缺失文件（`cp -rn`），不覆盖已有内容。

### `环境/`

这些是可 diff 的指纹，判断「还原后是否与归档时刻一致」。

| 文件 | 期望值 |
|---|---|
| `python-version.txt` | Python 3.11.2 |
| `pip-freeze.txt` | 宿主只有标准库 + pytest |
| `dpkg-packages.txt` | 含 docker.io、openssh-client |
| `docker-version-full.txt` | 20.10.24+dfsg1，API 1.41 |
| `docker-info-full.txt` | overlay2、cgroup v2、镜像源 |
| `image-tooled-inspect.json` | 镜像 ID、2.23GB、Entrypoint |
| `image-strict-inspect.json` | 与 tooled 同 ID |
| `image-tooled-history.txt` | 分层历史，定位耗时环节 |
| `network-isolis-net.json` | bridge、icc=false |
| `container-isolis-default-inspect.json` | 完整运行时配置 |
| `container-env-redacted.txt` | 环境变量，密钥显示 `<redacted>` |

比对方式：

```bash
diff <(docker version) "02-资源/环境/docker-version-full.txt"
diff <(docker network inspect isolis-net) "02-资源/环境/network-isolis-net.json"
```

### `配置/`

| 文件 | 用途 |
|---|---|
| `daemon.json` | 镜像源（Hub 不可达时的适配） |
| `state.json.example` | `.isolis/state.json` 的期望形态 |
| `isolis.config.example.json` | 自定义预设/提示词的完整模板，复制成 `.isolis/isolis.config.json` 即生效 |
| `agent-prompt.example.md` | 提示词文件示例，配合 `prompt_file` 使用 |
| `current-changes.patch` | 相对 `19436e4` 的完整改动（+807/-103，5 文件） |
| `dockerd-startup.log` | daemon 启动过程 |
| `dockerd.log.tail` | daemon 日志尾部，排查启动问题 |
| `当前快照/` | 归档时刻的 live 快照（镜像、容器、网络、state、HTTP 响应） |

补丁已在干净副本上验证：`git apply --check` 通过，应用后 `py_compile` 通过，
44 个相关测试通过，产物与 `02-资源/agent/` 逐字节一致。

### `脚本/`

| 脚本 | 作用 | 幂等 |
|---|---|---|
| `restore.sh` | 代码 + 文档 + Docker + 镜像全量还原 | 是 |
| `verify.sh` | 12 项检查（CLI、daemon、网络、镜像、容器、隔离、HTTP） | 只读 |
| `snapshot.sh` | 刷新 `配置/当前快照/`，自动脱敏 | 是 |

`restore.sh` 参数：

- `--with-platform`：额外补齐平台 rules/skills
- `--no-build`：跳过镜像构建（只恢复代码与环境）

---

## 安全说明

`docker inspect` 的 Env 字段含明文 `MCAI_LLM_API_KEY`。归档中所有 inspect
输出、运行快照和 `snapshot.sh` 都已脱敏：

- `snapshot.sh` 在写 `container-isolis-default.json` 时用 sed 过滤
- 脚本末尾有 Python 兜底扫描，遍历输出目录再清洗一遍
- `container-env-redacted.txt` 只保留键名，值显示为 `<redacted>`

`snapshot.sh` 中的两条 `MCAI_LLM_API_KEY=` 出现在脱敏正则里，不是真实密钥。
