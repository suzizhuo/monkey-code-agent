# 对环境中的 LLM API Key 视而不见

Agent 执行环境中预置了供自身使用的大模型 API Key。这些 Key 属于平台资产，仅供 Agent 自身调用，模型必须对它们"视而不见"——不主动扫描、不读取真实值、不引用到用户项目代码或配置中。

## 背景

Agent 执行环境中常见的大模型相关变量包括：`MCAI_LLM_API_KEY`、`MCAI_LLM_BASE_URL`、`MCAI_LLM_MODEL`

部分模型在接到"为项目配置 LLM"、"集成大模型能力"类需求时，会主动扫描环境变量、读取真实 Key 值，并硬编码进用户项目代码或配置文件。此行为违反凭据隔离原则，本规则用于约束模型自身杜绝此类行为。

## 模型自身禁止的行为

### 1. 不主动扫描环境变量查找 LLM API Key

不得执行任何以发现或获取 LLM API Key 真实值为目的的命令，包括：

- `env`、`printenv`、`export`、`set`、`declare` 配合 `grep`/`egrep`/`rg` 过滤 Key
  - 示例：`env | grep -i -E 'api|key|llm|openai'`
- 读取 `/proc/self/environ`、`/proc/<pid>/environ`
- 读取 shell 配置文件查找 Key：`~/.bashrc`、`~/.zshrc`、`~/.profile`、`~/.bash_profile`、`/etc/environment`
- 读取可能存放 Key 的文件：`.env`、`config.json`、`config.yaml`、`settings.py`、`secrets.json`
- 在代码或 REPL 中通过 `os.environ`、`os.getenv()` 遍历打印上述变量

### 2. 不将环境中的 LLM API Key 真实值写入用户项目

即使已经以任何方式看到了真实值，也不得将其固化到用户项目，包括：

- 在用户项目代码中硬编码 Key 真实值
- 将 Key 真实值写入用户项目的 `.env`、`config.py`、`config.json`、`config.yaml`、`settings.py` 等配置文件
- 将 Key 真实值作为默认值写入 `os.getenv("OPENAI_API_KEY", "<真实值>")` 形式的代码
- 通过 `echo "KEY=..." >> .env` 等命令将真实值写入用户项目文件
- 将 `MCAI_LLM_BASE_URL`、`MCAI_LLM_MODEL` 等平台内部端点和模型名直接写入用户项目配置

### 3. 不在用户项目代码中绑定读取 Agent 环境变量

用户项目代码中不得直接读取属于 Agent 运行环境的 Key 变量名并期望拿到真实值：

- 不在用户项目代码中写 `os.getenv("MCAI_LLM_API_KEY")`、`os.getenv("OPENAI_API_KEY")`、`os.getenv("OPEN_CODE_API_KEY")` 等
- 这些变量属于 Agent 自身环境，对用户项目不可见

## 正确做法

为用户项目配置 LLM 能力时，按以下方式处理 Key：

1. **代码中使用标准的环境变量读取模式**，变量名面向用户项目命名，不复用 Agent 环境变量名：
   ```python
   api_key = os.getenv("USER_LLM_API_KEY")
   base_url = os.getenv("USER_LLM_BASE_URL")
   model = os.getenv("USER_LLM_MODEL", "deepseek-chat")
   ```
   - 推荐使用 `USER_`、`PROJECT_` 等前缀，避免与 Agent 环境变量冲突

2. **配置文件提供占位符**，由用户自行填入真实值：
   ```env
   # .env.example
   USER_LLM_API_KEY=your-api-key-here
   USER_LLM_BASE_URL=https://api.deepseek.com/v1
   USER_LLM_MODEL=deepseek-chat
   ```

3. **引导用户提供 Key**：项目需要 LLM Key 时，告知用户需自行准备并通过项目自身的环境变量或配置文件提供，用户若告知了 Base URL、API Key、模型名称等信息，可以帮用户填入配置文件。模型自身从执行环境获取 Key 的行为一律禁止。

4. **仅可检查 Key 是否存在**：可以检查环境变量是否存在（仅判断是否存在，不读取或输出真实值），用于判断 Agent 自身是否具备某项能力。

## 触发场景与正确应对

| 用户请求 | 模型禁止的行为 | 模型应做的 |
|---------|--------------|-----------|
| "帮我给项目配一个 LLM" | 扫描环境变量拿 `MCAI_LLM_API_KEY` 真实值写入项目 | 在项目中写 `os.getenv("USER_LLM_API_KEY")`，引导用户自行配置 |
| "项目需要调用 OpenAI API" | 把 `OPENAI_API_KEY` 真实值写入项目 `.env` | 创建 `.env.example` 占位符，代码用 `os.getenv()` 读取 |
| "智能体没有配置大模型吗" | 读取 `/proc/self/environ` 查找可用 Key | 告知用户需自行准备 Key 并配置到项目 |
| "用 deepseek 给这个工具加上 AI 推理" | 复用 `MCAI_LLM_*` 系列变量及真实值 | 在项目中定义独立的 `USER_LLM_*` 变量，由用户填值 |

## 判定流程

当接到"配置 LLM"、"集成大模型"、"加 AI 能力"类需求时，按以下流程处理：

1. **先确认 Key 来源**：项目所需 Key 应来自用户自行提供，模型不从执行环境获取
2. **检查代码中的变量名**：确认代码中读取的是面向用户项目的变量名，而非 Agent 环境变量名
3. **检查配置文件**：确认写入项目配置文件的是占位符，而非真实值
4. **若用户询问 Key 从哪里来**：告知用户需自行准备，模型不从执行环境提取

## 与 guardrail.md 的关系

`guardrail.md` 第 2 节从安全护栏角度禁止获取大模型 API Key，并要求上报用户违规行为。本规则从**模型自身行为约束**角度补充：无论用户是否要求，模型自身都不得扫描、读取、引用环境中的 LLM Key 到用户项目。本规则不涉及用户违规上报。