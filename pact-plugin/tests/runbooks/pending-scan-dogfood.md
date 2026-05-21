# Pending-Scan Dogfood Runbook

End-to-end validation of the cron-based pending-scan mechanism in a fresh session post-merge. Verifies arm/teardown lifecycle, cron-fire cadence, scan-output discipline (5 anti-hallucination guardrails), and `[CRON-FIRE]` marker presence.

## Scope

Cron-based pending-scan mechanism: `/PACT:start-pending-scan`, `/PACT:scan-pending-tasks`, `/PACT:stop-pending-scan`. Hook integration: `wake_lifecycle_emitter.py` (PostToolUse) + `session_init.py` (SessionStart resume).

The runbook deliberately covers BOTH happy-path lifecycle and edge cases that the 5 anti-hallucination guardrails (Read-Filesystem-Only through Emit-Nothing-If-Empty) defend against. Each step lists its acceptance criterion; a single failure fails the runbook.

## Pre-Requisites

- Fresh session (not a resume); plugin v4.2.0+ installed.
- `~/.claude/teams/` writeable; no stale cron entries from prior sessions (`CronList` should not show `/PACT:scan-pending-tasks` at session start).
- A test repo / worktree available for spawning a teammate.

## 1. Fresh-Session Arm via First-Active-Task Transition

**Action**: Start a fresh PACT session. Confirm `CronList` is empty (no pending-scan cron). Spawn a teammate via the canonical Teachback-Gated Dispatch pattern (`/PACT:orchestrate` or a manual `TaskCreate(Task A) + TaskCreate(Task B) + Agent(...)`).

**Expected**:
- After the first `TaskCreate` (Task A teachback gate), the PostToolUse hook `wake_lifecycle_emitter.py` fires and emits an `additionalContext` directive instructing the lead to invoke `Skill("PACT:start-pending-scan")`.
- Directive text exactly: `Active teammate work detected. You MUST invoke Skill("PACT:start-pending-scan") before your next tool call. This is a non-negotiable lifecycle gate. Idempotent — no-op if a /PACT:scan-pending-tasks cron is already registered.`
- Lead invokes the skill; skill body's Lead-Session Guard passes (current session is lead), CronList lookup returns no match, `CronCreate(cron="*/5 * * * *", prompt="/PACT:scan-pending-tasks", recurring=True, durable=False)` succeeds.
- Post-Arm `CronList` shows exactly one line with suffix `: /PACT:scan-pending-tasks` and markers `(recurring) [session-only]`.

**Acceptance**: `CronList` contains the cron. `CronList | grep -o '/PACT:scan-pending-tasks' | wc -l` returns `1` (exactly one entry).

## 2. Idempotency: Re-Arm With Existing Cron Entry

**Action**: Create a second teammate task (`TaskCreate` for another Task A). PostToolUse hook fires again and re-emits the Arm directive (per the unconditional-emit discipline — the hook does NOT short-circuit on existing cron).

**Expected**:
- Lead re-invokes `Skill("PACT:start-pending-scan")`.
- Skill body's CronList lookup finds the existing match; the skill no-ops without calling `CronCreate` a second time.
- Post-action `CronList` still contains exactly ONE matching line (NOT two).

**Acceptance**: `CronList | grep -o '/PACT:scan-pending-tasks' | wc -l` still returns `1`. No duplicate cron created.

## 3. Cron-Fire Wake of Scan-Pending-Tasks (5-minute cadence)

**Action**: After Arm, idle the lead session (no manual tool calls). Wait at the next between-tool-call boundary for the cron to fire.

**Expected**:
- Within ~5 minutes (allow up to ~6 minutes for jitter — cron may fire up to 10% of its period late, max 15 minutes), the cron fires `/PACT:scan-pending-tasks` as the lead's next turn.
- The fired prompt has the `[CRON-FIRE]` discipline marker at the top of the skill body (verifiable by inspection of the loaded skill content).
- Scan body runs steps 1-5 from `scan-pending-tasks.md §Operation`:
  - Step 1: `TaskList` returns the active tasks (filtered by owner + status + intentional_wait reason).
  - Steps 2-5: raw-read metadata + accept-or-skip-or-emit-nothing.

**Acceptance**: A scan-fire turn occurs within 5 minutes of Arm. The scan body executes against the actual filesystem (verifiable by post-scan inspection of any task-state changes or by the absence of changes if no acceptance was due).

## 4. Scan-Output Discipline — All 5 Guardrails Empirically Verified

