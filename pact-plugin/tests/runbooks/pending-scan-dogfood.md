# Pending-Scan Dogfood Runbook

End-to-end validation of the cron-based pending-scan mechanism in a fresh session post-merge. Verifies arm/teardown lifecycle, cron-fire cadence, scan-output discipline (5 anti-hallucination guardrails), `lead_session_id` gate, and `[CRON-FIRE]` marker presence.

## Scope

Cron-based pending-scan mechanism: `/PACT:start-pending-scan`, `/PACT:scan-pending-tasks`, `/PACT:stop-pending-scan`. Hook integration: `wake_lifecycle_emitter.py` (PostToolUse) + `session_init.py` (SessionStart resume).

The runbook deliberately covers BOTH happy-path lifecycle and edge cases that the 5 anti-hallucination guardrails (G1-G5) defend against. Each step lists its acceptance criterion; a single failure fails the runbook.

## Pre-Requisites

- Fresh session (not a resume); plugin v4.2.0+ installed.
- `~/.claude/teams/` writeable; no stale cron entries from prior sessions (`CronList` should not show `/PACT:scan-pending-tasks` at session start).
- A test repo / worktree available for spawning a teammate.

## 1. Fresh-Session Arm via First-Active-Task Transition

**Action**: Start a fresh PACT session. Confirm `CronList` is empty (no pending-scan cron). Spawn a teammate via the canonical Teachback-Gated Dispatch pattern (`/PACT:orchestrate` or a manual `TaskCreate(Task A) + TaskCreate(Task B) + Agent(...)`).

**Expected**:
- After the first `TaskCreate` (Task A teachback gate), the PostToolUse hook `wake_lifecycle_emitter.py` fires and emits an `additionalContext` directive instructing the lead to invoke `Skill("PACT:start-pending-scan")`.
- Directive text exactly: `First active teammate task created. Invoke Skill("PACT:start-pending-scan") before any further teammate dispatch. Idempotent — no-op if a /PACT:scan-pending-tasks cron is already registered.`
- Lead invokes the skill; skill body's Lead-Session Guard passes (current session is lead), CronList lookup returns no match, `CronCreate(cron="*/2 * * * *", prompt="/PACT:scan-pending-tasks", recurring=True, durable=False)` succeeds.
- Post-Arm `CronList` shows exactly one line with suffix `: /PACT:scan-pending-tasks` and markers `(recurring) [session-only]`.

**Acceptance**: `CronList` contains the cron. `CronList | grep -o '/PACT:scan-pending-tasks' | wc -l` returns `1` (exactly one entry).

## 2. Idempotency: Re-Arm With Existing Cron Entry

**Action**: Create a second teammate task (`TaskCreate` for another Task A). PostToolUse hook fires again and re-emits the Arm directive (per the unconditional-emit discipline — the hook does NOT short-circuit on existing cron).

**Expected**:
- Lead re-invokes `Skill("PACT:start-pending-scan")`.
- Skill body's CronList lookup finds the existing match; the skill no-ops without calling `CronCreate` a second time.
- Post-action `CronList` still contains exactly ONE matching line (NOT two).

**Acceptance**: `CronList | grep -o '/PACT:scan-pending-tasks' | wc -l` still returns `1`. No duplicate cron created.

## 3. Cron-Fire Wake of Scan-Pending-Tasks (2-minute cadence)

**Action**: After Arm, idle the lead session (no manual tool calls). Wait at the next between-tool-call boundary for the cron to fire.

**Expected**:
- Within ~2 minutes (allow up to 3 minutes for jitter — cron may fire up to 10% of its period late, max 15 minutes), the cron fires `/PACT:scan-pending-tasks` as the lead's next turn.
- The fired prompt has the `[CRON-FIRE]` discipline marker at the top of the skill body (verifiable by inspection of the loaded skill content).
- Scan body runs steps 1-7 from `scan-pending-tasks.md §Operation`:
  - Step 1: read `pact_session_context["session_id"]` for the same-session-identity gate.
  - Step 2: `TaskList` returns the active tasks.
  - Steps 3-7: filter by gate + read metadata + accept-or-skip-or-emit-nothing.

**Acceptance**: A scan-fire turn occurs within 3 minutes of Arm. The scan body executes against the actual filesystem (verifiable by post-scan inspection of any task-state changes or by the absence of changes if no acceptance was due).

## 4. Scan-Output Discipline — All 5 Guardrails Empirically Verified

This is the load-bearing verification — the 5 anti-hallucination guardrails are why this mechanism exists.

### 4a. G1: Read filesystem only

**Action**: Before the next cron-fire, manually inject misleading prose into a teammate `SendMessage` that mentions a fake teachback (e.g., "Teachback submitted!") WITHOUT actually writing `metadata.teachback_submit`. Wait for the next cron-fire.

