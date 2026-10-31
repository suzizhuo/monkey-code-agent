# 阶段 04：沙箱硬化与验收文档（Task 8）

状态：**已完成**。必读设计 §18–§19、计划 Task 8。

## 目标

工具调用审计落到宿主 JSONL；watchdog 杀掉卡死 worker；进程模式默认禁装；README 成为云环境唯一入口；验收测试闭环。

## 已有可复用

`autonomy.harvest_audit_lines`、`workers.watchdog_start` 在 Task 5/6 已实现。本阶段补：

- `runtime.process_spawn_args(..., process_install=False)` 默认 `CAGE_INSTALL=0`
- `runtime.log_source(handle)`
- `cli.cmd_status` 增行 `watchdog:` 与 `work:`
- `ProcessRuntime.start` 读 `CAGE_PROCESS_INSTALL`
- `tests/test_sandbox.py`、`tests/test_acceptance.py`
- README：TUI 表、自主、子代理、人机验证、沙箱与审计、环境变量全表

`harvest_audit_lines`：逐行 `json.loads`，只落含 `ts/agent/tool` 的行，坏行跳过。

watchdog：`threading.Timer(seconds, os.killpg, [os.getpgid(pid), SIGKILL])`，daemon=True。

## 测试要点

```python
assert autonomy.harvest_audit_lines(lines, path) == 1
workers.watchdog_start(proc.pid, 1); time.sleep(2.5); assert proc.poll() is not None
spec = runtime.process_spawn_args("d", 18080, "tooled", {}, PROFILES["tooled"], process_install=False)
assert spec["env"]["CAGE_INSTALL"] == "0"
# cmd_status 输出含 watchdog 与 work
```

冒烟：`python3 -m pytest tests/ -q && python3 cage.py doctor && python3 cage.py profiles && python3 cage.py help`

doctor 因无注入 exit 1 是正确。

绿后：

`feat(sandbox): audit harvest, watchdog, process-mode hardening, docs, acceptance`

落地：`af74e21`。验收 27、29。
