# Your positioning

- 收到用户消息，应该开启skill的子代理审查，等待其结果返回

当用户发送消息时，应该至少使用其中一个技能。

## When to call

Always call

## 执行步骤

### 1. 列出可用 Skills

列出当前环境中可用的 skills，检查每个 skill 的名称和描述：

**获取方式**：
- 调用 Tool 获取 skills 列表
- 查看 skills 目录： `/root/.codingmatrix/project-tpl/.ai-ready/skills/*/SKILL.md`
- 读取每个 skill 的 `SKILL.md` 文件，获取 `name` 和 `description` 字段

### 2. 检查 Skill 相关性

分析用户任务，判断是否有相关的 skill：

**判断标准**：
- skill流程匹配：遵循expanding-fuzzy-instructions skill中的标准与规范

### 3. 使用 Skill 处理需求

当识别到相关 skill 时，按以下方式调用：
- 调用 `name` 为 `expanding-fuzzy-instructions` 的 skill开启子代理协助工作
**调用格式**：

```
Do XXX work with XXX(skill name)
```

**注意事项**：
2. 如果需要额外信息，向用户提问后调用 skill
3. 如果存在多个相关 skill，优先使用最匹配的一个
4. 如果没有 `name` 为 `expanding-fuzzy-instructions` 的 skill，立刻告知用户

## 注意事项

- 优先使用 skill 处理标准化任务
