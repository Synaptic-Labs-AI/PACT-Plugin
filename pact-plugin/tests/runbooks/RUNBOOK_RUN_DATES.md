# Runbook Run Dates

Tracks manual runbook execution dates. Each runbook in this directory has a
section below; append a new dated entry on each manual run.

Manual runbook execution is REQUIRED before each minor release tag and
informs the fallback-ladder trigger criteria evaluation across sessions
(the lazy-load fidelity dogfooding signal cannot be CI-tested; runbook
data is the substitute).

## 591-inbox-wake.md

| Run date (UTC) | Operator | Plugin version | Sections passed | inline-mission mode | Notes / per-section observations |
| -------------- | -------- | -------------- | --------------- | ------------------- | -------------------------------- |
| _pending — execute post-merge in fresh session_ | | | /9 | n/a | Arm-on-first-active-task (§5 step 3) PASS/FAIL · STATE_FILE on disk (§5 step 4) PASS/FAIL · FIRST_GROW + LAST_GROW (§5 steps 5-7) PASS/FAIL · Teardown on last-task transition (§5 step 8) PASS/FAIL · /wrap-up safety-net Teardown idempotent (§5 step 9) PASS/FAIL. |

Sections-passed denominator is 9 per runbook §5 (the End-to-End Runbook nine steps). The inline-mission mode column does not apply to this runbook (`n/a`); the column is retained for cross-runbook column parity. If a step fails, map to the failure modes in runbook §3 and file a follow-up issue with the journal evidence.

## 662-dispatch-gate.md

| Run date (UTC) | Operator | Plugin version | Sections passed | inline-mission mode | Notes / counter-test outcomes |
| -------------- | -------- | -------------- | --------------- | ------- | ------------------------------- |
| _pending — execute post-merge in fresh session_ | | 4.1.3 | /8 | warn (default) | matcher-fidelity (§1+§1.1) PASS/FAIL · Bash-touch bypass (§2+§2.1) PASS/FAIL · inline-mission advisory injection (§3) WARN-visible / WARN-dropped → flipped to deny · inline-mission shadow (§3.1) PASS/FAIL · inline-mission deny (§3.2) PASS/FAIL · sabotaged-import fail-closed (§4) PASS/FAIL. |

Sections-passed denominator is 8 per runbook §5 (§1, §1.1, §2, §2.1, §3, §3.1, §3.2, §4). The inline-mission mode column records whether the empirical observation kept the production default at `warn` or motivated a flip to `deny`. If a section fails, file a follow-up issue and link it in the Notes column.

## 691-bootstrap-secretary-dispatch.md

| Run date (UTC) | Operator | Plugin version | Sections passed | inline-mission mode | Notes / per-section observations |
| -------------- | -------- | -------------- | --------------- | ------------------- | -------------------------------- |
| _pending — execute post-merge in fresh session_ | | 4.1.6 | /4 | warn (default) | gate-clean spawn (§1) PASS/FAIL · Task A/B structural shape (§2) PASS/FAIL · acceptance two-call pair (§3) PASS/FAIL · briefing-within-one-wake (§4) PASS/FAIL. |

Sections-passed denominator is 4 per runbook §5 (§1, §2, §3, §4). The inline-mission mode column records whether the production default (`warn`) was in effect during the run or whether the operator overrode to `deny` / `shadow`. If a section fails, file a follow-up issue per the severity tiers in runbook §5 and link it in the Notes column.

## 885-team-registration-smoke.md

