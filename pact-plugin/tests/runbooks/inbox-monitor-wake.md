# Empirical Runbook — Inbox Monitor Wake

> Manual dogfood test for two acceptance criteria of the lead-side wake mechanism that cannot be automated from a subagent context.
>
> Background: implements #591 (lead-side path-2 wake mechanism); see git history for full design lineage.

---

## 1. Purpose

This runbook validates two empirical acceptance criteria for the inbox-monitor wake mechanism (a Monitor + Cron pair on the lead's inbox file):

- **AC1 — 5s wake**: in a fresh PACT session, the lead receives an inbound `SendMessage` notification within 5 seconds of emit, every time.
- **AC2 — gate-bypass**: Monitor stdout fires a turn on the lead's session even when that session has `isLoading=true` OR `focusedInputDialog=true` — bypassing `useInboxPoller`'s `!isLoading && !focusedInputDialog` gate.

Both ACs are manual because they test behavior of the **lead's own session under specific UI states**. A subagent cannot fabricate `isLoading=true` or `focusedInputDialog=true` on a different session from within itself, so an automated probe cannot satisfy the actual claim.

**Strong prior for AC2.** Prior empirical observation (recorded during the design spike): Monitor stdout-line-as-event triggers a new turn on the lead's session regardless of `useInboxPoller`'s `!isLoading && !focusedInputDialog` gate state. The two turn-trigger mechanisms are independent — `useInboxPoller` polls the inbox file from inside the lead's UI loop and submits at gate-clear; Monitor stdout fires a turn through a separate path that does not consult the gate. This runbook closes the gate-bypass-specific claim by reproducing the observation under deliberately-gated states, not just under unconstrained conditions.

---

## 2. Prerequisites

- A fresh Claude Code session (the **lead** under test). Start `/PACT:orchestrate` (or any other ARMING workflow command) so that the wake mechanism arms.
- A second messaging endpoint — either a PACT teammate spawned in the lead's session, or a separate Claude Code session/subagent — capable of sending inbound `SendMessage` to the lead.
- Tool availability for `Monitor`, `CronCreate`, `CronList`, `TaskList`, `TaskStop`, `CronDelete`, `SendMessage`.
- The team's UUID-prefixed name (e.g., `<your-team-uuid>`); substitute it for `{team_name}` everywhere below.

---

## 3. Setup

1. In the lead session, invoke an ARMING workflow command (e.g., `/PACT:orchestrate`). The command auto-arms Monitor + Cron using the canonical strings in the tracked fixture files: `pact-plugin/tests/fixtures/inbox-wake-canonical/monitor-block.txt` (Monitor) and `pact-plugin/tests/fixtures/inbox-wake-canonical/cron-block.txt` (Cron). The fixtures are byte-equivalent to the canonical blocks inlined at every workflow-command callsite (verified by extension to `verify-protocol-extracts.sh`). Substitute the live `{team_name}` UUID in the runtime tool invocation.

2. **Confirm Monitor is armed**: run `TaskList` and verify a task with description `pact-inbox-monitor:{team_name}:team-lead` is present and `status=in_progress`.

3. **Confirm Cron is armed**: run `CronList` and verify a job with description `pact-inbox-cron:{team_name}:team-lead` is present.

4. **Confirm Monitor is healthy**: read the Monitor task's recent stdout (via `TaskGet` or the monitor log under `~/.claude/teams/{team_name}/monitors/`) and verify at least one `HEARTBEAT count=N ts=T` line within the last 5 minutes.

If any of steps 2-4 fail, do NOT proceed — file the failure as an arm-step regression and stop. The wake mechanism is not under test if it isn't actually armed.

---

## 4. Test 1 — 5s Wake AC

**Goal**: verify the lead receives an inbound `SendMessage` notification within 5s of emit, every time.

### Procedure

1. Record `T0` = current epoch (e.g., `date +%s`).
2. From the second messaging endpoint, send an inbound `SendMessage` to the lead. Record `T1` = emit time.
3. Observe the lead's session for a new turn triggered by the inbox grow.
4. Record `T2` = lead's turn-spawn time (visible in the lead's UI thread, or via the lead's `session_journal` event for the inbound).
5. Compute `Δ = T2 - T1`.
6. **Repeat steps 1-5 a minimum of 5 times** with a 30-60s gap between trials. Record each `Δ`.

### Pass / fail

- **PASS**: every trial has `Δ ≤ 5s` (allowance for the 5s poll cycle + 200ms stdout batch + minimal turn-spawn overhead → 5s is the user-acceptable upper bound).
- **FAIL**: any trial has `Δ > 5s` AND the Monitor task is healthy (recent HEARTBEAT) at the time of the trial. If Monitor is auto-stopped or crashed, recovery latency is governed by the cron's 4-min cadence and is a separate failure mode — note it but do not score it as a 5s-wake failure unless the Monitor was demonstrably alive.

If FAIL: file an algedonic ALERT with the recorded `Δ` series and the Monitor stdout context. The 5s claim is load-bearing for the design's user-acceptable-latency promise.

---

## 5. Test 2 — Gate-Bypass AC

**Goal**: verify Monitor stdout fires a turn on the lead even when `isLoading=true` OR `focusedInputDialog=true`.

### Procedure A — `isLoading=true`

1. In the lead session, kick off a long-running operation that keeps `isLoading=true` for ≥30s. A multi-step Task or a slow Bash with `sleep 30` works.
2. While `isLoading=true` is asserted (the operation is mid-flight), send an inbound `SendMessage` to the lead from the second endpoint. Record `T1` = emit time.
3. Observe whether the lead's session spawns a new turn for the inbound *while still in `isLoading=true` state*. Record `T2` = turn-spawn time.
4. Note the lead's `useInboxPoller` state heuristic at `T2`: was the original long-running op still in flight (i.e., still `isLoading=true`)? Read the visible UI thread or session_journal.

### Procedure B — `focusedInputDialog=true`

1. In the lead session, surface a `focusedInputDialog` — the most reliably-reproducible mechanism is via `AskUserQuestion`, which sets `focusedInputDialog=true` while awaiting user response.
2. While the dialog is open and unanswered, send an inbound `SendMessage` to the lead from the second endpoint. Record `T1` = emit time.
3. Observe whether the lead's session spawns a new turn for the inbound *while the dialog is still open*. Record `T2` = turn-spawn time.
4. Note `useInboxPoller` state at `T2`: was the dialog still open?

### Pass / fail (both procedures)

- **PASS**: turn fires within 10s of `T1` (5s poll + 200ms batch + turn-spawn overhead) AND the gated state was still asserted at `T2`. This confirms Monitor's stdout-line-as-event mechanism bypasses `useInboxPoller`'s `!isLoading && !focusedInputDialog` gate.
- **FAIL**: lead's session does NOT spawn a turn until the gated state clears (i.e., the gate held; Monitor stdout did NOT bypass it).

If either procedure FAILs: the design's load-bearing claim is refuted. File an algedonic ALERT with `T1`/`T2`/state evidence; the implementation transitions to BLOCKED pending redesign. Cite this runbook's recorded values and the `useInboxPoller` `!isLoading && !focusedInputDialog` gate as the evidence triangle to surface.

---

## 6. Teardown

1. `TaskStop` the Monitor task: find it via `TaskList` filtered by `pact-inbox-monitor:{team_name}:team-lead`, then `TaskStop(task_id)` for each match.
2. `CronDelete` the Cron job: find it via `CronList` filtered by `pact-inbox-cron:{team_name}:team-lead`, then `CronDelete(job_id)` for each match.
3. **Confirm clean**: re-run `TaskList` and `CronList`; both filters should return 0 matches.
4. Errors on missing IDs are benign (the canonical teardown block treats them as no-ops).

---

## 7. Test 3 — Monitor-Crash → Cron Recovery (manual integration)

The recovery-rule's prose-pseudocode semantics are covered by the unit tests in `pact-plugin/tests/test_inbox_wake_recovery.py::TestRecoveryRuleBranchLogic` (heartbeat-staleness threshold + 3-branch tree shape). The live wall-clock variant — arm Monitor, kill it, wait for the cron-fire to detect staleness and re-arm — is deferred to this runbook step because a 7-min CI test is flake-prone and slow.

### Procedure

1. Setup per §3 (start a fresh session in any workflow-arming command). Confirm `~/.claude/teams/{team_name}/inbox-wake-state.json` and `inbox-wake-heartbeat.json` are present.
2. Note `armed_at` and the heartbeat's `ts` field.
3. `TaskStop(STATE_FILE.monitor_task_id)` — kill the Monitor without writing teardown.
4. **Do nothing**. Wait for the next cron-fire (≤ 4 minutes). The heartbeat file's `ts` will not advance once the Monitor is dead, so within 7 minutes (420s threshold) the heartbeat will go stale.
5. After the next cron-fire that follows the staleness boundary, re-inspect the registry:
   - `STATE_FILE` should exist with a fresh `armed_at` AND a fresh `monitor_task_id` (different from the killed one).
   - `inbox-wake-heartbeat.json` should have a current `ts` (within last 5s).
   - `TaskList` should show a new `pact-inbox-monitor:{team_name}:team-lead` task (the re-armed Monitor).

### Pass / fail

- **PASS**: registry refreshes within 11 minutes of TaskStop (4 min worst-case cron-fire + 7 min staleness boundary). The new `monitor_task_id` is healthy (heartbeat advancing).
- **FAIL**: registry never refreshes after 12 minutes; OR the new monitor's heartbeat is also stalled; OR multiple `pact-inbox-monitor` tasks accumulate (recovery rule's TaskStop on the old monitor failed silently).

If PASS, the recovery rule is confirmed end-to-end. If FAIL, triage via the cron-fire turn's stdout (look for the recovery-rule's branch log lines).

---

## 8. Recording Results

Append a dated entry to `pact-plugin/tests/runbooks/inbox-monitor-wake-runs.md` (create the file if it does not exist) with:

- Date + Claude Code version + plugin version.
- Test 1: each `Δ` value across the trial series; PASS/FAIL.
- Test 2 Procedure A: `T1`, `T2`, gated-state-still-asserted-at-T2 (yes/no); PASS/FAIL.
- Test 2 Procedure B: same fields; PASS/FAIL.
- Test 3 (optional, gated): wall-clock from TaskStop to registry-refresh; new `monitor_task_id` healthy (yes/no); PASS/FAIL.
- Notes: any anomalies (Monitor auto-stop, cron-fire interference, recovery-rule activations during the trial).

A run with Tests 1 + 2 PASS is sufficient evidence for the empirical ACs. Test 3 is optional but recommended quarterly — automated coverage of the recovery rule is unit-level (parses the prose-pseudocode); Test 3 is the live confirmation. A run with any FAIL must be triaged before the wake-mechanism work merges.
