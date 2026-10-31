# 阶段 07：Skill 池（Task 12）

状态：**待做**。必须先完成阶段 06。先读 `00-constraints.md`、设计 §23、计划 Task 12。

你是实现 Agent。一轮只做本阶段。不要改 `plans/`。不要 push。宿主标准库 only。测试禁止外网和 Docker。

## 目标

`init` 后自动扫描本地 skill 仓库 + GitHub 高分候选。chat 首行盘点。任务命中则把 skill **复制**进 `/work/.cage/skills/<name>/`（改副本不动原件）。不够用时写改进提案。TUI 命令 `skills` / `skills adopt` / `skills proposals`。

## 文件

创建：

- `cage/skills.py`
- `tests/test_skills.py`
- `tests/fixtures/skills-pool/bili-dl/SKILL.md`
- `tests/fixtures/skills-pool/repo-audit/SKILL.md`

修改：

- `cage/cli.py`：init 扫描钩子、chat 首行、`cmd_skills`
- `cage/runtime.py`：worker 启动前复制命中 skill（能测则测，测不了就纯函数 `prepare_for_task`）
- `README.md`：Skill 池与提案一节

## 两个池

- 本地：`~/.cage/skills-repo/`，默认克隆 `CAGE_SKILLS_REPO`（`https://github.com/suzizhuo/suzizhuo-skill-repository`），`git pull --ff-only`。测试用临时目录，不要真 clone。
- 远程候选：有 `CAGE_GH_TOKEN` 走 `https://api.github.com/search/repositories?q=topic:skill&sort=stars`；否则 `web_search`。adopt 才 clone。
- 登记表：`~/.cage/skills/index.json`

SKILL.md frontmatter 至少 `name`、`description`。

## 接口（逐字）

```
scan_local(repo_dir: str) -> dict
scan_remote(token: str, query: str) -> list[dict]
merge_index(old: dict, new_entries: list[dict]) -> dict
match_skills(task: str, index: dict, k: int = 3) -> list[dict]
prepare_for_task(task: str, index: dict, workdir: str) -> list[str]
propose(name, gap, suggestion, evidence, est_gain) -> str   # 提案文件路径
render_catalog_line(index: dict, matched: list[dict]) -> str
cli.cmd_skills(args) -> str
```

`render_catalog_line` 格式：

`skills: N local, M remote (top: <name> ★<stars>), matched: <names|none>`

提案四段必须都在文件里：缺口、建议、证据、预估收益。路径 `~/.cage/skills/proposals/<ts>-<name>.md`。

`init --no-scan` 跳过。扫描失败用旧 index，不阻塞 init。

主代理只看到「用了/更新了/提案了哪个 skill」一行。SKILL.md 正文只进 worker 副本上下文。

自动晋升候选不入正式池；operator 用 `skills adopt <name>`。

## Step 1 失败测试

```python
def test_scan_local_builds_index_and_dedupes(tmp_path):
    repo = tmp_path / "pool"
    (repo / "bili-dl").mkdir(parents=True)
    (repo / "bili-dl" / "SKILL.md").write_text(
        "---\nname: bili-dl\ndescription: 用 yt-dlp 下载哔哩哔哩视频/列表\n---\n# steps", encoding="utf-8")
    idx = skills.scan_local(str(repo))
    assert idx["bili-dl"]["source"] == "local" and "哔哩哔哩" in idx["bili-dl"]["description"]
    idx2 = skills.merge_index(idx, skills.scan_local(str(repo)))
    assert list(idx2) == ["bili-dl"]

def test_match_skills_hits_bili_and_empty_pool():
    idx = {"bili-dl": {"name": "bili-dl", "description": "用 yt-dlp 下载哔哩哔哩视频/列表"},
           "repo-audit": {"name": "repo-audit", "description": "仓库体检与依赖审计"}}
    hits = skills.match_skills("下载B站这个人的所有视频", idx)
    assert hits[0]["name"] == "bili-dl"
    assert skills.match_skills("下载B站视频", {}) == []

def test_prepare_copies_isolated_and_proposals(tmp_path):
    repo = tmp_path / "pool"; (repo / "bili-dl").mkdir(parents=True)
    (repo / "bili-dl" / "SKILL.md").write_text("---\nname: bili-dl\ndescription: x\n---\n", encoding="utf-8")
    work = tmp_path / "work"
    index = {"bili-dl": {"name": "bili-dl", "description": "x", "path": str(repo / "bili-dl")}}
    skills.prepare_for_task("下载视频", index, str(work))
    (work / ".cage" / "skills" / "bili-dl" / "SKILL.md").write_text("modified", encoding="utf-8")
    assert (repo / "bili-dl" / "SKILL.md").read_text(encoding="utf-8") == "---\nname: bili-dl\ndescription: x\n---\n"
    path = skills.propose("bili-dl", "1080P 需 SESSDATA 而 skill 未说明",
                          "增加 cookie 参数与降级说明", "audit#12", "-3 步")
    text = open(path, encoding="utf-8").read()
    assert all(k in text for k in ("SESSDATA", "cookie", "audit#12", "-3 步"))

def test_render_catalog_line_format():
    idx = {"a": {"name": "a", "stars": 2300, "source": "remote"}, "b": {"name": "b", "stars": 10, "source": "local"}}
    line = skills.render_catalog_line(idx, [{"name": "bili-dl"}])
    assert line.startswith("skills: ") and "remote" in line and "matched: bili-dl" in line
```

夹具 `tests/fixtures/skills-pool/bili-dl/SKILL.md` 与 `repo-audit/SKILL.md` 按上面 frontmatter 写真实短文，扫描测试也可指向该目录。

cli 钩子测试若 `cmd_init` 太重，可把扫描抽成 `cli.scan_skills_on_init(scan=True)` 单测；chat 首行用 `cli.print_chat_header(name)`。断言输出含 `skills:`，且 `index.json` 落在 `CAGE_HOME/skills/`。

`scan_remote` 必须可注入 HTTP 函数，测试不要打 `api.github.com`。

## Step 2–6

1. `python3 -m pytest tests/test_skills.py -q` 预期 FAIL。
2. 实现 `scan_local`（re 解 frontmatter）、`match_skills`（分词重叠）、`prepare_for_task`（`shutil.copytree`）、`propose`、`cmd_skills` 三个子命令。
3. HELP_TEXT 增加 `skills`。
4. `python3 -m pytest tests/ -q` 全绿。
5. README：本地池 vs 远程候选、`--no-scan`、`skills`/`adopt`/`proposals`、微改隔离、提案四段。

提交（本地，不 push）：

```bash
git add cage/ agent/ tests/ README.md
git commit -m "feat(skills): registry scan, task matching, isolated adopt, improvement proposals"
```

然后停，向 operator 汇报：文件、pytest 红→绿、验收 46–51、未决事项。

## 验收 ID

46 index 去重。47 match 命中 bili / 空池 []。48 副本隔离。49 提案四段。50 chat 首行 skills 盘点。51 与阶段 06 的风险默认一起成立。