| Run date (UTC) | Operator | Plugin version | Sections passed | inline-mission mode | Notes / per-section observations |
| -------------- | -------- | -------------- | --------------- | ------------------- | -------------------------------- |
| 2026-06-02 | michael-wojcik | 4.4.2 (overlay of 4.4.3 branch) | 3/3 | n/a | PASS. Standard teammate (smoke-probe): invoked `pact-team-registration` first → ran the register command → registry line `{own session_id → smoke-probe@pact-6f81f147}`; `resolve()` recovered it (members-validated). Secretary (smoke-secretary): registered `{own session_id → smoke-secretary@pact-6f81f147}`; `resolve()` recovered it. Integrity: `resolve(bogus)` → None. Overlay reverted (cache pristine 4.4.2, registry reset). **OVERLAY LESSON**: a whole-tree rsync carried the branch's 4.4.3 version bump into the 4.4.2 cache → the HMAC-signed bootstrap marker (signs `plugin_version`) mismatched → `bootstrap_gate` fail-closed → dispatch blocked until `.claude-plugin/plugin.json` was restored to 4.4.2. The §Step 1 recipe now EXCLUDES the version files so the cache version stays matched to the signed marker. |
| _pending — re-run PRE-merge in fresh tmux session (overlay)_ | | | /3 | n/a | Standard-teammate first-action = `Invoke Skill("PACT:pact-team-registration")` (§Step 2) PASS/FAIL · Secretary registers `secretary@<team>` before briefing (§Step 3) PASS/FAIL · Registry non-empty with valid `session_id`→`name@team` per teammate (§Step 4) PASS/FAIL · Overlay reverted (§Step 5) DONE/PENDING. |

Sections-passed denominator is 3 per runbook (standard-teammate first-action; secretary register-before-briefing; registry-non-empty assertion). The inline-mission mode column does not apply (`n/a`). Unlike the post-merge runbooks above, this is a **PRE-merge overlay smoke** — the empirical gate the LEG-4 NO-GO requires before this PR merges. If any section is RED, apply an architecture-doc §8.6 contingency and do **not** merge. The overlay revert (§Step 5) is mandatory regardless of outcome.

## 923-missed-wake-live-probe.md

