# User Instruction Memory Rules

## Description

When users teach or instruct the AI assistant on how to perform tasks or act in a certain way during conversations, these teachings should be recorded in the `.monkeycode/MEMORY.md` file located in the repository root. Additionally, when the Agent discovers valuable project knowledge during task execution, it should proactively record these findings into the same file.

### Core Principle of Recorded Content: Record "How to Do," Not "What Was Done"

MEMORY.md only records user instructions regarding the model's **behavioral patterns** (i.e., "how something should be done"), not the **specific tasks** the user asked the model to complete (i.e., "what was done").

Correct Examples (Recording Behavioral Patterns):
- "Run `npm run lint` after every code change" — This is a behavioral instruction.
- "Use pnpm for this project, not npm" — This is a behavioral instruction.

Incorrect Examples (Recording Specific Tasks):
- "The user asked me to fix a bug on the login page" — This is a task record and should not be written.
- "Completed the CRUD interfaces for the user management module" — This is a task record and should not be written.

---

## Exclusions (Strictly Prohibited from Recording)

**The following content is absolutely NOT allowed in MEMORY.md. Before each record attempt, you MUST check against the following exclusion list item by item. If any item matches, abandon the record.**

### 1. Design and Code Implementation Requirements

User requirements regarding UI layout, page structure, component patterns, code architecture, implementation methods, etc., during development. These requirements are already reflected in the code and can be obtained by reading the code; no extra memory is needed.

> Judgment Criterion: Can this information be learned by reading the code? For example — how many grid columns a page uses, which shared module a component should read metadata from, field mapping required before submission, what architectural pattern the backend uses — these are all in the code and don't need to be memorized.

### 2. Specific Implementation Details of a Development Task

Records of feature implementation, technical details, code architecture explanations, final verification results, etc., fall under the category of "what was done." The code itself is the best documentation.

> Judgment Criterion: Is this content describing "what was done in this development task" or "how it should be done every time in the future"? The former should not be recorded; the latter may be recorded.

### 3. Content Already Existing in Other Documents

If content has already been written into `.monkeycode/docs/`, `Collaborative Documents/`, `Engineering Development Progress.md`, or other project documentation, do NOT record it again in MEMORY.md. MEMORY.md is not a document index.

> Judgment Criterion: Is this information already fully documented elsewhere in the project? If yes, MEMORY.md does not need another copy.

### 4. Product Naming and Brand Expression Decisions

Product naming, brand copywriting, visual expression styles, etc., belong to the scope of product documentation, not MEMORY.md.

> Judgment Criterion: Is this content about "what the product is called, how the product speaks, what the product looks like"? If yes, it belongs to product documentation.

### 5. Information Directly Obtainable by Reading Code

Interface definitions, type declarations, function signatures, module paths, file paths, constant definitions, code logic flows, etc. This information can be obtained by exploring the code.

> Judgment Criterion: If another Agent can find the answer by searching the codebase using Grep/Glob tools, do not record it.

### 6. Obvious Content

Basic information that doesn't require special memorization, such as what language or framework the project uses.

---

## Permitted Content Scope

MEMORY.md **only records** content within the following categories:

| Category | Description | Correct Examples |
|----------|-------------|------------------|
| **Operations & Deployment** | Server addresses, deployment directories, service names, health check URLs, deployment processes | "Production server public IP is xxx, SSH user is ubuntu" |
| **Build & Compilation** | Build commands, build tools, build verification steps | "Frontend build: `cd /workspace/business-toolkit && npm run build`" |
| **Troubleshooting & Debugging** | Troubleshooting workflows, debugging methodologies, common issue investigation steps | "When a tool is unavailable, follow the 'frontend page -> route mapping -> backend definition -> authentication -> interface retest' chain to confirm layer by layer" |
| **Workflow & Collaboration** | Multi-Agent collaboration norms, development processes, commit strategies, progress documentation systems | "Different Agents must be responsible for different modules. After each modification, immediately git add + commit + push" |
| **Environment Configuration** | Special configurations for the development environment, environment variables, tool availability | "The apply_patch tool does not exist in this environment. Please use Write/Edit tools to edit files." |
| **Behavioral Instructions** | User's direct instructions regarding assistant behavior (reply language, operational constraints, etc.) | "All thought processes and replies must be in Chinese" |

**Judgment Standard**: Before recording, ask yourself — "When another Agent joins the project later, will this information help them quickly get started with operations, building, troubleshooting, and collaboration?" If the answer is "yes," record it. If the answer is "can be known by reading the code" or "already written in the code," do not record it.

---

## Implementation Method

- Monitor the conversation for user's instructional commands.
- During task execution, proactively identify and record project-specific knowledge related to **operations, building, troubleshooting, workflows, and environment configuration**.
- **Before recording, MUST check against the exclusion list item by item.** If any item matches, abandon the record.
- Before adding a new entry, scan `.monkeycode/MEMORY.md` to check for similar or identical instructions.
- If a duplicate is found, skip the new entry or merge it with the existing one.
- When merging, update the context or date of the existing entry.
- If no duplicate exists, append the instruction to `.monkeycode/MEMORY.md` in the repository root.
- Entry format should include date, user instruction, and context.
- When recording useful behavioral instructions, pay attention to protecting user privacy.
- **File Size Management**: When updating MEMORY.md, if the file content already exceeds 150 lines, you need to merge rules of the same module/category into a single rule to avoid MEMORY.md becoming too large. When merging, retain key information and remove redundant descriptions.

