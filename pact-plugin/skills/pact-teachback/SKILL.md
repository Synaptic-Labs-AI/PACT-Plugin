---
name: pact-teachback
description: |
  Teachback protocol for PACT specialist agents. Auto-loaded via frontmatter
  so the teachback format is always available as the agent's first action.
---

# Teachback Protocol

**Teachback is a gate, not a notification.** Send your teachback BEFORE any
implementation work (`Edit`, `Write`, `Bash`). Reading files to understand the
task (`Read`, `Glob`, `Grep`) is permitted before teachback.

**Format**:
```
SendMessage(type="message", recipient="lead",
  content="[{sender}->lead] Teachback:\n- Building: {what}\n- Key constraints: {constraints}\n- Approach: {approach, briefly}\nProceeding unless corrected.",
  summary="Teachback: {1-line}")
```

**Rules**:
- Send as your **first message** after reading your task and any upstream handoffs
- Keep concise: 3-6 bullet points
- After sending, record: `TaskUpdate(taskId, metadata={"teachback_sent": true})`
- If the lead sends a correction, adjust your approach immediately
- Non-blocking: proceed with work after sending — do not wait for confirmation

**When**: Every task dispatch. Only exception: consultant questions (peer asks you something).