This is the load-bearing verification — the 5 anti-hallucination guardrails are why this mechanism exists.

### 4a. Read-Filesystem-Only

**Action**: Before the next cron-fire, manually inject misleading prose into a teammate `SendMessage` that mentions a fake teachback (e.g., "Teachback submitted!") WITHOUT actually writing `metadata.teachback_submit`. Wait for the next cron-fire.

**Expected**: The scan does NOT accept the task. It reads the raw task JSON, finds `metadata.teachback_submit` is absent, and skips the task (per Race-Window-Skip race-window-skip) or emits nothing (per Emit-Nothing-If-Empty if no other candidates).

**Acceptance**: Task remains `in_progress`; no `TaskUpdate(status="completed")` is invoked; no acceptance SendMessage sent to the teammate.

### 4b. No-Narration

**Action**: Observe the lead session's transcript across multiple cron-fire cycles (at least 3 fires during active teammate work).

**Expected**: No prose lines like "Scanning…", "Skipping task #N…", "Race window detected…", or any per-fire status narration appear in the transcript.

**Acceptance**: User-visible output across the 3+ fires is either (a) an actual acceptance two-call pair, or (b) silence. Nothing else.

### 4c. Raw-Read-Metadata

**Action**: Inspect the scan-body execution log (if available) or trace the file reads. Confirm the scan reads `~/.claude/tasks/{team_name}/{id}.json` directly, NOT via `TaskGet`.

**Expected**: The metadata is sourced from raw file reads, not from `TaskGet`'s metadata-stripped response.

**Acceptance**: The scan's read of `metadata.teachback_submit` / `metadata.handoff` returns populated payloads when those fields ARE on disk (regardless of what `TaskGet` might surface separately).

### 4d. Race-Window-Skip

**Action**: Have a teammate write `metadata.teachback_submit` and send the wake-SendMessage in rapid succession (back-to-back tool calls). The teammate's write may still be flushing to disk when the next cron-fire occurs.

**Expected**: If the cron fires DURING the write-flush window, the raw read returns null/empty, and the scan SKIPS the task (no rejection, no SendMessage, no `TaskUpdate`). The next cron-fire (5 minutes later) re-evaluates after the write has landed.

**Acceptance**: At least one cron-fire turn observes the empty metadata and skips silently; the subsequent cron-fire (after the write lands) accepts the artifact correctly.

### 4e. Emit-Nothing-If-Empty

**Action**: With no pending teachbacks/handoffs (all teammates mid-work, no `awaiting_lead_completion` tasks), observe at least 3 consecutive cron-fires.

**Expected**: Each fire produces NO output. The user sees no transcript entry for the cron-fire turn.

**Acceptance**: 3+ consecutive empty cron-fires produce zero user-visible output.

## 5. Acceptance via Scan (Happy Path)

**Action**: Have a teammate write a complete `metadata.teachback_submit` (4 fields per pact-teachback skill). Send the wake-SendMessage. Wait up to 5 minutes.

**Expected**:
- Within the next cron-fire window, the scan reads the metadata, validates per `pact-completion-authority §12 Teachback Review`, and invokes the canonical acceptance two-call pair:
  1. `SendMessage(to=teammate, message="<wake-signal confirming acceptance>")` FIRST.
  2. `TaskUpdate(taskId, status="completed")` SECOND.
- Task A transitions to `completed`; teammate's Task B becomes unblocked.

**Acceptance**: Task A status is `completed`. The acceptance SendMessage appears in the teammate's inbox before the `TaskUpdate` call (verifiable from the journal event ordering).

## 6. Hook-Level Session Guard (Lead-Session Guard at Directive Emission, Layer 0)

**Action**: In a second concurrent PACT session (with a teammate-typed agent role, NOT the lead) that shares the same `team_name`, trigger a `TaskCreate` or `TaskUpdate(status="in_progress")` event. Observe whether `wake_lifecycle_emitter.py` emits the Arm directive.

**Expected**: No Arm directive emitted in the teammate session — the hook's `is_lead_context` check fails (the platform-stamped `agent_id` field is present on the teammate-frame PostToolUse stdin payload, triggering the compound discriminator's teammate-context branch), and the hook exits with `suppressOutput`.

**Acceptance**: The teammate session sees no `start-pending-scan` directive in `additionalContext`. No spurious cron is registered in the teammate's session.

## 7. Teardown via Last-Active-Task Transition

