# Runbook: Missed-Wake Surfacer Live-Probe Acceptance Gate (#923)

**Purpose:** the post-merge acceptance gate for the `get_task_list` team-dir
resolution fix. The fix repairs two hooks that shipped INERT under Agent Teams
(`missed_wake_scan` and `teammate_idle`). A green test suite is **necessary but
NOT sufficient** to close #923 — the v4.4.11 cycle proved that a fully-mocked
suite (and review, and an architect design-verify) can all be green over a
feature that never fires in live operation, because they operate on
mocked/stubbed task input. **Only a live probe confirms the hook actually FIRES
in a real Agent-Teams session.** This runbook defines that probe.

> Why static verification structurally cannot replace this: the broken seam was
> `get_team_name() → ~/.claude/tasks/{team_name}/ → glob`. Every prior unit test
> stubbed `get_task_list` or fed tasks straight to the predicate, so the one
> broken seam was the one every test bypassed. The non-mocked integration suite
> (`tests/test_missed_wake_scan_integration.py`) closes the static gap; this
> live probe closes the residual "does it fire in a real process?" gap.

---

## §1 — Acceptance criterion (NOT closed on green tests alone)

#923 is closed only when BOTH hold:

1. The non-mocked integration suite is green AND its non-vacuity gate FAILS on a
   source-revert of the resolver fix (see `test_missed_wake_scan_integration.py`
   module docstring — expected cardinality `{3 failed, 15 deselected}` on
   `-k non_vacuity_gate` against pre-fix `task_utils.py`).
2. This live probe passes in **both** teammateModes:
   - **tmux = MANDATORY real live-probe** (the dominant mode + where the bug
     bit hardest; N separate processes, N:1 session→team).
   - **in-process = real preferred; faithful-synthetic fallback acceptable** (1
     process per team). The synthetic fallback drives a real team-dir on disk +
     a real `is_lead` UserPromptSubmit frame in a live session — equivalent to
     the integration test but executed in a running process rather than pytest.

---

## §2 — Live-probe procedure (per mode)

Run once per mode. Record the results in `RUNBOOK_RUN_DATES.md`.

**Setup.** Start a real PACT session in the target mode; let it create a team
(`team_name = pact-<uuid8>`). Confirm tasks persist at
`~/.claude/tasks/{team_name}/*.json` (NOT `~/.claude/tasks/{bare-session_id}/`).

**Step 1 — induce a stranded wait.** Have a teammate set
`metadata.intentional_wait = {reason:"awaiting_lead_completion",
expected_resolver:"lead", since:<now>}` on its in-progress task and idle (the
canonical missed-wake: the teammate is waiting for the lead's completion + paired
wake-SendMessage).

**Step 2 — cross the staleness threshold.** Back-date the task JSON's
`intentional_wait.since` to **> 30 min** ago (edit the file directly), or wait
out the real 30-min `wait_stale()` window.

**Step 3 — trigger the lead surface.** Take a lead turn (any
`UserPromptSubmit`) — or start a new session (`SessionStart`). The
`missed_wake_scan` hook fires on the lead's process.

**Step 4 — OBSERVE (the gate):**
- **(a) SURFACE:** the lead's turn-start `additionalContext` carries the
  `PACT missed-wake alarm:` notice naming the stranded teammate + task id +
  subject + age + the corrective action.
- **(b) FORENSIC:** **exactly one** `missed_wake` event lands in the real
  `~/.claude/pact-sessions/{slug}/{session_id}/session-journal.jsonl`
  (`grep '"type":"missed_wake"'`).
- **(c) PERSISTENT + DEDUP:** a second lead turn while still stale RE-SHOWS the
  surface (no surface dedup) but writes **NO 2nd** journal event
  (once-per-`(task_id, since)`).
- **(d) AUTO-CLEAR:** resolve the wait (lead sends the wake / completes or
  re-sets the task) → next lead turn → the surface is GONE (`suppressOutput`),
  no new event.

