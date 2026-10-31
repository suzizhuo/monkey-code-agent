# 阶段 06：研究回路与风险档（Task 11）

状态：**待做**。上一阶段 05 必须已绿。先读 `00-constraints.md`、设计 §22、计划 Task 11。

你是实现 Agent。一轮只做本阶段。不要改 `plans/`。不要 push。宿主标准库 only。测试禁止外网和 Docker。

## 目标

worker 遇到不认识的站点时：`web_search` 找工具 → 装候选 → 小样验证 → 写入 playbook。`init --risk` 与 `CAGE_RISK_LEVEL` 决定对抗深度。白名单 `CAGE_CHALLENGE_HOSTS` 不随档位改变。代理池明确不实现。

## 文件

创建：

- `cage/research.py`
- `cage/playbook.py`
- `cage/risk.py`
- `tests/test_research.py`
- `tests/test_playbook.py`
- `tests/fixtures/search.html`

修改：

- `agent/server.py`：`web_search` 工具、`research_loop`、`extract_install_hint`、playbook 约定
- `cage/cli.py`：`--risk` 已在 parse_options 里占位，接到 `cmd_init`；`/info` 与 status 增字段
- `README.md`：研究回路与风险档一节

## 接口（逐字）

```
build_search_queries(task: str) -> list[str]
parse_results(html: str) -> list[dict]          # {title, url, snippet}
web_search(query: str, n: int, backend: Callable|None) -> list[dict]

playbook.key_for(url: str) -> str               # host
playbook.read(host: str, base=None) -> str
playbook.merge(host: str, entry: dict, base=None) -> str
playbook.render(entries: list[dict]) -> str

risk.RISK_MATRIX: dict[str, dict[str, bool]]
risk.risk_allows(level: str, action: str) -> bool
risk.effective_risk(state: dict, env) -> str    # --risk > CAGE_RISK_LEVEL > state > balanced

agent.web_search(query: str, n: int = 5) -> str  # JSON 或 error: search unavailable
agent.extract_install_hint(html) -> {manager, packages}
agent.research_loop(task, validate) -> str
```

`/info` 增加 `risk_level`、`playbook_entries`。

playbook 条目字段：工具、命令模板、cookie 要求、限速、失败模式、验证时间。同 host 去重留最新。

位置：容器 `/work/.cage/playbook/<host>.md`；进程 `~/.cage/playbook/<host>.md`。测试用 `base=`。

## 风险矩阵（必须与设计 22.5 一致）

| 动作键 | strict | balanced | aggressive |
| --- | --- | --- | --- |
| `public_tool` | True | True | True |
| `rate_limit_ua` | False | True | True |
| `injected_cookie` | False | True | True |
| `third_party_installer` | False | True | True |
| `auto_solve` | False | False | True |
| `third_party_resolver` | False | False | True |
| `proxy_pool` | False | False | False |

`effective_risk` 默认文档写 aggressive 是 operator 选定；计划测试写 `> balanced`。实现：显式 `--risk` / `CAGE_RISK_LEVEL` 优先，都空则 `aggressive`（验收 51），并在 README 写明。若测试断言 balanced，以测试为准并在本文件记下。先写测试再实现，测试红什么就对什么。

`auto_solve` 即使 aggressive 仍受 `CAGE_CHALLENGE_HOSTS` 约束。

## 研究回路

1. 现有工具先试
2. 失败 → `web_search`（站点名 / 下载或 downloader / 接口 三模板，`build_search_queries`）
3. 读前几个结果，`extract_install_hint` 抽 `pip install X` / `npm i -g X` / `git clone`
4. `install_packages`
5. `validate` 小样
6. `playbook.merge` → ≤800 字摘要

`web_search` 后端：`CAGE_SEARCH_CMD` 子进程 → DuckDuckGo HTML（`_ddg_backend`）→ `error: search unavailable`。测试打桩 backend，不要打真搜索。

## Step 1 写失败测试

`tests/test_research.py`：

