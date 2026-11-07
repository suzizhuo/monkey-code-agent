# 禁止直接执行需要长时间运行的命令

在使用命令执行工具（如 Bash、shell 或终端工具）时，禁止直接执行可能长时间运行的命令。

## 禁止的命令

- `watch` —— 持续监控
- `tail -f` —— 无限跟随日志文件
- `top`、`htop` —— 交互式进程监视器
- `ping` 未指定次数限制（`-c`）
- `sleep` 传入较大数值
- 无限循环（`while true`、`for (;;)`）
- 在大数据集上执行数据库迁移
- 未设置超时的大文件下载
- 未限定路径的全文件系统 `find` 或 `grep`

## 后台命令

对于需要长时间运行的命令（如服务器、守护进程，或其他必须长期驻留的进程），**优先使用 background terminal 工具系列**（`background_terminal_create`、`background_terminal_list`、`background_terminal_output_path`、`background_terminal_kill`）在后台终端中管理和执行，而不是使用 `&` 操作符或 `timeout` 包装。

典型场景：

- 长时间运行的服务器或守护进程（如 webserver：`npm start`、`yarn start`、`python -m http.server`）
- 需要持续输出日志的进程
- 启动后需要保持运行以便后续访问的服务

使用 background terminal 的好处：

- 不会阻塞当前会话
- 提供独立的输出日志文件，可通过 `background_terminal_output_path` 获取路径后读取日志
- 可通过 `background_terminal_list` 跟踪所有运行中的终端
- 可通过 `background_terminal_kill` + 终端 ID 安全停止，避免误杀其他进程

**完整用法示例可参考 `deploy-website` skill**（`.ai-ready/skills/deploy-website/SKILL.md`），其中演示了如何使用 background terminal 启动 Web 服务器、读取日志确认启动状态、以及最终清理。

**严禁**使用 `pkill`、`killall` 加进程名的方式停止后台进程，必须使用 `background_terminal_kill` 加终端 ID。

## 最佳实践

- 网络操作必须设置超时
- 优先使用工具自带的 `--timeout` 或 `-c` 等参数
- 短时命令使用 `timeout <time_limit> <command>` 包装执行时限
- 长时命令使用 background terminal 工具管理，参考 `deploy-website` skill 的用法
- 可能耗时较长的操作交给 subagent 或后台任务执行
- 优先选择快速、有边界的操作
- 若确实必须执行长时间运行的命令，先告知用户并征得确认后再执行