**Step 5 — teammate_idle companion probe (the 2nd live bug).** Let a teammate
idle past the idle threshold. Confirm `teammate_idle` now FIRES: the
idle-count escalation → shutdown suggestion → `shutdown_request` path runs
(pre-fix it no-op'd because arg-less `get_task_list()` returned `None`). Observe
the idle-cleanup message reaches the lead.

**Step 6 — CONTINGENCY tripwire (defense-in-depth).** While probing, confirm
`CLAUDE_CODE_TASK_LIST_ID` is **unset** in the team-session hook env
(`env | grep CLAUDE_CODE_TASK_LIST_ID` from the hook context, or inspect the
captured stdin). devops's CODE-phase grep confirmed it is never platform-set in
a team context; under Design A (team-first) that env var is consulted ONLY in
the solo branch. **If it is empirically SET to anything in a team session,
HALT and escalate to the architect** — Design A and the rejected Design B
(strict-honor) would diverge for real sessions (devops contingency C), and the
`team × TASK_LIST_ID=SID` matrix cell would need re-derivation.

**PASS** = (a)+(b)+(c)+(d)+Step 5 all hold in the mode under test, and Step 6
finds `CLAUDE_CODE_TASK_LIST_ID` unset. tmux PASS is mandatory; in-process PASS
may use the faithful-synthetic fallback.

---

## §3 — Per-caller audit (#551 inert-class candidates)

`get_task_list` has **4 production callers**, all previously resolving by the
broken `session_id` key. The GLOBAL fix repairs all four with zero call-site
edits. Recommended non-mocked test coverage, **priority by blast radius**:

| # | Caller | Severity under Agent Teams (pre-fix) | Own non-mocked test? | Test shape |
|---|--------|--------------------------------------|----------------------|------------|
| 1 | `missed_wake_scan.py:276` | **INERT** — alarm never fires (the #923 subject) | **DONE** — `test_missed_wake_scan_integration.py` IT-1/IT-2 | `run_surface` e2e via `Path.home` redirect + real team dir → surfaces + 1 journal event |
| 2 | `teammate_idle.py:319` | **INERT** — idle-cleanup + auto-shutdown nudge dead (2nd live bug) | **SMOKE DONE** (IT-6); fuller e2e RECOMMENDED | arg-less `get_task_list()` resolves the team dir → `check_idle_cleanup` escalation path runs |
| 3 | `session_init.py:1214` | **PARTIAL** — post-compaction checkpoint degrades to the bootstrap safety-net | RECOMMENDED (medium) | build the post-compaction checkpoint from a real team dir → `find_feature_task`/`current_phase`/`active_agents` non-empty |
| 4 | `session_end.py:840` | **MINOR** — only the secondary untracked-PR scan lost (primary PR-unpause path still fires via journal) | OPTIONAL (low) | `check_unpaused_pr` reads a real team dir for the secondary scan |

**Key framing:** callers 3 & 4 are already REPAIRED by the GLOBAL fix — their
own non-mocked tests would be **regression-pinning** coverage (prevent a future
re-break), not bug-discovery. The two INERT *alarms* (1 & 2) are the
mandatory live-probe targets above. `dispatch_helpers.has_task_assigned` uses
the CORRECT `iter_team_task_jsons(team_name)` already — it is the reference
pattern, NOT a candidate. Staging the caller-3/4 regression tests as a fast
follow-up after this PR is acceptable.

---

## §4 — Sections-passed denominator

Denominator = **2** (tmux live-probe; in-process live-probe). Each must satisfy
all of §2 (a)+(b)+(c)+(d)+Step 5 with the Step 6 tripwire clear. Record per-mode
PASS/FAIL in `RUNBOOK_RUN_DATES.md`. If tmux is RED, do **not** declare #923
closed — re-open and route back to the resolver. If only in-process is feasible
this cycle, record tmux as `pending` and close only after the tmux probe lands.