## Purpose

To establish persistent memory of user preferences and instructions for guiding future interactions and customization.

## Enforcement Items

- If the [memory file](.monkeycode/MEMORY.md) does not exist, **when attempting to record content for the first time**, create the file using the **file template** and include the content to be recorded this time.
- If the [memory file](.monkeycode/MEMORY.md) exists, you **MUST** read the [memory file](.monkeycode/MEMORY.md) as project-level instructions **before the first reply**!
- **Every single** user input should be validated against the following **trigger conditions**. Once a match is found, **you MUST immediately** check against the exclusion list first. After confirming it is not within the exclusion scope, then update the [memory file](.monkeycode/MEMORY.md).

## Trigger Conditions

### 1. User Provides Explicit Instructions Regarding Behavioral Patterns

> Examples:
> - "Please use Chinese when replying to me"
> - "Always respond in bullet points"
> - "Write code comments uniformly in English"
> - "Do not use emojis in replies"

### 2. User Instructs or Corrects Assistant Behavior

> Examples:
> - **Do something when a condition is met**: "Automatically run lint after every code change", "When I ask you to refactor, always write unit tests first"
> - **Do NOT do something when a condition is met**: "Do not automatically delete commented-out code", "Don't modify files outside the src/ directory unless I explicitly say so"
> - **Use (or do not use) a certain tool in a specific project/module**: "Use pnpm for this project, not npm", "Use `pytest` instead of `unittest` in this repo", "In the backend module, do not use print, use logger"

### 3. User Expresses Preferred Implementation Methods

> Examples:
> - "I prefer functional style, use fewer classes"
> - "Prefer composition over inheritance in this project"
> - "Use CTEs instead of subqueries when writing SQL"
> - "For CSS, prioritize Tailwind utility classes, do not write custom styles"

**Constraint on Code Style Recording**: Avoid recording general code style preferences (like indentation, naming conventions, etc.) unless the user explicitly and strongly requests it (e.g., "Always write React components in functional style from now on, must follow this principle"). Code styles should be defined in project configuration files (like `.eslintrc`, `.prettierrc`). MEMORY.md focuses on behavioral instructions and project knowledge that cannot be obtained from code.

### 4. User Explains Expected Task Execution Methods

> Examples:
> - "Before fixing a bug, help me write a test case that can reproduce the issue"
> - "Always run `make check` before committing code"
> - "When refactoring, modify only one file at a time. Continue only after I confirm the changes"
> - "When adding a new API endpoint, always update the OpenAPI spec first"

### 5. Agent Proactively Discovers Project Knowledge During Task Execution

During task execution, if the Agent discovers project knowledge valuable for future **operations, building, troubleshooting, and collaboration**, it should proactively record it into MEMORY.md. This type of knowledge does not require explicit user instruction; the Agent should judge and record it independently.

#### Trigger Timing

- **During Build Execution**: Learns about the project's build commands, build toolchain, build configuration, etc.
- **During Test Execution**: Learns about the testing framework, test commands, testing conventions, etc.
- **During Code Generation**: Discovers the project uses code generation tools (e.g., protobuf, OpenAPI codegen, ORM migration, etc.), records the timing and methods of generation.
- **During Problem Debugging**: Discovers special dependencies, environment requirements, known pitfalls of the project, etc.
- **During Deployment & Operations**: Learns about server information, deployment processes, health check methods, etc.

#### Recording Format

Use the same entry format as user instructions, but mark the Context field as discovered by the Agent:

```
[Project Knowledge Summary]
- Date: [YYYY-MM-DD]
- Context: Discovered by Agent while performing [specific task description]
- Category: [Operations & Deployment|Build Methods|Testing Methods|Troubleshooting & Debugging|Workflow & Collaboration|Environment Configuration]
- Instructions:
  - [Specific knowledge points, described line by line]
```

#### Pre-Recording Checklist

Before recording project knowledge discovered by the Agent, you must confirm:

1. Does it belong to the scope of operations & deployment / build & compilation / troubleshooting & debugging / workflow & collaboration / environment configuration? (If not, do not record)
2. Has it already been written into other project documentation? (If yes, do not duplicate)
3. Can it be directly obtained by reading the code? (If yes, do not record)
4. Is it specific implementation details of a particular development task? (If yes, do not record)
5. Do similar entries already exist in MEMORY.md? (If yes, merge or skip)

## MEMORY.md File Template

```markdown
# User Instruction Memory

This file records user instructions, preferences, and teachings for reference in future interactions.

## Format

### User Instruction Entry
User instruction entries should follow this format:

[User Instruction Summary]
- Date: [YYYY-MM-DD]
- Context: [Mentioned scenario or time]
- Instructions:
  - [Content of user teaching or instruction, described line by line]

### Project Knowledge Entry
Entries discovered by the Agent during task execution should follow this format:

[Project Knowledge Summary]
- Date: [YYYY-MM-DD]
- Context: Discovered by Agent while performing [specific task description]
- Category: [Operations & Deployment|Build Methods|Testing Methods|Troubleshooting & Debugging|Workflow & Collaboration|Environment Configuration]
- Instructions:
  - [Specific knowledge points, described line by line]

## Deduplication Strategy
- Before adding a new entry, check for similar or identical instructions.
- If a duplicate is found, skip the new entry or merge it with the existing one.
- When merging, update the context or date information.
- This helps avoid redundant entries and keeps the memory file tidy.

## Entries

[Memory entries recorded according to the above format]
```