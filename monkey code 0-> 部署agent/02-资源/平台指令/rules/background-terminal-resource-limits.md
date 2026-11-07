# 编译与 Java 命令后台资源限制

在本机执行可能消耗大量资源的命令时，必须避免 CPU、内存或 I/O 无约束增长。对于本规则覆盖的命令，必须使用 `background_terminal_create` 创建受管理的后台终端，不得直接使用 OpenCode 自带的 `bash`、`exec` 或其他同类命令执行工具。

## 强制触发条件

以下命令必须通过 `background_terminal_create` 执行：

- 所有编译、构建和打包任务，例如 Maven、Gradle、Go、Rust、C/C++、Make、CMake，以及 Node.js 前端构建
- 所有直接或间接调用 `java` 或 `javac` 的命令
- 包含上述任务的复合 shell 命令

普通文件查询、Git 状态检查等低风险命令不在本规则覆盖范围内，可以继续使用 OpenCode 自带的命令执行工具。

## 创建前检查现有终端

每次调用 `background_terminal_create` 前，必须先调用 `background_terminal_list`：

- 查看所有运行中的 terminal，避免重复启动相同服务或构建任务
- 汇总运行中 terminal 的 `memory.max` 配额，计算当前已分配的内存限制总量
- 新增任务的 `memory_percent` 与既有运行中任务的内存限制总量相加，不得超过环境总内存的 85%
- 如果总额度将超过 85%，不得创建新 terminal；必须先通过 `background_terminal_kill` 清理不再需要的任务，或降低新任务的 `memory_percent`
- 未设置 `memory_percent` 的既有任务不计入该预算，但应在继续创建任务前确认它不会成为新的内存风险
- 即使新任务不需要设置 `memory_percent`，也必须先执行 `background_terminal_list` 检查现有任务
- **注意：由于本开发环境有 内存气球，计算内存百分比时请按 free 命令返回的总内存 (Total Mem) 进行计算，而不是按剩余可用内存进行计算。**

## 按需选择限制

先根据命令用途、项目规模、并行参数、JVM 参数和已有运行数据判断风险，再选择必要的限制。不要机械地同时填写所有限制参数；没有对应风险时应省略该参数，避免限制过严导致进程异常。

### 执行时间

- 使用 `timeout` 为预计执行时间保留合理余量
- `timeout: 0` 表示不限制运行时间，仅用于明确需要长期运行的任务
- 编译和构建任务应尽量设置合理超时，避免异常任务无限占用资源

### 内存

- 只有预计任务峰值内存可能超过主机总内存 30% 时，才设置 `memory_percent`
- 根据预估峰值和安全余量，在 30%-70% 范围内选择合适值
- 单个任务的 `memory_percent` 绝对不得超过 70%
- 新增任务还必须遵守“创建前检查现有终端”中的 85% 总预算
- 轻量命令或没有高内存风险的任务应省略 `memory_percent`

### CPU

- 仅对高并行或明显 CPU 密集型任务设置 `cpu_percent`
- `cpu_percent: 100` 表示一个完整 CPU 核，`200` 表示两个完整 CPU 核
- 应结合任务并行度和环境规模设置，不要为了形式完整而限制普通轻量任务

### 文件描述符

- 仅当任务存在文件句柄失控风险，或工具链确实会同时打开大量文件时设置 `max_open_fds`
- 不要随意设置过低的文件描述符上限，以免构建工具因无法打开正常所需文件而失败

## 参数示例

轻量 Java 命令只需要合理超时：

```text
background_terminal_create(
  command: "java -version",
  timeout: 10000
)
```

常规 CPU 密集型构建可以限制 CPU 和执行时间；没有证据表明内存可能超过主机总内存 30% 时，不设置内存限制：

```text
background_terminal_create(
  command: "cd /workspace/project && ./mvnw test",
  timeout: 1200000,
  cpu_percent: 200
)
```

预计高内存的大型构建才增加内存限制。创建前必须先确认运行中任务的内存配额加上 50% 后仍不超过环境总内存的 85%：

```text
background_terminal_create(
  command: "cd /workspace/project && ./gradlew build --max-workers=4",
  timeout: 1800000,
  memory_percent: 50,
  cpu_percent: 300
)
```

示例参数只用于说明选择方法。实际执行时必须根据当前命令、现有 terminal 和环境调整，不得无条件照搬。

## 创建失败与运行检查

`background_terminal_create` 请求 memory 或 CPU 限制但 cgroup v2 无法完整应用时，创建会失败，命令不会以无约束或部分约束方式运行。此时不得回退到 OpenCode 自带的 `bash`、`exec` 或其他同类工具直接执行；必须向用户报告失败原因，并在清理不必要 terminal、降低请求限制或修复 cgroup 环境后重试。

创建后应根据任务进度使用以下工具检查：

- `background_terminal_output_path`：获取输出文件路径并读取日志
- `background_terminal_list`：检查运行状态、实际资源限制、峰值内存和 OOM 状态
- `background_terminal_kill`：停止不再需要或异常的任务

不得在限制创建失败时声称命令已受到资源约束，也不得通过命令末尾添加 `&` 绕过 terminal 管理。

## 与长时间运行命令规则的关系

长期驻留的服务器、守护进程和耗时构建都使用 background terminal 工具系列管理。启动前必须先用 `background_terminal_list` 检查现有终端，运行后通过输出路径观察日志，并用 `background_terminal_kill` 加 terminal ID 停止。长期运行任务仍须遵循 `no-long-running-commands.md`。
