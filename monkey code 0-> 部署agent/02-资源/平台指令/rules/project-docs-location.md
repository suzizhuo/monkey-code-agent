# Project Documentation Location Rule

When the user mentions project documentation, read from the `.monkeycode/` directory within the project.

## Directory Structure

- `.monkeycode/docs/` - Project documentation
- `.monkeycode/specs/*/` - Historical feature specifications, each subdirectory contains:
  - `requirements.md` - Requirements document
  - `design.md` - Design document
  - `tasklist.md` - Implementation task list

## Guidelines

### Reading Project Documentation

When the user asks about project documentation, read files from `.monkeycode/docs/`.

### Finding Specific Feature Specifications

When the user mentions a specific feature or requirement:

1. List subdirectories under `.monkeycode/specs/`
2. Find the subdirectory that best matches the user's description
3. **Confirm with the user** whether the selected directory is correct before proceeding
4. After confirmation, read the relevant files (`requirements.md`, `design.md`, `tasklist.md`) and execute the user's requested task

### Confirmation Example

As you think `.monkeycode/specs/2006-01-02-feature-xxx` maybe the most likely spec folder, 

1. Read existing markdown files in the folder
2. Compose a summary of the spec in one line
3. Confirm with the user in the following style.

```
Found the following specification directories that may match your request:
- `2006-01-02-feature-xxx`: Spec Summary

Is this the correct feature specification? Please confirm before I proceed.
```