**Expected**: The scan does NOT accept the task. It reads the raw task JSON, finds `metadata.teachback_submit` is absent, and skips the task (per G4 race-window-skip) or emits nothing (per G5 if no other candidates).

**Acceptance**: Task remains `in_progress`; no `TaskUpdate(status="completed")` is invoked; no acceptance SendMessage sent to the teammate.

### 4b. G2: No narration

**Action**: Observe the lead session's transcript across multiple cron-fire cycles (at least 3 fires during active teammate work).

**Expected**: No prose lines like "Scanning…", "Skipping task #N…", "Race window detected…", or any per-fire status narration appear in the transcript.

**Acceptance**: User-visible output across the 3+ fires is either (a) an actual acceptance two-call pair, or (b) silence. Nothing else.

### 4c. G3: Raw-read metadata

**Action**: Inspect the scan-body execution log (if available) or trace the file reads. Confirm the scan reads `~/.claude/tasks/{team_name}/{id}.json` directly, NOT via `TaskGet`.

**Expected**: The metadata is sourced from raw file reads, not from `TaskGet`'s metadata-stripped response.

**Acceptance**: The scan's read of `metadata.teachback_submit` / `metadata.handoff` returns populated payloads when those fields ARE on disk (regardless of what `TaskGet` might surface separately).

### 4d. G4: Race-window skip

**Action**: Have a teammate write `metadata.teachback_submit` and send the wake-SendMessage in rapid succession (back-to-back tool calls). The teammate's write may still be flushing to disk when the next cron-fire occurs.

**Expected**: If the cron fires DURING the write-flush window, the raw read returns null/empty, and the scan SKIPS the task (no rejection, no SendMessage, no `TaskUpdate`). The next cron-fire (2 minutes later) re-evaluates after the write has landed.

**Acceptance**: At least one cron-fire turn observes the empty metadata and skips silently; the subsequent cron-fire (after the write lands) accepts the artifact correctly.

### 4e. G5: Emit nothing if empty

**Action**: With no pending teachbacks/handoffs (all teammates mid-work, no `awaiting_lead_completion` tasks), observe at least 3 consecutive cron-fires.

**Expected**: Each fire produces NO output. The user sees no transcript entry for the cron-fire turn.

**Acceptance**: 3+ consecutive empty cron-fires produce zero user-visible output.

## 5. Acceptance via Scan (Happy Path)

**Action**: Have a teammate write a complete `metadata.teachback_submit` (4 fields per pact-teachback skill). Send the wake-SendMessage. Wait up to 3 minutes.

**Expected**:
- Within the next cron-fire window, the scan reads the metadata, validates per `pact-completion-authority §12 Teachback Review`, and invokes the canonical acceptance two-call pair:
  1. `SendMessage(to=teammate, message="<wake-signal confirming acceptance>")` FIRST.
  2. `TaskUpdate(taskId, status="completed")` SECOND.
- Task A transitions to `completed`; teammate's Task B becomes unblocked.

**Acceptance**: Task A status is `completed`. The acceptance SendMessage appears in the teammate's inbox before the `TaskUpdate` call (verifiable from the journal event ordering).

## 6. Lead-Session-ID Gate Verification (INV-9, Layer 3)

**Action**: Manually create a task in `~/.claude/teams/{team_name}/` whose `metadata.lead_session_id` is a different (fake) session_id, while in `awaiting_lead_completion` status with populated `metadata.teachback_submit`. Wait for next cron-fire.

**Expected**: The scan filters this task out at step 1 of `scan-pending-tasks §Operation` (same-session-identity gate). The task is NOT accepted, no SendMessage sent, no `TaskUpdate(status="completed")` called.

**Acceptance**: The mismatched-session task remains in `awaiting_lead_completion` status indefinitely. The scan's behavior for tasks owned by THIS session is unaffected.

## 7. Hook-Level Session Guard (INV-12, Layer 0)

**Action**: In a second concurrent PACT session (with a teammate-typed agent role, NOT the lead) that shares the same `team_name`, trigger a `TaskCreate` or `TaskUpdate(status="in_progress")` event. Observe whether `wake_lifecycle_emitter.py` emits the Arm directive.

**Expected**: No Arm directive emitted in the teammate session — the hook's `_is_lead_session` check fails (session_id != team_config.leadSessionId), and the hook exits with `suppressOutput`.

**Acceptance**: The teammate session sees no `start-pending-scan` directive in `additionalContext`. No spurious cron is registered in the teammate's session.

## 8. Teardown via Last-Active-Task Transition

