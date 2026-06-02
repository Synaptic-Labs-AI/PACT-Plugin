---
name: pact-team-registration
description: Record your teammate identity (name@team) at session start so later hooks can recover your friendly name. Invoke as your first action when your spawn prompt directs it.
---

# Register Your Team Identity

As your **first action**, record your `name@team`. Run exactly:

```bash
python3 ~/.claude/protocols/pact-plugin/../hooks/shared/session_registry.py register --name '<your-name>@<your-team>'
```

Substitute your name and team from your spawn prompt. The command self-acquires `session_id` and is fail-safe (path error → no-op). Do NOT add a fallback or rewrite the path. This is identity bookkeeping, not implementation work — exempt from the teachback no-`Bash`-before rule.
