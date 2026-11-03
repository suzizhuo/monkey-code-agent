# Isolis

隔离的 LangChain agent：宿主机 TUI 负责生命周期，容器（无 Docker 则进程）跑 `agent/server.py`。平台通过 `MCAI_LLM_*` 注入网关；本仓库不内置厂商或模型名。

- 权威规范：`ISOLIS_PROMPT.md`（完整版，含 v1.1/v1.2 增补，冲突时以它为准）；`PROMPT.md`（压缩版驱动，仅供快读，冲突即废）
- 宿主只用 Python 标准库 + Docker CLI；LangChain 只在容器里
- 默认 `tooled` 档：agent 在沙箱内自配环境（JDK/SDK/Android/浏览器/包管理器），隔离红线不动

## 快速上手

```bash
python3 -m pytest tests/ -q     # 单元测试：无 Docker / 无外网 / 无 LLM
python3 isolis.py doctor        # 环境体检（缺 MCAI_LLM_* 时非零退出是正确行为）
python3 isolis.py init          # 建镜像 + 起一个隔离 agent；无 Docker 时进程模式
python3 isolis.py chat          # you> 对话；/export /import /save 可用
python3 isolis.py status        # 列表 + 隔离摘要；status --json 机器可读
```

无 Docker 的机器（CLI 缺失或 daemon 不可达）自动进入**进程模式**：
`agent/server.py` 作为宿主进程跑在 `127.0.0.1:<18080-18119>`，环境白名单、
HTTP 契约、`/files`、export/import/batch/broadcast 与容器模式完全一致；
隔离红线（无宿主挂载、无 docker.sock、无兄弟容器可达）改为如实标注的降级
运行时。相关状态落在 `.isolis/run/`（pid 元数据）、`.isolis/logs/`（agent
输出）、`.isolis/workspaces/<name>/`（workspace）。

任务文件批量跑：`tasks.txt` 每行 `名字|消息`（省略名字走当前 agent）：

```bash
python3 isolis.py batch tasks.txt --out out/
```

给所有运行中的 agent 发同一句：`python3 isolis.py broadcast "把 /work 清点一遍"`。

取回/送入沙箱文件（不经挂载）：`export alpha pull.tgz` / `import push.tgz alpha`。

## 隔离红线（不随功能放宽）

- 单端口只绑 `127.0.0.1`；`isolis-net` 关 icc；无宿主挂载、无 docker.sock
- `cap-drop ALL` + 白名单（tooled 恰好 6 个 cap）；`no-new-privileges`；tmpfs `/tmp /work /dev/shm`；pid/内存/cpu 限额
- strict：只读根 + `nobody` + 无 cap + 禁装；tooled：rw root + 6 cap + 可装
- 模型只走平台注入网关；key 不进 state、不进日志、不进打印

## 目录

| 路径 | 作用 |
| --- | --- |
| `isolis.py` | 宿主 TUI/orchestrator（标准库 + Docker CLI） |
| `agent/server.py` | 容器 runtime：LangChain 工具循环 + 有界记忆 + `/files` 交换 |
| `agent/Dockerfile` | 满血镜像：JDK17、构建链、Node、Playwright Chromium、Android SDK |
| `agent/install-android-sdk.sh` | Android SDK 幂等安装器 |
| `tests/` | 宿主 + agent 单测（`tests/helpers.py` 提供进程内 live agent；`tests/test_process_mode.py` 覆盖无 Docker 降级与协议协商） |
| `ISOLIS_PROMPT.md` / `PROMPT.md` | 完整规范 / 压缩驱动规范 |
