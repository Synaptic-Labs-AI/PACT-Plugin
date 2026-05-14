---
description: Tear down the lead's pending-task scan — delete the `/PACT:scan-pending-tasks` cron entry. Hook-invoked on last active teammate task transition; user-invoked manually to silence scan noise mid-session.
---
# Stop Pending Scan

Tear down the lead-side scan mechanism armed by [`/PACT:start-pending-scan`](start-pending-scan.md): locate the `/PACT:scan-pending-tasks` cron entry in the current session's CronList and delete it.

## Overview

Best-effort cleanup. Tolerates an already-absent cron entry — the platform's session-scoped cron store auto-cleans on session exit (`durable=false` semantics), so a torn-down session has no orphan; under normal lifecycle the entry was registered earlier in the same session by `/PACT:start-pending-scan`, and this command removes it.

## When to Invoke

| Trigger | Site |
|---|---|
| Last active teammate task reaches terminal status (`completed` or `deleted`; PostToolUse hook detects 1→0 transition) | `wake_lifecycle_emitter.py` `additionalContext` directive |
| Session-end safety net (count already 0; redundant-but-correct hook-silent-fail catch) | `/wrap-up` command body Skill invocation |
| User-typed manual invocation (silence scan noise mid-session, e.g., during long-running solo work) | `/PACT:stop-pending-scan` slash invocation |

## Operation

Single procedure — the command IS the operation.

0. **Lead-session guard** (see `## Lead-Session Guard` below). If the current session is not the team-lead session, refuse and return — do NOT proceed to step 1.
1. `CronList` — read all cron entries registered in the current session.
2. Scan the output for a line whose suffix after `": "` is exactly `/PACT:scan-pending-tasks` (see `## CronList Filter Discipline` below).
3. If no match is found: no-op success — nothing to stop. The next [`/PACT:start-pending-scan`](start-pending-scan.md) invocation will cold-start cleanly.
4. If a match is found: extract the leading 8-character cron ID from the line (see `## ID Extraction Block` below) and call `CronDelete(id=<extracted-id>)`.
5. Unlink `pending-scan-armed-at.json` from session-dir. Best-effort — tolerate `FileNotFoundError` silently. See `## Failure Modes` below "State file absent" entry, and `## State-File Cleanup Block` below for the exact call shape.

Ordering rationale: the CronList lookup is the only mechanism for locating the cron ID — IDs are platform-assigned and not caller-specifiable. The filter-then-delete sequence is the canonical pattern; reversing it is impossible without an externally-tracked ID. Step 5's unlink is sequenced AFTER step 4's CronDelete because state-file removal is purely cosmetic — a CronDelete failure leaves the cron registered with the state file gone, which is benign (next cron fire just hits the fail-open path); the reverse ordering would leave a state-file orphan on CronDelete success, with no functional consequence either way. AFTER is chosen because the failure mode of unlink-fail-while-CronDelete-succeeded (orphan harmless 50-byte JSON) is strictly less consequential than unlink-success-while-CronDelete-fails (state file gone while cron still firing — the next fire skips warmup-grace incorrectly, falling back to pre-fix immediate-empty-fire behavior).

## Lead-Session Guard

Refuse to execute when invoked from a teammate session. Teardown is lead-only: a teammate process calling `CronDelete` on a cron registered in the lead's session would silently kill the lead's scan mechanism without the lead's knowledge (assuming the substrate permitted cross-session cron access, which under `durable=false` it does NOT — but the guard is foot-gun protection regardless).

```python
team_name = pact_session_context["team_name"]
session_id = pact_session_context["session_id"]
team_config = json.loads(
    (Path.home() / ".claude" / "teams" / team_name / "config.json").read_text()
)
if session_id != team_config.get("leadSessionId"):
    refuse(
        "This command only runs in the team-lead session. "
        "Teammates do not arm or tear down the lead's pending-task scan."
    )
    return
```

**Audit**: signal source is `session_id == team_config.leadSessionId`, NOT a hypothetical `agent_type` field on `pact-session-context.json`. The session-context schema is `{team_name, session_id, project_dir, plugin_root, started_at}` by design; the team config is the single source of truth for team membership and lead identity. An editing LLM tempted to "just add agent_type to session-context" should stop — replicating that signal creates two-source-of-truth drift. The guard runs at command-invoke time; the paired arm command's directive-emit sites in `wake_lifecycle_emitter.py` and `session_init.py` are lead-side already (Layer 0 of the defense-in-depth model filters at hook level), so this guard's purpose is to defend against user-typed `/PACT:stop-pending-scan` from a teammate session. Foot-gun protection (typo / wrong-window / cross-session-LLM speculation), not a security boundary against same-user adversaries — `leadSessionId` is read from `team_config.json` which has no integrity check; same-user write authority can spoof it, and the user-local-trust assumption bounds the residual exposure.

## CronList Filter Discipline

