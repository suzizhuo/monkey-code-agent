# Auto Project Wiki Rule

When the user's task involves project documentation generation or new project initialization, automatically invoke the `/project-wiki` skill to assist.

## Trigger Conditions

Invoke the project-wiki skill when any of the following conditions are met:

1. **Generate Documentation**
   - User mentions "generate docs", "create documentation", "build wiki", "document the project"
   - Need to generate complete project documentation for existing code

2. **Update Documentation**
   - User mentions "update docs", "sync documentation", "refresh wiki", "update the documentation"
   - Code has changed and documentation needs to be synchronized

3. **New Project Setup**
   - User mentions "new project", "start from scratch", "plan project", "initialize project"
   - Need to plan a new project and generate initial documentation

## How to Use

When the above trigger conditions are detected, invoke the skill:

```
/project-wiki
```

The skill will automatically detect the project status and select the appropriate mode (new project planning, full generation, or sync/refresh) to execute.
