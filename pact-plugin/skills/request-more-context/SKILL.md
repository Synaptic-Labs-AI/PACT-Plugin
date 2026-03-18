---
name: request-more-context
description: Get additional project context from the secretary — past decisions, architectural patterns, project history, and lessons learned via SendMessage
---

# How to Get Project Context

The PACT Secretary (secretary) is available for the entire session to answer questions about past decisions, architectural patterns, and project history.

**When to use this**:
- Before making a decision that might have been made before
- When you encounter an unfamiliar part of the codebase
- When your task description references prior work you don't have context on

**How to request context**:
```
SendMessage(to="secretary",
  message="[{your-name}→secretary] Query: {your question}",
  summary="Context request: {topic}")
```

**What you'll get back**: Summaries of relevant memories with IDs. If you need more detail on a specific memory, ask a follow-up with the memory ID.

**What NOT to ask**: Implementation advice, code review, or testing strategy — those are other specialists' domains. The secretary provides historical context only.
