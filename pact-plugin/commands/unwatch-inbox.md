---
description: Tear down the lead's inbox-watch Monitor — stop Monitor task, unlink STATE_FILE atomically. Hook-invoked on last active teammate task transition; user-invoked manually to silence Monitor noise mid-session.
---
# Unwatch Inbox

Tear down the lead-side wake mechanism armed by [`/PACT:watch-inbox`](watch-inbox.md): stop the Monitor task and unlink the registry sidecar.

## Overview

Best-effort cleanup. Tolerates a Monitor that died silently mid-session — the wake mechanism is opportunistic by design (no in-session watchdog; see [`/PACT:watch-inbox`](watch-inbox.md#failure-modes)), so `TaskStop` on a stale `monitor_task_id` is the expected path under silent-death conditions.

## When to Invoke

| Trigger | Site |
|---|---|
| Last active teammate task completed (PostToolUse hook detects 1→0 transition) | `wake_lifecycle_emitter.py` `additionalContext` directive |
| Session-end safety net (count already 0; redundant-but-correct hook-silent-fail catch) | `/wrap-up` command body Skill invocation |
| User-typed manual invocation (silence Monitor noise mid-session, e.g., during long-running solo work) | `/PACT:unwatch-inbox` slash invocation |

## Operation

Single procedure — the command IS the operation. Order is load-bearing: stop the live Monitor before unlinking the registry sidecar.

1. Read STATE_FILE at `~/.claude/teams/{team_name}/inbox-wake-state.json`; if absent or invalid (malformed JSON / `v ≠ 1`), skip step 2 — nothing to stop.
2. `TaskStop(STATE_FILE.monitor_task_id)` — **ignoring not-found errors** (the Monitor may have died silently mid-session).
3. Unlink STATE_FILE — `Path.unlink(missing_ok=True)`.

Ordering rationale: the inverse ordering would leave a brief window where a STATE_FILE-less Monitor still runs but the next [`/PACT:watch-inbox`](watch-inbox.md) sees no STATE_FILE and re-arms — creating an orphan.

## Teardown Block

Best-effort sequence. Tolerates a Monitor that died silently mid-session.

```
1. Read STATE_FILE; if absent or invalid (malformed JSON / v ≠ 1), skip step 2.
2. TaskStop(STATE_FILE.monitor_task_id) — ignoring not-found errors.
3. Path.unlink(STATE_FILE, missing_ok=True).
```

`TaskStop` will return a `tool_use_error` if the Monitor died silently. Tolerate not-found and continue to step 3. Do not abort teardown on `TaskStop` failure; an undeleted STATE_FILE is worse than a failed `TaskStop` because it leaves a phantom registry entry that confuses the next [`/PACT:watch-inbox`](watch-inbox.md) invocation.

**Audit**: F6 tolerance phrasing (**"ignoring not-found errors"**) is the load-bearing fragment. An editing LLM "tightening up error handling" by removing the phrase silently restores crash-on-stale-ID. The principle anchor — teardown is best-effort because a torn-down session may have already lost its Monitor — tells the editing LLM why the phrase exists. The wake mechanism is opportunistic; teardown must be tolerant.

## Failure Modes

### Monitor died silently mid-session

`TaskStop(STATE_FILE.monitor_task_id)` returns `tool_use_error: No task found ...`. This is the EXPECTED path under silent-death conditions, not a defect. Tolerate the error and continue to the STATE_FILE unlink; the teardown's purpose is to clean the registry sidecar regardless of the Monitor's actual state.

### STATE_FILE absent

If STATE_FILE does not exist, the wake was never armed (or was already torn down). Skip steps 2-3; this is a no-op success.

## Verification

Confirm teardown:

1. STATE_FILE absent at `~/.claude/teams/{team_name}/inbox-wake-state.json`.
2. `STATE_FILE.monitor_task_id` (read before teardown) no longer resolves to a live Monitor task — either successfully stopped or already dead.

## References

- [`/PACT:watch-inbox`](watch-inbox.md) — paired arm command.
- [Communication Charter Part I §Wake Mechanism](../protocols/pact-communication-charter.md#wake-mechanism) — protocol contract surface.
