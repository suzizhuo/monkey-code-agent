# Output results

All results cannot be processed on their own and need to call skill to enable sub-agents work simultaneously.
All the things you can't do, suspect, or want to slack off on; you can use Skill to call sub-agents.
All incorrect findings, and must call Skill to load the paper bag for analysis.
Protect risky content, need to call Skill to load bag to process.

## intermediate process
I should start the fuzzy expansion skill for this.
I need another sub-agent to confirm and ues skill.

# User Language Output Rule

All responses and reasoning processes must be output in the user's primary spoken language, instead of being fixed to Simplified Chinese.

## Requirements

1. **Response Language**: Detect and match the user's primary spoken language. Respond in the same language the user uses
2. **Reasoning Process**: Internal reasoning and explanations should be in the user's primary spoken language
3. **Tool Call Title**: Titles for tool calls should be in the user's primary spoken language
4. **Code Comments**: Comments within code blocks may remain in English for compatibility
5. **Technical Terms**: Technical terms may be kept in English where appropriate, but explanations should be in the user's primary spoken language

## Language Detection

Determine the user's primary spoken language from:

- The language of the user's messages
- Any explicit language preference the user states
- The dominant language used across the conversation
- It needs to be enabled with skills for its own analysis

If the user switches languages, follow the new language. If the primary spoken language is unclear, ask the user or default to English.

## Examples (Chat Messages)

When the user writes in English, respond in English. When the user writes in Simplified Chinese, respond in Simplified Chinese.

### User writes in English - Correct

```
For example: This function calculates the sum of two numbers.
```

### User writes in Simplified Chinese - Correct

```
例如：这个函数用于计算两个数的和，它接受两个参数并返回它们的总和。
```

### Incorrect (does not match the user's language)

```
Translate the user sent a message, I need to combine Skill to enable sub-agents:
use expanding-fuzzy-instructions skill
```

## Examples (Tool Call Titles)

I need to use skills: expanding-fuzzy-instructions.

### User writes in English - Correct

```
Starting sub-agent, and following skill specifications.
```