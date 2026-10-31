# Cage 提示词包

本目录把 Isolis 先例、cage 设计、实现计划和当前代码收成**可直接喂给下一位 Agent 的分阶段提示词**。每份都自洽：目标、硬约束、文件、接口、测试、验收、提交格式都写在同一文件里。

## 怎么用

1. 先读 `00-constraints.md`（全局硬约束，每一阶段都生效）。
2. 再读 `00-resources.md` 和 `00-progress.md`（资源在哪、做到哪）。
3. 按阶段编号顺序执行。已完成阶段只核验、不重做；未完成阶段按该文件从红到绿做完再停。
4. 权威细节仍以 `specs/2026-10-31-cage-design.md` 与 `plans/2026-10-31-cage.md` 为准。提示词与二者冲突时停下来问 operator。

## 文件地图

| 文件 | 阶段 | 状态 |
| --- | --- | --- |
| `00-constraints.md` | 全局约束 | 现行 |
| `00-resources.md` | 全部资源清单 | 现行 |
| `00-progress.md` | 进度与提交记录 | 现行 |
| `01-phase-foundation.md` | Task 1–4 骨架/agent/TUI/serve | 已完成 |
| `02-phase-autonomy.md` | Task 5–6 自主档与 worker | 已完成 |
| `03-phase-challenge.md` | Task 7 人机验证 | 已完成 |
| `04-phase-sandbox.md` | Task 8 沙箱、审计、文档 | 已完成 |
| `05-phase-workspace-publish.md` | Task 9–10 work/key/publish | 已完成 |
| `06-phase-research.md` | Task 11 研究回路与风险档 | **待做** |
| `07-phase-skills.md` | Task 12 skill 池与提案 | **待做** |

## 执行规矩（写进每一阶段）

一轮只做当前阶段。测试：`python3 -m pytest tests/ -q`。绿了本地提交 `feat(scope): ...`。不要 force、rebase、amend。operator 没说「上传到 GitHub」就不要 push。

宿主 `cage.py` + `cage/` 只用 Python 标准库。langchain / playwright 只在 `agent/`。