| Run date (UTC) | Operator | Plugin version | Sections passed | inline-mission mode | Notes / per-mode observations |
| -------------- | -------- | -------------- | --------------- | ------------------- | -------------------------------- |
| 2026-06-07 | michael-wojcik | 4.4.12 | tmux 6/6 — PASS (real platform surface confirmed) | n/a | **TMUX session** (registry: teammates in their OWN sessions — secretary `a69eea0d`, probe-wait `065360b8`; lead `4aa3b619`; iTerm2 panes). Real tmux topology: separate-process teammates wrote tasks into `~/.claude/tasks/pact-4aa3b619/`; lead resolved via UNSTUBBED `get_task_list` — the exact #923 broken seam. SURFACE (§2a) PASS — BOTH the REAL platform-fired UserPromptSubmit additionalContext (task #4, age ~184min real wall-clock) AND a lead-invoked synthetic fire (task #3) · FORENSIC exactly-one (§2b) PASS (journal: one event per (task_id,since); #3 synthetic ts 2026-06-07T23:38:26Z, #4 REAL-platform ts 2026-06-08T02:09:07Z) · PERSISTENT+DEDUP (§2c) PASS (re-fire re-shows surface, no 2nd event per key) · AUTO-CLEAR (§2d) PASS (resolve task → suppressOutput, no new event) · teammate_idle 2nd-bug (§2 Step 5) PASS (suppress@1-2, suggest@N=3, force+ACTION@N=5) · CONTINGENCY CLAUDE_CODE_TASK_LIST_ID unset (§2 Step 6) CLEAR. **tmux MANDATORY gate GREEN — #923 runtime-confirmed for tmux.** Note: synthetic phase initially mis-assumed in-process; corrected to tmux via the teammate registry (concrete instance of the dual-mode pin: key on session_id-vs-leadSessionId structural signal, not an assumed flag). |
| 2026-06-07 | michael-wojcik | 4.4.12 | in-process 6/6 — PASS (real hook, real resolver, real journal) | n/a | **IN-PROCESS session** (`leadSessionId == session_id == b6945cc3`). Real-preferred path: spawned teammate `probe-tm` authored a genuine `awaiting_lead_completion` wait via `TaskUpdate` → flushed to `~/.claude/tasks/pact-b6945cc3/2.json`; `since` back-dated on disk; real `missed_wake_scan.py` fired over the UNSTUBBED `get_task_list` (the #923 broken seam). SURFACE (§2a) PASS (real lead `UserPromptSubmit` frame → alarm naming probe-tm / #2 / ~62min / corrective action) · FORENSIC exactly-one (§2b) PASS (1 event: task_id=2, agent=probe-tm, since=2026-06-07T22:41:33+00:00, ts=2026-06-07T23:44:13Z) · PERSISTENT+DEDUP (§2c) PASS (re-fire re-shows, age→~63min, journal still 1 event; dedup on (task_id,since)) · AUTO-CLEAR (§2d) PASS (real lead acceptance: wake-SendMessage + `TaskUpdate` completed → post-resolve fire `suppressOutput`, no new event) · teammate_idle 2nd-bug (§2 Step 5) PASS (suggest@idle=3, shutdown_request@idle=5) · CONTINGENCY `CLAUDE_CODE_TASK_LIST_ID` unset (§2 Step 6) CLEAR. **in-process gate GREEN.** Wiring note: in-process completed tasks are removed from disk and the lead's own fresh task isn't immediately hook-readable, but a teammate's in-progress `TaskUpdate` flushes promptly — the exact state the surfacer needs. **Both modes now PASS → #923 fully runtime-confirmed; the standing inert-class gate (#861) is cleared.** |

Sections-passed denominator is 2 per runbook §4 (tmux live-probe; in-process live-probe), each gated on all of §2 (a)+(b)+(c)+(d)+Step 5 with the Step 6 tripwire clear. The inline-mission mode column does not apply (`n/a`). Unlike a pre-merge overlay smoke, this is the **POST-merge acceptance gate** — a green suite does NOT close #923; the tmux live-probe is mandatory. If tmux is RED, re-open and route back to the resolver. If the Step 6 tripwire finds `CLAUDE_CODE_TASK_LIST_ID` SET in a team session, HALT + architect escalation (Design A/B divergence, devops contingency C).

## v4.0.0-launch-and-isolation.md

| Run date (UTC) | Operator | Plugin version | Sections passed | Notes / fallback-ladder signals |
| -------------- | -------- | -------------- | --------------- | -------------------------------- |
| 2026-05-04 | michael-wojcik | 4.0.0 | 8/8 | Section 4: 80% (4/5) — S3/S4, algedonic, state-recovery, completion-authority all YES; communication-charter NO on body-overlap prompt. Required two intervention commits to clear (`0b08df87` strengthened phrasing, `11145446` top-of-body pre-commitment + first-person voice). Charter NO mechanism (body §1 Pre-Response Channel Check overlap) confirmed by Session 6 diagnostic; charter cross-reference demoted to soft post-test in `2e53b6b0`. No Section 5 algedonic failures. Section 6 teammate teachback skill preload validated (4-field structured teachback: understanding/most_likely_wrong/least_confident_item/first_action). No fallback-ladder escalation; staying on Option F. Follow-up: [#624](https://github.com/Synaptic-Labs-AI/PACT-Plugin/issues/624) (systematic imperative-cross-reference audit for body-content overlap). |

### How to record a run

1. Execute the runbook end-to-end against a clean session (no prior
   v4.0.0 state on disk).
2. Append a new row above with: ISO date in UTC, operator name, plugin
   version under test, count of sections passed (out of total), and any
   fallback-ladder signals observed (lazy-load fidelity YES/NO across the
   N>=5 sessions section, algedonic emission outcome, teammate teachback
   smoke outcome).
3. If a section fails, file a follow-up issue and link it in the Notes
   column.
4. Recommend a re-run within 30 days if a fallback-ladder signal landed
   below the trigger threshold (<80% YES on lazy-load fidelity).

### Q4 LOCKED

This file is required infrastructure for the fallback-ladder evaluation;
do not delete or rename. Contents are append-only — historical runs are
the calibration record.
