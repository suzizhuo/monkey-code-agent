# Git Submodule Check Rule

在用户与模型首次对话时，必须先检查并加载所有的 Git Submodules。

## 执行时机

在开始任何任务之前，必须先执行此检查，包括但不限于：
- 代码开发或修改
- 运行测试
- 构建项目
- 代码审查
- 文档生成

## 检查步骤

1. **检查 `.gitmodules` 文件是否存在**
   - 如果文件不存在，说明项目没有使用 submodules，可以跳过后续步骤

2. **初始化并更新 submodules**
   - 如果 `.gitmodules` 文件存在，执行以下命令：
   ```bash
   git submodule update --init --recursive --depth 1
   ```

## 注意事项

- 使用 `--depth 1` 参数进行浅克隆，以减少克隆时间和存储空间
- 如果 submodule 初始化失败，需要先向用户报告错误，等待用户处理后再继续任务
- 此检查应在任务开始阶段尽早完成，避免因缺少 submodule 依赖导致后续任务失败
