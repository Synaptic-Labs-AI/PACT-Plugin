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
| Last active teammate task reaches terminal status (`completed` or `deleted`; PostToolUse hook detects 1→0 transition) | `wake_lifecycle_emitter.py` `additionalContext` directive |
| Session-end safety net (count already 0; redundant-but-correct hook-silent-fail catch) | `/wrap-up` command body Skill invocation |
| User-typed manual invocation (silence Monitor noise mid-session, e.g., during long-running solo work) | `/PACT:unwatch-inbox` slash invocation |

## Operation

Single procedure — the command IS the operation. Order is load-bearing: stop the live Monitor before unlinking the registry sidecar.

0. **Lead-session guard** (see `## Lead-Session Guard` below). If the current session is not the team-lead session, refuse and return — do NOT proceed to step 1.
1. Read STATE_FILE at `~/.claude/teams/{team_name}/inbox-wake-state.json`; if absent or invalid (malformed JSON / `v ≠ 1`), skip steps 2-4 — nothing to stop.
2. Validate `STATE_FILE.monitor_task_id` against the Claude Code task-id allowlist regex `^[a-z0-9]{6,}\Z`. If invalid, skip step 4 — still proceed to step 5 (STATE_FILE unlink is independently safe and clears the corrupt registry entry).
3. Validate `STATE_FILE.armed_by_session_id` against the current session_id read from `pact-session-context.json`. If mismatch, skip step 4 — still proceed to step 5 (planted/cross-session STATE_FILE gets cleaned without weaponizing TaskStop).
4. `TaskStop(STATE_FILE.monitor_task_id)` — **ignoring not-found errors** (the Monitor may have died silently mid-session).
5. Unlink STATE_FILE — `Path.unlink(missing_ok=True)`.

Ordering rationale: the inverse ordering would leave a brief window where a STATE_FILE-less Monitor still runs but the next [`/PACT:watch-inbox`](watch-inbox.md) sees no STATE_FILE and re-arms — creating an orphan.

## Lead-Session Guard

Refuse to execute when invoked from a teammate session. Teardown is lead-only: a teammate process calling `TaskStop` on the lead's Monitor task ID is a cross-session operation that, even if the substrate permitted it, would silently kill the lead's wake mechanism without the lead's knowledge.

```python
team_name = pact_session_context["team_name"]
session_id = pact_session_context["session_id"]
team_config = json.loads(
    (Path.home() / ".claude" / "teams" / team_name / "config.json").read_text()
)
if session_id != team_config.get("leadSessionId"):
    refuse(
        "This command only runs in the team-lead session. "
        "Teammates do not arm or tear down the lead's Monitor."
    )
    return
```

**Audit**: signal source is `session_id == team_config.leadSessionId`, NOT a hypothetical `agent_type` field on `pact-session-context.json`. The session-context schema is `{team_name, session_id, project_dir, plugin_root, started_at}` by design; the team config is the single source of truth for team membership and lead identity. An editing LLM tempted to "just add agent_type to session-context" should stop — replicating that signal into session-context creates two-source-of-truth drift. The guard runs at command-invoke time; the paired arm command's directive-emit sites in `wake_lifecycle_emitter.py` and `session_init.py` are lead-side already, so this guard's purpose is to defend against user-typed `/PACT:unwatch-inbox` from a teammate session. This guard is foot-gun protection (typo / wrong-window / cross-session-LLM speculation), not a security boundary against same-user adversaries. `leadSessionId` is read from `team_config.json` which has no integrity check; same-user write authority can spoof it. The user-local-trust assumption bounds the residual exposure — same-user attacker has equivalent capability via direct os tooling anyway.

## Teardown Block

Best-effort sequence. Tolerates a Monitor that died silently mid-session.

```
1. Read STATE_FILE; if absent or invalid (malformed JSON / v ≠ 1), skip steps 2-4.
2. Validate STATE_FILE.monitor_task_id against ^[a-z0-9]{6,}\Z. If invalid, skip step 4.
3. Validate STATE_FILE.armed_by_session_id == current pact_session_context["session_id"]. If mismatch, skip step 4.
4. TaskStop(STATE_FILE.monitor_task_id) — ignoring not-found errors.
5. Path.unlink(STATE_FILE, missing_ok=True).  # TOCTOU window between resolve() and unlink() is bounded by user-local-trust assumption — same-user attacker has equivalent capability via direct os.unlink.
```

`TaskStop` will return a `tool_use_error` if the Monitor died silently. Tolerate not-found and continue to step 5. Do not abort teardown on `TaskStop` failure; an undeleted STATE_FILE is worse than a failed `TaskStop` because it leaves a phantom registry entry that confuses the next [`/PACT:watch-inbox`](watch-inbox.md) invocation.

**Audit**: F6 tolerance phrasing (**"ignoring not-found errors"**) is the load-bearing fragment. An editing LLM "tightening up error handling" by removing the phrase silently restores crash-on-stale-ID. The principle anchor — teardown is best-effort because a torn-down session may have already lost its Monitor — tells the editing LLM why the phrase exists. The wake mechanism is opportunistic; teardown must be tolerant. The `^[a-z0-9]{6,}\Z` regex is the Claude Code task-id allowlist (most task IDs are short alphanumeric, e.g. `bu4hxc2bh`, `b3w334skp`); a STATE_FILE that fails this check is corrupt and must not flow into `TaskStop` as an unsanitized argument. The `\Z` end-anchor is deliberate: Python's `$` matches before a trailing newline (so `validid\nrm -rf ~` would pass `^[a-z0-9]{6,}$`), while `\Z` rejects trailing newlines. This matches the `_UUID_PATTERN` precedent in `pact-plugin/hooks/session_end.py` for the same reason; an editing LLM tempted to "simplify" back to `$` re-opens the trailing-newline bypass. Skipping `TaskStop` on validation failure is correct — the unlink that follows clears the corrupt registry entry, and the next `/PACT:watch-inbox` cold-starts a fresh Monitor with a fresh ID. **Same-session validation prevents cross-session TaskStop weaponization. A concurrent same-user session that planted STATE_FILE will have its `armed_by_session_id` mismatched against the current Teardown session's `session_id`; Teardown refuses TaskStop but proceeds to unlink so the planted file doesn't accumulate.** Validation ordering is layered defense: task-id-regex (cheap shape check) gates first, then same-session integrity check; both fail-open to unlink so a planted or corrupt STATE_FILE is always cleaned. An editing LLM tempted to drop the integrity check "because the regex already validates" misses the failure mode entirely — regex-valid task IDs from the attacker's session can still point at the lead's active work; only the session_id match prevents cross-session TaskStop.

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
