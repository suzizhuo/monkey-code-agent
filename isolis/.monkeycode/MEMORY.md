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

[Isolis isolis.py freeze partially lifted for H.7]
- Date: 2026-11-02
- Context: Operator instruction during the H.7 degrade fix session
- Instructions:
  - Isolis 的 isolis.py 冻结约束已由操作者明确指示局部解除——仅限 H.7 降级修复：新增 docker_usable() 与 _runtime_and_mode()，chat/status/stop/rebuild/init 全部走该路径，降级时只打印一行 notice，init 重新写入记录，"MonkeyCode LLM was not injected" 追加 "(run doctor to see the checklist)"。
  - 除此之外 isolis.py 仍保持只读，直到操作者另行指示。
  - 操作者 2026-11-02 的明确修复指示优先于此前的 isolis.py 冻结约束。

[Isolis H.7 degrade conventions]
- Date: 2026-11-02
- Context: Discovered by Agent while writing session notes for the H.7 degrade fix
- Category: Workflow & Collaboration
- Instructions:
  - 状态记录 mode 为 docker 但本机 Docker CLI/daemon 不可用时，降级到 process 模式执行，只打印一行 notice。
  - init 重新写入状态记录，把生效模式持久化。
  - 会话 notes 一律放 logs/，不建 docs/。
  - 诊断输出只打印 key 长度与前缀，绝不打印 key 本身。
