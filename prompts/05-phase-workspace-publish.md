# 阶段 05：工作区与 Git 发布（Task 9–10）

状态：**已完成**。必读设计 §21、计划 Task 9–10。

## Task 9 `work`

创建：`cage/workspace.py`、`tests/test_workspace.py`。改 `agent/server.py` 的 `GET /files`、`cage/cli.py` 的 `cmd_work`。

接口：

- `render_listing(path, entries) -> str`：首行 split 后第 2 个 token 是 `1`；目录名带 `/`
- `parse_selection(text, count) -> int`：非数字/越界抛 `CageError`
- `host_dest_allowed(dest, cwd) -> bool`：只允许 cwd 之下
- `list_workspace` / `read_workspace` / `extract`
- `GET /files?path=&read=`：list `{ok,path,entries:[{name,dir,size,mtime}]}`；read `{ok,content}`；越界 400
- TUI：数字进目录或读文件；`r N` 读；`x N dest` 抽出；`p` 转 publish；`q` 退出

`extract` 进程模式 `shutil.copy2`（测试用绝对 dest，校验放在 `cmd_work` 不放在 `extract` 内部）。

`http_get_json` 在 `tests/helpers.py`。

红：`cannot import name 'workspace'`。绿后：

`feat(work): numbered workspace browser over /files with read and extract`

落地：`944353d`。验收 36。

## Task 10 `key` / `publish` / `git_publish`

创建：`cage/publish.py`、`tests/test_publish.py`。改 agent `git_publish`、cli `key`/`publish`。runtime 已透传 `CAGE_GH_TOKEN` / `CAGE_SSH_KEY_B64` / `CAGE_PUBLISH_REPO`。

接口：

- `ensure_ssh_key(keydir) -> (priv, pub)` 幂等，`ssh-keygen -t ed25519 -N ""`
- `remote_for("pat:tok", "git@github.com:me/x.git")` 以 `https://x-access-token:tok@github.com/` 开头
- `remote_for("ssh:/k/id", repo)` 原样 SSH URL
- `git_env("ssh:/k/id")` → `GIT_SSH_COMMAND` 含 `ssh -i /k/id` 与 `accept-new`
- `publish_plan(workdir, repo, message, branch, auth) -> list[list[str]]`：repo 空抛 `CageError`；序列含 add/commit/push 与 branch
- `run_publish(plan, env) -> {ok, sha, files, error_kind}`，error_kind 为 auth/remote/rejected/network
- agent `git_publish.invoke({repo, message, branch?})`
- `cli.cmd_key` / `cli.cmd_publish`

`git_publish` 进 worker 白名单与 `DESTRUCTIVE_TOOLS`。orchestrator 经 delegate 完成。

审计：`token` 脱敏；`repo` 写成 `github.com/me/x.git` 形式。

空 repo 拒绝，防止推到不明处。`CAGE_GH_CREATE_REPO` 本期不实现。

红：`cannot import name 'publish'`。绿后：

`feat(publish): ssh/PAT git publishing from workspace, key bootstrap, audit redaction`

落地：`69d10b5`。验收 37–40。

README 已补「工作区在哪/怎么看」「上传到我的 GitHub」和 tmpfs 死亡线。help 含 `work/key/publish`。

核验：`python3 -m pytest tests/test_workspace.py tests/test_publish.py tests/test_acceptance.py -q && python3 cage.py help`
