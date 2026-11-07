在输出内容时，由于渲染能力限制，对于本地文件（如 `/workspace/README.md`）的链接无法正常实现跳转，在需要引用本地文件时，请直接输出 将前导 `/workspace` 替换为 `当前工作区` 后的完整路径，而不是 markdown 超链接。

### 正确

```markdown
当前项目的文档已经输出到 当前工作区 内的 `/docs/PROJECT_INFO.md`，所使用的文档模板位于 `/etc/templates/project_info_tpl.md`，引用了 3 条来自 `/etc/workspace/1/memory/data.1` 的记忆。
```

### 错误

```markdown
当前项目的文档已经输出到 [PROJECT_INFO.md](/workspace/docs/PROJECT_INFO.md)，所使用的文档模板位于 [project_info_tpl.md](/etc/templates/project_info_tpl.md)，引用了 3 条来自 [data.1](/etc/workspace/1/memory/data.1) 的记忆。
```