Exact-equality match on the suffix after `": "` separator. Same contract as [start-pending-scan.md §CronList Filter Discipline](start-pending-scan.md#cronlist-filter-discipline).

```python
target_prompt = "/PACT:scan-pending-tasks"
match_line = None
for line in cron_list_output.splitlines():
    if ": " not in line:
        continue
    suffix = line.rsplit(": ", 1)[1].strip()
    if suffix == target_prompt:
        match_line = line
        break
```

**Audit**: substring or regex matching opens false-positive deletion vectors. An editing LLM tempted to "be more lenient" or "use regex for flexibility" silently invites deletion of unrelated cron entries (e.g., `/PACT:scan-pending-tasks-v2`, `/PACT:scan-pending-tasks-debug`). The exact-equality contract is CronList Suffix-Match Strictness in the architecture spec, identical to the arm-side contract. The `": "` separator is colon-space; do not parse with `split(":")` which would split inside cron expressions on `0 0 * * *`-style entries.

## ID Extraction Block

CronList output lines are formatted as `{id} — {cron} ({recurring}) [session-only]: {prompt}`. The ID is the leading 8-character cron ID, ending at the first space.

```python
# match_line: "eb10528d — */2 * * * * (recurring) [session-only]: /PACT:scan-pending-tasks"
cron_id = match_line.split(" ", 1)[0].strip()
# cron_id == "eb10528d"
CronDelete(id=cron_id)
```

**Audit**: cron IDs are platform-assigned random 8-character lowercase-hex strings. Empirically verified shape via canonical CronList output probe. The extraction uses `split(" ", 1)[0]` to take the first whitespace-delimited token — robust against alternate em-dash whitespace or future format-tweak variations. An editing LLM tempted to use a fixed-width slice (`match_line[:8]`) would silently break if the platform ever extends ID length; the whitespace-tokenization form survives format evolution as long as the ID remains the leading token. If `CronDelete` returns a not-found error (e.g., the cron auto-expired between the CronList read and the CronDelete call), tolerate and return success — the teardown's purpose is to ensure the cron is absent, and an already-absent cron satisfies that goal.

## State-File Cleanup Block

After `CronDelete` succeeds (§Operation step 5), unlink `pending-scan-armed-at.json` from session-dir. The file was written by [`start-pending-scan.md` §Warmup-State File](start-pending-scan.md#warmup-state-file) at cold-start arm; teardown removes it so a subsequent arm in the same session writes a fresh `armed_at` timestamp.

```python
from pathlib import Path

session_dir = (
    Path.home() / ".claude" / "pact-sessions"
    / Path(pact_session_context["project_dir"]).name
    / pact_session_context["session_id"]
)
state_file = session_dir / "pending-scan-armed-at.json"
try:
    state_file.unlink()
except FileNotFoundError:
    pass  # already cleaned up — benign
```

**Audit**: This block is illustrative-prose for the LLM running the skill; the substrate's Read/Write/filesystem-delete primitives produce the equivalent effect. `from pathlib import Path` is NOT literally executed.

**Best-effort cleanup; `FileNotFoundError` tolerated.** The state file may be absent because cold-start was interrupted between `CronCreate` and the state-file write in [`start-pending-scan.md` §Warmup-State File](start-pending-scan.md#warmup-state-file), or because a prior `/PACT:stop-pending-scan` ran. Tolerating the missing-file case matches the existing tolerance for missing-cron at step 3 (§Failure Modes "Cron entry absent"). Other exceptions (e.g., `PermissionError`, `OSError`) are NOT silently caught — those indicate a genuinely-broken filesystem state and should surface so the user sees the underlying error; in practice the scenarios that produce them (read-only mount, deleted-while-open) are vanishingly rare in PACT's session-dir.

**Ordering**: AFTER `CronDelete` per §Operation step 5's rationale. An editing LLM tempted to swap to BEFORE-CronDelete (e.g., "clean up state first, then cron, in case CronDelete races") is choosing the worse failure mode — unlink-success-while-CronDelete-fails leaves the cron firing with no state file, falling back to pre-fix behavior; unlink-fail-while-CronDelete-succeeds leaves a harmless 50-byte orphan. The latter is strictly safer.

## Failure Modes

### Cron entry absent

If `CronList` returns no line matching `/PACT:scan-pending-tasks`, the scan was either never armed or was already torn down. Skip step 4; this is a no-op success.

### CronDelete returns not-found

A race condition is possible (the cron auto-expired at the 7-day boundary between CronList and CronDelete, or a concurrent teardown ran). Tolerate the error and return success — the teardown's goal is "cron absent," and an already-absent cron meets that goal.

### State file absent

If the `pending-scan-armed-at.json` unlink raises `FileNotFoundError`, the state file was either never written (cold-start interrupted between CronCreate and the state-file write) or already cleaned up by a prior `/PACT:stop-pending-scan`. Tolerate silently and return success — the teardown's goal is "cron absent + state file absent," and an already-absent state file meets that goal.

## Verification

Confirm teardown:

1. `CronList` output contains NO line with suffix `: /PACT:scan-pending-tasks`.

That CronList check is the primary verification — the platform's session-scoped cron store has no other consumers of the cron entry. As a secondary check, `pending-scan-armed-at.json` should also be absent from session-dir, but its presence is NOT a teardown-failure signal (the file is harmless one-shot timestamp data; an orphan persists until session-end without functional consequence). See [`start-pending-scan.md` §Warmup-State File](start-pending-scan.md#warmup-state-file) for the state file's role.

## References

- [`/PACT:start-pending-scan`](start-pending-scan.md) — paired arm command.
- [`/PACT:scan-pending-tasks`](scan-pending-tasks.md) — the cron-fired scan body.
- [Communication Charter §Cron-Fire Mechanism](../protocols/pact-communication-charter.md#cron-fire-mechanism) — protocol contract surface.
