# MCP 文档查询优先级规则

当用户或模型需要了解开源工具或库的详细用法时，在使用模型记忆前，务必优先尝试使用 MCP 相关工具查询在线文档。

## 触发条件

以下情况需要触发文档查询流程：

1. **用户询问开源库/工具的用法**
   - "如何使用 XXX 库"
   - "XXX 框架的最佳实践"
   - "XXX 工具怎么配置"

2. **需要查看最新文档**
   - 用户要求查看官方文档
   - 库/工具有版本更新
   - 需要最新的 API 信息或配置示例

3. **模型不确定具体用法**
   - 库的 API 文档可能已过时
   - 需要验证最新的配置方式
   - 需要查看官方示例代码

## 可用的 MCP 工具

### 1. resolve-library-id

用于解析和查找开源库的标识符。

**用途**：获取库的规范 Context7 兼容标识符（格式：`/org/project` 或 `/org/project/version`），用于后续查询文档。

**必需参数**：
- `query`：用户需要帮助的问题或任务描述
- `libraryName`：要搜索的库名称

**返回信息**：
- Library ID：Context7 兼容标识符
- Name：库名称
- Description：库描述
- Code Snippets：可用代码示例数量
- Source Reputation：权威性指示（High, Medium, Low, Unknown）
- Benchmark Score：质量指示（100 为最高分）
- Versions：可用版本列表

**使用示例**：
```
resolve-library-id(query: "如何使用 React hooks", libraryName: "React")
resolve-library-id(query: "Vue 3 响应式 API", libraryName: "Vue")
resolve-library-id(query: "Express.js 中间件", libraryName: "Express.js")
```

### 2. query-docs

用于查询库或工具的在线文档和代码示例。

**用途**：获取库的官方文档、API 参考、使用示例等详细信息。

**必需参数**：
- `libraryId`：Context7 兼容标识符（格式：`/org/project` 或 `/org/project/version`）
- `query`：具体的查询问题或需求

**返回信息**：
- 官方文档内容
- 代码示例
- API 参考
- 配置说明

**使用示例**：
```
query-docs(libraryId: "/facebook/react", query: "useState hook 用法")
query-docs(libraryId: "/vuejs/core", query: "Composition API reactive 函数")
query-docs(libraryId: "/expressjs/express", query: "中间件开发最佳实践")
```

**重要限制**：
- 每个查询最多调用 3 次，若无法找到所需信息，使用已有的最佳结果
- 若第一个查询失败，可以调整查询词重试，但要计入次数限制

## 执行流程

### 步骤 1：识别查询需求

检查用户输入是否涉及以下情况：
- 询问开源工具/库的用法
- 需要查看官方文档
- 需要最新的配置示例

### 步骤 2：使用 resolve-library-id 解析库标识

如果用户询问的库名称不确定或需要获得规范的 Context7 标识符，使用 `resolve-library-id` 工具。

**示例**：
```
用户输入："怎么用 React 的 hooks？"
执行：
  - query: "怎么用 React 的 hooks？"
  - libraryName: "React"
结果：获得库的规范标识符，如 /facebook/react
```

### 步骤 3：使用 query-docs 查询文档

基于解析结果的 libraryId，使用 `query-docs` 工具查询相关的在线文档。

**示例**：
```
用户输入："怎么用 React 的 hooks？"
执行：
  - libraryId: "/facebook/react"
  - query: "useState hook 用法和最佳实践"
结果：获得官方文档中关于 Hooks 的详细信息和代码示例
```

### 步骤 4：综合结果

将查询到的在线文档与模型知识相结合，为用户提供最准确、最新的回答。

## 查询优先顺序

1. **优先使用 MCP 工具查询在线文档**
   - 确保获得最新的官方信息
   - 避免使用过时的记忆信息
   - 获得权威的代码示例

