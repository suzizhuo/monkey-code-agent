# Local File Upload Guide Rule

When the user mentions a file on their local machine and you need to read it, guide them to upload the file to the workspace first.

## Guidelines

If you encounter a situation where:
- The user references a file path that appears to be on their local machine (not in the current workspace)
- You need to access or read this file to complete the user's request
- The file does not exist in the current workspace

Then you should guide the user to upload the file by following these steps:

1. **Explain the situation**: Let the user know that you cannot access files on their local machine directly
2. **Provide upload instructions**: Guide them with the following steps:
   - 在界面右上角找到「文件」按钮打开文件管理面板
   - 点击面板右上角的下拉菜单
   - 选择「上传文件」选项
   - 上传需要的文件到工作空间

3. **Wait for confirmation**: After the user uploads the file, confirm the file path in the workspace before proceeding

## Example Scenarios

### Scenario 1: User mentions a local file path
```
User: "请帮我分析一下 ~/Documents/report.pdf 这个文件"
Assistant: "我无法直接访问您本地机器上的文件。请您通过以下步骤将文件上传到工作空间：
1. 在界面右上角点击「文件」按钮打开面板
2. 点击面板右上角的下拉菜单
3. 选择「上传文件」选项
4. 上传 report.pdf 文件

上传完成后，请告诉我文件在工作空间中的路径，我就可以帮您分析了。"
```

### Scenario 2: User asks to read a non-existent file
```
User: "读取 /Users/john/data.csv 并分析数据"
Assistant: "该文件路径似乎指向您的本地机器。我需要您先将文件上传到工作空间：
1. 在界面右上角的「文件」按钮
2. 点击下拉菜单，选择「上传文件」
3. 上传 data.csv 文件

上传后我就可以读取并分析数据了。"
```

## Exceptions

- If the file path is within the current workspace directory, proceed to read it directly
- If the user has already uploaded the file and provided a workspace path, use that path
- For files that are clearly system files or configuration files (e.g., `/etc/hosts`), explain that those cannot be accessed for security reasons

## Rationale

- The AI agent runs in a sandboxed workspace and cannot access files on the user's local machine
- Guiding users to upload files ensures they can complete their tasks successfully
- Clear instructions improve user experience and reduce confusion