**Action**: Complete all teammate tasks (or have all reach `completed` / `deleted` terminal status). Verify the same-teammate-continuation guard does NOT defer Teardown (no remaining same-teammate active continuation in any task's `blocks` chain).

**Expected**:
- PostToolUse hook detects the 1→0 transition and emits a stop-pending-scan directive.
- Directive text exactly: `Last active teammate task completed. Invoke Skill("PACT:stop-pending-scan") to delete the /PACT:scan-pending-tasks cron. Best-effort — tolerates a cron that was already auto-deleted (7-day expiry) or never registered.`
- Lead invokes the skill; skill body's CronList lookup finds the match; `CronDelete(id=<extracted-8-char-id>)` succeeds.
- Post-Teardown `CronList` shows no match for `/PACT:scan-pending-tasks`.

**Acceptance**: `CronList | grep -o '/PACT:scan-pending-tasks' | wc -l` returns `0`.

## 9. Bug A Preservation — Deferred Teardown on Same-Teammate Continuation

**Action**: Spawn a teammate with Task A blocking Task B (canonical Two-Task Dispatch shape). Have the teammate complete Task A (the teachback gate) — Task A reaches `completed` while Task B is still `pending` (same owner). Watch for PostToolUse hook behavior.

**Expected**: The hook detects the 1→0 transient (Task A complete, but Task B still pending with same owner) and DEFERS the Teardown emit per `has_same_teammate_continuation` predicate. The cron entry remains armed.

**Acceptance**: After Task A completes, `CronList` still contains the cron entry. No spurious Teardown was emitted. When Task B subsequently completes (and no further same-teammate continuation exists), the regular 1→0 Teardown fires normally.

## 10. Bug B Preservation — Re-Arm on Pending→In-Progress Transition

**Action**: After Teardown (cron entry absent), have a teammate claim a `pending` task via `TaskUpdate(status="in_progress")`. Observe hook behavior.

**Expected**: The hook detects the pending→in_progress transition and emits the Arm directive (since `active_count >= 1` post-transition and no cron is registered). The lead invokes `start-pending-scan` and the cron is re-registered.

**Acceptance**: Post-transition `CronList` contains exactly one `/PACT:scan-pending-tasks` entry.

## 11. `[CRON-FIRE]` Discipline Marker Presence Verification

**Action**: Open `pact-plugin/commands/scan-pending-tasks.md`. Confirm the file body begins with the marker `[CRON-FIRE] This skill is invoked by the platform cron scheduler, NOT by user input.` — appearing BEFORE the `## Overview` section header.

**Expected**: Marker is present at the top of the skill body, byte-identical to the architecture spec.

**Acceptance**: `head -5 pact-plugin/commands/scan-pending-tasks.md | grep -c '\[CRON-FIRE\]'` returns `1` (marker present in the first 5 lines).

## 12. Cross-Skill Byte-Identity (INV-1)

**Action**: Verify `/PACT:scan-pending-tasks` string is byte-identical across all 3 skill files.

**Command**:
```
grep -o '/PACT:scan-pending-tasks' \
    pact-plugin/commands/start-pending-scan.md \
    pact-plugin/commands/scan-pending-tasks.md \
    pact-plugin/commands/stop-pending-scan.md \
    | wc -l
```

**Expected**: Result is `>= 3` (each file contains the literal at least once).

**Acceptance**: At least one occurrence per file (per-file `grep -o ... | wc -l` returns `>= 1` for each of the 3 files).

## Pass Criteria

All 12 steps pass independently. A single step failure fails the runbook and must be triaged before merging the next PACT release.

## Failure Triage

- **Step 1 / 2 fail**: hook diagnostic issue or skill body Lead-Session Guard logic regression. Inspect `wake_lifecycle_emitter.py` and `start-pending-scan.md`.
- **Step 3 fails**: platform cron primitive issue or 2-minute cadence misconfiguration. Check `CronCreate` call shape against canonical schema.
- **Step 4 (any sub-step) fails**: load-bearing guardrail violation. Treat as a P0 regression; the 5 guardrails are why this mechanism exists.
- **Step 5 fails**: completion-authority procedure regression. Inspect canonical acceptance two-call pair in `scan-pending-tasks §Lead-Only Completion Contract`.
- **Step 6 fails**: cross-session contamination defense regression. Inspect `lead_session_id` field-write at canonical task-creation sites and the gate logic in `scan-pending-tasks.md` step 1.
- **Step 7 fails**: hook-level session guard regression. Inspect `_is_lead_session` in `wake_lifecycle_emitter.py` and `_is_lead_session_at_init` in `session_init.py`.
- **Step 8 / 9 / 10 fail**: lifecycle predicate regression. Inspect `count_active_tasks` and `has_same_teammate_continuation` in `shared/wake_lifecycle.py`.
- **Step 11 / 12 fail**: structural-test territory; the corresponding test suite should have caught this in CI. Re-run `test_scan_pending_tasks_command_structure.py`.