2. **无缝回退到模型记忆**
   - 若工具无法访问或不可用，直接使用模型记忆中的信息
   - 无需向用户反馈工具不可用的情况
   - 继续为用户提供有帮助的回答

3. **补充说明**
   - 结合官方文档的最新信息与项目实际需求
   - 根据 Source Reputation 和 Benchmark Score 评估信息质量
   - 提供更加准确和及时的建议

## 查询调用限制

- **最多调用 3 次**：对单个查询 `query-docs` 最多调用 3 次
- **计数方式**：每次调用 `query-docs` 都计入限制，包括不同关键词的查询
- **超出限制处理**：如果 3 次查询后仍未找到所需信息，使用已有的最佳结果回答用户

## 注意事项

- **始终优先查询**：即使模型记忆中有相关信息，也应优先尝试查询在线文档
- **无缝回退**：当 MCP 工具无法访问时，直接使用模型记忆，不向用户反馈工具不可用
- **库标识规范化**：优先使用 `resolve-library-id` 获得规范的 Context7 标识符，避免手动猜测
- **版本指定**：如果用户明确提到版本，使用对应版本的 libraryId（如 `/facebook/react/v18.0.0`）
- **选库质量指标**：
  - 优先选择 Source Reputation 为 High 或 Medium 的库
  - 参考 Benchmark Score 评估质量（100 为最高）
  - 选择 Code Snippets 数量多的库（文档示例丰富）
- **调用限制**：严格遵守 `query-docs` 最多 3 次调用的限制
- **查询词优化**：
  - 第一次查询用通用关键词（如 "useState hook"）
  - 若结果不理想，第二次查询用更具体的需求描述（如 "useState hook 性能优化"）
  - 第三次为最后尝试，选择最能直达用户需求的查询词
- **缓存利用**：MCP 工具可能会缓存查询结果，无需重复查询完全相同的内容
- **用户体验**：多数查询应在 1-2 秒内完成，不会显著增加响应时间

## 与其他规则的关系

- **auto-use-skills.md**：MCP 工具查询是获取信息的补充手段，不冲突
- **user-teaching-memory.md**：MCP 查询结果可以作为项目知识被记录到 MEMORY.md
- **code-quality-standards.md**：使用最新的官方文档确保代码质量和最佳实践

## 示例场景

### 场景 1：用户询问库的用法

```
用户输入："如何在 Vue 3 中使用 Composition API 创建响应式数据？"

执行流程：
1. resolve-library-id(
     query: "如何在 Vue 3 中使用 Composition API",
     libraryName: "Vue"
   )
   结果：获得 /vuejs/core

2. query-docs(
     libraryId: "/vuejs/core",
     query: "Composition API reactive 函数创建响应式数据"
   )
   结果：获得官方文档和代码示例

3. 综合结果为用户提供最新的代码示例和解释
```

### 场景 2：需要验证配置方式

```
用户输入："Webpack 5 怎么配置代码分割？"

执行流程：
1. resolve-library-id(
     query: "Webpack 5 代码分割配置",
     libraryName: "Webpack"
   )
   结果：获得 /webpack/webpack 或特定版本

2. query-docs(
     libraryId: "/webpack/webpack",
     query: "code splitting 配置方式"
   )
   结果：获得官方推荐的配置方式

3. 如果结果不够详细，可选择第二次查询（3 次限制内）：
   query-docs(
     libraryId: "/webpack/webpack",
     query: "code splitting 最佳实践示例"
   )
```

### 场景 3：查询最新的 API 参考

```
用户输入："Node.js 的 fs.promises API 有哪些方法？"

执行流程：
1. resolve-library-id(
     query: "Node.js fs.promises API 参考",
     libraryName: "Node.js"
   )
   结果：获得 /nodejs/node 或对应版本

2. query-docs(
     libraryId: "/nodejs/node",
     query: "fs.promises API 方法列表和用法"
   )
   结果：提供最新的 API 列表和使用示例
```