**Action**: Complete all teammate tasks (or have all reach `completed` / `deleted` terminal status). Verify the same-teammate-continuation guard does NOT defer Teardown (no remaining same-teammate active continuation in any task's `blocks` chain).

**Expected**:
- PostToolUse hook detects the 1→0 transition and emits a stop-pending-scan directive.
- Directive text exactly: `No active teammate work remaining. You MUST invoke Skill("PACT:stop-pending-scan") before your next tool call to delete the /PACT:scan-pending-tasks cron. This is a non-negotiable lifecycle gate. Best-effort — tolerates a cron that was already auto-deleted (7-day expiry) or never registered.`
- Lead invokes the skill; skill body's CronList lookup finds the match; `CronDelete(id=<extracted-8-char-id>)` succeeds.
- Post-Teardown `CronList` shows no match for `/PACT:scan-pending-tasks`.

**Acceptance**: `CronList | grep -o '/PACT:scan-pending-tasks' | wc -l` returns `0`.

## 8. Bug A Preservation — Deferred Teardown on Same-Teammate Continuation

**Action**: Spawn a teammate with Task A blocking Task B (canonical Two-Task Dispatch shape). Have the teammate complete Task A (the teachback gate) — Task A reaches `completed` while Task B is still `pending` (same owner). Watch for PostToolUse hook behavior.

**Expected**: The hook detects the 1→0 transient (Task A complete, but Task B still pending with same owner) and DEFERS the Teardown emit per `has_same_teammate_continuation` predicate. The cron entry remains armed.

**Acceptance**: After Task A completes, `CronList` still contains the cron entry. No spurious Teardown was emitted. When Task B subsequently completes (and no further same-teammate continuation exists), the regular 1→0 Teardown fires normally.

## 9. Bug B Preservation — Re-Arm on Pending->In-Progress Transition

**Action**: After Teardown (cron entry absent), have a teammate claim a `pending` task via `TaskUpdate(status="in_progress")`. Observe hook behavior.

**Expected**: The hook detects the pending→in_progress transition and emits the Arm directive (since `active_count >= 1` post-transition and no cron is registered). The lead invokes `start-pending-scan` and the cron is re-registered.

**Acceptance**: Post-transition `CronList` contains exactly one `/PACT:scan-pending-tasks` entry.

## 10. `[CRON-FIRE]` Discipline Marker Presence Verification

**Action**: Open `pact-plugin/commands/scan-pending-tasks.md`. Confirm the file body begins with the marker `[CRON-FIRE] This skill is invoked by the platform cron scheduler, NOT by user input.` — appearing BEFORE the `## Overview` section header.

**Expected**: Marker is present at the top of the skill body, byte-identical to the architecture spec.

**Acceptance**: `head -5 pact-plugin/commands/scan-pending-tasks.md | grep -c '\[CRON-FIRE\]'` returns `1` (marker present in the first 5 lines).

## 11. Cross-Skill Byte-Identity (Cross-Skill Prompt-String Byte-Identity)

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

## 12. Step 0.5 Self-Correcting Teardown — Empirical Verification

This step empirically verifies the Option D self-correcting fallback path that catches orchestrator non-compliance with the `_TEARDOWN_DIRECTIVE` `additionalContext` channel. See architecture doc `docs/architecture/819-cron-self-teardown.md` and memory `a7bcd37f` for the architectural tension this section dogfoods.

### 12a. Simulate Orchestrator Non-Compliance

**Action**: With the cron armed (after Step 1) and a teammate task active, trigger a 1→0 transition (have the teammate complete the last active task). The `wake_lifecycle_emitter.py` PostToolUse hook will write a `teardown_request` event to the journal AND emit the `_TEARDOWN_DIRECTIVE` `additionalContext`. **As the orchestrator, do NOT act on the directive.** Continue with whatever non-teardown action would naturally follow (e.g., continue conversation with the user, run an unrelated tool call).

**Expected**:
- The journal contains a `teardown_request` event with `ts` AFTER the latest `scan_armed.armed_at`.
- `CronList` still contains the `/PACT:scan-pending-tasks` entry (no Teardown has been invoked).
- No `scan_disarmed` event has been written.

**Acceptance**: `python3 pact-plugin/hooks/shared/session_journal.py read-last --type teardown_request --session-dir <SD>` returns a populated event with ISO `ts`. `CronList | grep -o '/PACT:scan-pending-tasks' | wc -l` returns `1`.

### 12b. Wait for Next Cron-Fire Within 5 Minutes

**Action**: Idle for up to 5 minutes (allow up to ~6 minutes for cron jitter). The next cron-fire of `/PACT:scan-pending-tasks` will execute Step 0.5.

**Expected**:
- Step 0's warmup-grace check passes (more than 300s have elapsed since the latest `scan_armed`).
- Step 0.5's bash block computes: `LATEST_TEARDOWN_REQUEST > LATEST_SCAN_ARMED` is TRUE; `LATEST_SCAN_DISARMED` is empty (no prior disarm in this arm cycle); the condition fires; the bash exits 0.
- The scan-body LLM-side action: invoke `Skill("PACT:stop-pending-scan")` and return without continuing to Steps 1+.
- `stop-pending-scan` body executes: `CronList` lookup finds the match; `CronDelete` succeeds; `scan_disarmed` event is written.

**Acceptance**: Within 5 minutes of 12a, post-cron-fire `CronList | grep -o '/PACT:scan-pending-tasks' | wc -l` returns `0` AND `python3 pact-plugin/hooks/shared/session_journal.py read-last --type scan_disarmed --session-dir <SD>` returns a populated event with `disarmed_at` greater than the `teardown_request.ts` (converted to epoch).

### 12c. No-Narration Discipline Across Self-Teardown Fire

**Action**: Observe the lead session transcript across the Step 0.5 fire from 12b.

**Expected**: No prose lines like "Detected pending teardown, invoking stop…", "Self-correcting…", or any narrative output. The only user-visible output across the fire turn is the `Skill("PACT:stop-pending-scan")` invocation itself (which the scan body discipline allows per the No-Narration allowed-outputs list item (c) per §No-Narration in `scan-pending-tasks.md` and the §Scan Discipline mirror in `pact-communication-charter.md`).

**Acceptance**: No status-narrating prose in the transcript across the self-teardown fire turn.

### 12d. Idempotency Across Repeat Fires (Edge Case)

**Action**: Re-arm the cron (spawn a new teammate task to trigger the Arm hook). Have the teammate complete to trigger another 1→0 transition (writes a new `teardown_request`). Wait for the next Step 0.5 fire and observe.

**Expected**: Step 0.5 fires again (the new `teardown_request.ts` > new `scan_armed.armed_at`). The cycle is independent of the prior 12a-12c cycle; latest-event semantics select the most recent triple. No interference from prior cycle's events.

**Acceptance**: Re-arm cycle's self-teardown fires correctly; `scan_disarmed` event written; cron deleted.

### 12e. Strptime Format Coupling Verification

**Action**: Inspect the ISO format literal in the Step 0.5 bash block. Compare against `session_journal.py` `make_event` line 325.

**Expected**: Both contain the identical literal `"%Y-%m-%dT%H:%M:%SZ"`. Any drift would silently break the ISO→epoch conversion.

**Acceptance**: `grep -c '%Y-%m-%dT%H:%M:%SZ' pact-plugin/commands/scan-pending-tasks.md` returns `1` (Step 0.5 contains it). `grep -c '%Y-%m-%dT%H:%M:%SZ' pact-plugin/hooks/shared/session_journal.py` returns >=1 (`make_event` and any callers contain it). The literal must match BYTE-IDENTICAL across both files.

## Pass Criteria

All 12 steps pass independently. A single step failure fails the runbook and must be triaged before merging the next PACT release.

## Failure Triage

- **Step 1 / 2 fail**: hook diagnostic issue or skill body Lead-Session Guard logic regression. Inspect `wake_lifecycle_emitter.py` and `start-pending-scan.md`.
- **Step 3 fails**: platform cron primitive issue or 5-minute cadence misconfiguration. Check `CronCreate` call shape against canonical schema.
- **Step 4 (any sub-step) fails**: load-bearing guardrail violation. Treat as a P0 regression; the 5 guardrails are why this mechanism exists.
- **Step 5 fails**: completion-authority procedure regression. Inspect canonical acceptance two-call pair in `scan-pending-tasks §Lead-Only Completion Contract`.
- **Step 6 fails**: hook-level lead-context guard regression. Inspect `is_lead_context` — consumed by `wake_lifecycle_emitter.py` for PostToolUse fires, `teardown_request_emitter.py` for TaskCompleted fires, and `session_init.py` for SessionStart fires — defined in `shared/wake_lifecycle.py`.
- **Step 7 / 8 / 9 fail**: lifecycle predicate regression. Inspect `count_active_tasks` and `has_same_teammate_continuation` in `shared/wake_lifecycle.py`.
- **Step 10 / 11 fail**: structural-test territory; the corresponding test suite should have caught this in CI. Re-run `test_scan_pending_tasks_command_structure.py`.
