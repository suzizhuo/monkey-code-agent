# User Instruction Memory

This file records user instructions, preferences, and teachings for reference in future interactions.

# Format

## User Instruction Entry
User instruction entries should follow this format:

[User Instruction Summary]
- Date: [YYYY-MM-DD]
- Context: [Mentioned scenario or time]
- Instructions:
  - [Content of user teaching or instruction, described line by line]

## Project Knowledge Entry
Entries discovered by the Agent during task execution should follow this format:

[Project Knowledge Summary]
- Date: [YYYY-MM-DD]
- Context: Discovered by Agent while performing [specific task description]
- Category: [Operations & Deployment|Build Methods|Testing Methods|Troubleshooting & Debugging|Workflow & Collaboration|Environment Configuration]
- Instructions:
  - [Specific knowledge points, described line by line]

# Deduplication Strategy
- Before adding a new entry, check for similar or identical instructions.
- If a duplicate is found, skip the new entry or merge it with the existing one.
- When merging, update the context or date information.
- This helps avoid redundant entries and keeps the memory file tidy.

# Entries

[Cage implementation workflow]
- Date: 2026-10-31
- Context: Operator instructions for implementing cage from specs/plans
- Instructions:
  - One Task per round. After green tests, local-commit with feat(scope): message. Do not push, force, rebase, or amend.
  - Operator saying 上传到 GitHub / push 时才 push origin；平时 cage Task 仍只本地提交。
  - 分阶段提示词放 `prompts/`，不要建 `docs/`。
  - Do not modify specs/ or plans/. Do not create docs/.
  - Host code (cage.py, cage/) uses the Python standard library only. langchain/playwright stay in agent/.
  - Names and constants must match plan Interfaces verbatim. Create only files listed for the current Task.
  - Tests: python3 -m pytest tests/ -q. No network, Docker, or MCAI_LLM_* usage.
  - No subagents and no parallel task work. Stop and ask on spec/plan conflicts.
  - Stop after each Task and wait for approval before starting the next.

[Project environment]
- Date: 2026-10-31
- Context: Discovered by Agent while starting cage Task 1
- Category: Environment Configuration
- Instructions:
  - Runtime Python is 3.11.2; the plan asks for 3.12+. Continue on 3.11 unless the operator says otherwise.
  - pytest is installed (9.2.0). Run tests with python3 -m pytest tests/ -q.
  - Authoritative design/plan files live at specs/2026-10-31-cage-design.md and plans/2026-10-31-cage.md.