```python
def test_build_search_queries_targets_site_and_tools():
    qs = research.build_search_queries("下载 bilibili UP 123 的视频")
    assert any("bilibili" in q.lower() for q in qs) and any("downloader" in q.lower() or "下载" in q for q in qs)

def test_web_search_stub_backend_and_unavailable():
    rows = research.web_search("q", 5, backend=lambda q, n: [{"title": "t", "url": "https://x", "snippet": "s"}])
    assert rows[0]["url"] == "https://x"
    assert research.web_search("q", 5, backend=None) == [] or isinstance(research.web_search("q", 5, backend=None), list)

def test_risk_matrix_matches_spec_table():
    assert risk.RISK_MATRIX["strict"]["public_tool"] and not risk.RISK_MATRIX["strict"]["auto_solve"]
    assert risk.RISK_MATRIX["balanced"]["rate_limit_ua"] and not risk.RISK_MATRIX["balanced"]["auto_solve"]
    assert risk.RISK_MATRIX["aggressive"]["third_party_resolver"]
    assert not risk.RISK_MATRIX["aggressive"]["proxy_pool"]
    assert risk.risk_allows("aggressive", "auto_solve") and not risk.risk_allows("balanced", "auto_solve")
```

`tests/test_playbook.py`：

```python
def test_merge_is_idempotent_per_host(tmp_path):
    e1 = {"tool": "yt-dlp", "cmd": "yt-dlp -f best URL", "cookie": "no", "rate": "2s",
          "verified_at": "2026-10-31T00:00:00Z"}
    playbook.merge("bilibili.com", e1, base=str(tmp_path))
    playbook.merge("bilibili.com", dict(e1, cmd="yt-dlp -f bestvideo URL"), base=str(tmp_path))
    text = playbook.read("bilibili.com", base=str(tmp_path))
    assert text.count("yt-dlp") == 1 and "bestvideo" in text

def test_key_for_url():
    assert playbook.key_for("https://space.bilibili.com/123") == "bilibili.com"
```

Agent 侧（可放 `tests/test_agent_server.py` 或本阶段测试文件）：

```python
def test_research_loop_replay(monkeypatch, tmp_path):
    monkeypatch.setenv("CAGE_WORK", str(tmp_path))
    monkeypatch.setattr(agent, "web_search", lambda q, n=5: [{"url": "https://example/tool", "title": "x"}])
    installed, playbook_writes = [], []
    monkeypatch.setattr(agent.install_packages, "invoke", lambda a: installed.append(a) or "installed")
    monkeypatch.setattr(agent, "write_file", lambda p="", c="": playbook_writes.append(p) or "wrote")
    out = agent.research_loop("下载 example.com 视频", validate=lambda cmd: True)
    assert installed == [{"manager": "pip", "packages": "example-dl"}]
    assert any("playbook" in p for p in playbook_writes) and "example.com" in out

def test_web_search_tool_degrades_without_backend(monkeypatch):
    monkeypatch.delenv("CAGE_SEARCH_CMD", raising=False)
    monkeypatch.setattr(agent, "_ddg_backend", lambda q, n: (_ for _ in ()).throw(OSError("blocked")))
    out = agent.web_search.invoke({"query": "q"})
    assert out.startswith("error: search unavailable")
```

`extract_install_hint` 对夹具 HTML 抽 pip/npm/git。`research_loop` 在打桩 web_search 无 hint 时，可用任务 host 推导 `{manager: pip, packages: example-dl}` 以满足上列断言，并在 `extract_install_hint` 单测里覆盖正则。

## Step 2–5

1. `python3 -m pytest tests/test_research.py tests/test_playbook.py -q` 预期 FAIL。
2. 实现到绿。
3. `python3 -m pytest tests/ -q` 全绿（不得破坏 72 个旧测试）。
4. README 补：六步回路、playbook 路径、风险表、`--risk`、`/info` 字段。

提交（本地，不 push）：

```bash
git add cage/ agent/ tests/ README.md
git commit -m "feat(research): web_search tool, research loop, host playbook, explicit risk levels"
```

停。下一阶段是 `07-phase-skills.md`。

## 验收 ID

41 web_search stub / 无后端不崩。42 研究回路可重放。43 playbook 幂等。44 风险矩阵。45 `/info.risk_level` 与 playbook 条目数。51 默认 aggressive 且白名单外 auto_solve 仍拒。
