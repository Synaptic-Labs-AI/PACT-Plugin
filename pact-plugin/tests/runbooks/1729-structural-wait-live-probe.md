# Runbook: Structural Wait Mechanism — Live Acceptance Probe

**Purpose:** the structural wait mechanism's acceptance gate. Fixtures
certify the watcher CLI, the arm-supply advisories, the tier-3 outlet and
the turn-end carve-out (see `tests/test_pact_wait.py`,
`tests/test_structural_wait_matrix.py`, `tests/test_turn_end_wait_carveout.py`,
`tests/test_wait_filler_gate.py`). What NO fixture can certify: that the
mechanism actually ends a real orchestrator wait-loop in a live session —
a hook-output fixture cannot demonstrate that a model stopped emitting
filler tool calls, and wake delivery to an idle lead is
mechanism-verified but not live-measured. This probe measures that, per
the plan's TEST phase (issue #1729).

**Operator split:** the TEST engineer DESIGNS this protocol and audits the
evidence; the team-lead RUNS it (the probe requires patching the installed
plugin — global effect — so patch permission is the lead's stop-and-ask
to the user, per the pinned probe protocol). The lead is the SUBJECT of
arm A; the test engineer must not observe the subject mid-run
(subject/observer split), so evidence flows through the transcript and
task records, not through live narration.

---

## §1 — Preconditions (all arms)

1. **Patch discipline** (pinned probe protocol): verify the installed
   plugin files are byte-identical to `git main` BEFORE backing up; back
   up with checksums PLUS an explicit delete-list for files the patch
   adds; compute the true import closure of anything imported (a missing
   schema module fails journal writes for an unrelated reason — read
   stderr before misreading a false negative). On revert: restore, delete
   added files, clear `__pycache__` (a stale `.pyc` survives a file
   restore), verify against BOTH restore sources, smoke the hooks, and
   RE-RUN the confirming arm after the fix or change under test lands.
   Files the patch must cover: `hooks/pact_wait.py`,
   `hooks/shared/wait_target.py`, the two emitting gates
   (`task_lifecycle_gate.py`, `wait_filler_gate.py`) and
   `hooks/shared/turn_end_gate.py` — or the fewest of these the arm
   under test actually loads.
2. **Human-present precondition:** background-completion wakes reach an
   idle lead only when the human is present at the terminal (April
   wake-map record: 5/5 present vs 0/16 away). Run the probe immediately
   after the permission grant, with the user at the keyboard. If the user
   must leave, STOP and reschedule — an away-run measures nothing about
   the wake channel and poisons the ledger with a false negative.
3. **Host sleep-blocker control:** the host denies nonzero `sleep`
   commands BEFORE any PACT gate runs. The filler census for arm B is
   restricted to bare `true` forms only, so a denied `sleep 300` can
   never be misattributed to a PACT layer. Never use `sleep N` as probe
   payload; use `true` (the mechanism's own canonical filler).
4. **Clock discipline:** compute every latency from platform envelope
   timestamps or task-output stamps, never from a session's own
   `date -u` (teammate clocks disagreed with platform envelopes by
   20–40 min in the 1620 ledger). Record the clock source per row.

## §2 — Arm 0: wake-channel smoke

Before spending a full episode on arm A, measure the channel it stands on.

1. The lead launches ONE harness-tracked background task —
   `python3 "<installed>/hooks/pact_wait.py" --team <live-team> --task <a
   real pending task id> --predicate handoff --deadline 60` via
   `run_in_background: true` (never shell `&`) — with the target task's
   deliverable deliberately ABSENT.
2. The lead ends its turn and goes idle. Record: does the completion
   notification re-invoke the lead with no external stimulus (no
   message, no user input)?
3. Expected: at ~60s the watcher exits 3 (EXPIRED, loud on stderr) and
   the notification re-invokes the lead. Record the latency
   (platform clock) and the exit code reported in the notification.
4. **Scoring:** delivered-without-manual-interrupt within completion +
   3 min = channel confirmed, proceed to arm A. Nothing within 10 min =
   channel FALSIFIED for this machine/version — STOP; the mechanism
   degrades to the plan's pre-specified fallback (detect-and-escalate
   toward the user; cron silence-backstop becomes primary). Do not run
   arm A on a falsified channel: its result would be uninterpretable.

## §3 — Arm A: the real episode (the acceptance arm)

One live orchestrator episode shaped like the failure the mechanism
replaces.

1. **Setup:** a fresh session with the patched plugin; a real teammate
   dispatched on a real sub-task; `/goal` active; the filler state file
   PRE-EXHAUSTED (seed the state file so the next filler call lands at
   computed tier 3) so the episode reaches the valve-retirement surface
   without burning minutes on benign fills.
2. **The two consecutive waits:** the lead arms the watcher (the
   lifecycle gate's advisory supplies the command; the lead runs it
   VERBATIM as one `run_in_background` Bash call) before ending its
   turn. While the first watcher runs, the lead arms a SECOND watch on
   the same deliverable (the duplicate-re-arm path) — both must be
   visible in the transcript as separate harness-tracked jobs.
3. **The delayed HANDOFF:** instruct the teammate to complete its work
   and write its HANDOFF only after a deliberate delay longer than one
   poll interval (60–120s of real work is enough; the teammate must NOT
   send any message — the HANDOFF write is the only signal).
4. **Expected shape:** the lead ends its turn after arming; NO filler
   calls are emitted while idle; the watcher fires 0 when the HANDOFF
   lands; the completion notification re-invokes the lead WITHOUT any
   manual interrupt; the lead resumes and harvests the HANDOFF.
5. **Tier-3 outlet surface:** if the lead (as subject) emits a filler
   call anyway, the pre-exhausted state makes the denial carry the
   PACT-WAIT-OUTLET arm command — record whether the subject RUNS the
   supplied command (the denial-compliance prior is one of the two
   things this probe exists to measure).
6. **Evidence to capture** (the audit package):
   - tool-call census: filler-shaped calls (`true`/`sleep`/no-op Bash)
     in the window between arm and wake — before/after counts, from the
     transcript;
   - the wake: notification delivered WITHOUT manual interrupt, with its
     timestamp (platform clock) and the watcher's exit code and stdout/
     stderr summary it carried;
   - timeout behavior: if the deadline expires instead (teammate held
     back past the deadline on a repeat run), the EXPIRED exit must
     re-invoke the lead loud, never silently;
   - the transcript row of each arm command (proves
     `run_in_background: true`, not shell `&`);
   - the turn-end gate's behavior: the armed watcher must NOT produce a
     lead-block on turn end (the carve-out) — a block here is a finding
     even if the wake later lands.
7. **Scoring:** arm A PASSES only with zero filler calls in the idle
   window AND a notification-driven wake with no manual interrupt AND a
   successful resume. Any other shape is a finding with the evidence
   attached.

## §4 — Arm B: counter-control (can the probe even see the loop?)

Arm A is only evidence if the same instrument would have caught the OLD
behavior. "Loop" is defined PRE-probe, operationally: three or more
no-information Bash calls (`true`) within any 10-minute window while a
teammate deliverable is pending and no watcher is armed.

1. Identical setup to arm A, but the plugin UNPATCHED (stock installed
   version) and no watcher armed; the same delayed-HANDOFF teammate.
2. Expected: with the filler state pre-exhausted the OLD tier-3 valve
   (or its successor text) appears; the lead (subject) keeps emitting
   fillers because nothing wakes it — the operational loop definition is
   met.
3. If arm B does NOT loop (the subject stops on its own, or the old
   install has no valve to exhaust), the probe CANNOT discriminate
   patched from unpatched on this episode — say so in the ledger; arm A
   then certifies the mechanism works but not that it beats the
   baseline, and the gap goes to the HANDOFF as a limitation.
4. Evidence: the same tool-call census format as arm A, so the two arms
   are directly comparable (filler counts per 10-minute window).

## §5 — Ledger (to be filled at run time)

| # | Arm | Date | Plugin version | Patched? | Watcher armed (how observed) | Filler calls in idle window | Wake delivered (latency, clock source, exit code) | Manual interrupt needed? | Verdict |
|---|-----|------|----------------|----------|------------------------------|-----------------------------|---------------------------------------------------|--------------------------|---------|
| 1 | 0 | 2026-11-21 | 4.8.35+patch | yes | task 16, deadline 60, `run_in_background: true` transcript row | n/a | ~60-75s (platform hook stamps; launch turn and wake turn both stamped 01:09 UTC), exit 3, loud EXPIRED text carried task/team/predicate | no | PASS — channel confirmed |
| 2 | A | 2026-11-21 | 4.8.35+patch | yes | tasks 26 (teachback) + 27 (handoff), deadline 1800/1800, two transcript rows; duplicate re-arm on 26 also armed and fired | 0 in both idle windows | both wakes exit 0, notification-driven, ~1-2 min each (armed ≈01:13, wakes ≈01:15 and ≈01:19) | no | PASS at episode level ×3 waits, one team; §6 two-episode rule ("A single passing episode is one trial"): cross-team repeat outstanding here, run 2026-11-24 (rows 5-6) — zero fillers, carve-out held, duplicate re-arm held |
| 3 | B | 2026-11-21 | 4.8.35 stock | no | n/a — deliberately none | 3 no-info calls in ~2 min (2 valve-ALLOWED inert calls with budget 2→0, 1 exhausted-valve FILLER-LOCK denial) | none — nothing armed, no notification-driven wake at any point | n/a | MET — operational loop definition satisfied on stock; deterministic-termination text proven unenforced |
| 4 | 0 | 2026-11-24 | commit 4f8e3db7 patched over 4.8.36 | yes | team session-46721530: task 1, `teachback_submit`, deadline 60, one `Bash` call with `run_in_background: true` (19:31:14.6Z) | n/a | exit 4 TERMINAL at 19:31:19.6Z (~5 s: `target_missing on 2 consecutive reads`, because task 1 had completed and its file was gone); notification 19:31:19.7Z, lead resumed 19:31:23.1Z with no user turn between; clock: transcript envelope stamps | no | PASS — channel confirmed (resume 3.4 s after the exit). Deviation: the EXPIRED (3) leg was not exercised because the target task no longer existed; the TERMINAL (4) leg and its loud task/team/predicate text were exercised instead |
| 5 | A | 2026-11-24 | commit 4f8e3db7 patched over 4.8.36 | yes | team session-46721530: task 3 `teachback_submit`/1800, the advisory's command run verbatim (19:33:36.7Z); task 4 `handoff`, advisory said 1800, the lead ran 1200 (19:34:13.8Z); both `run_in_background: true` | 0 in both idle windows (no filler-shaped `Bash`, no `TaskList`/`TaskGet`); the seeded filler-state file is unchanged since its 19:29:31Z seed, so no filler call reached the gate and the tier-3 outlet never surfaced | teachback written 19:34:00.5Z, exit 0 notified 19:34:02.6Z, lead resumed 19:34:07.4Z; HANDOFF written 19:36:35.4Z, exit 0 notified 19:36:38.8Z, lead resumed 19:36:45.5Z; clock: transcript envelope stamps (teammate write = its `TaskUpdate` record) | no | PASS — zero fillers, two notification-driven wakes, clean resumes, no turn-end block on a running watcher |
| 6 | A | 2026-11-24 | commit 4f8e3db7 patched over 4.8.36 | yes | team session-ccbbfe9c (second team): task 3 `teachback_submit`/1800 run verbatim (19:41:24.9Z); task 4 `handoff`/1800 run verbatim (19:42:06.8Z); both `run_in_background: true`. The lead also launched its own background shell poll on task 3 (a `seq`/`sleep 10` loop); the turn-end gate blocked once, naming that loop and not the watcher, and the lead stopped the loop | 0 in both idle windows | teachback written 19:41:47.5Z, exit 0 notified 19:41:49.9Z, lead resumed 19:41:53.9Z; HANDOFF exit 0 notified 19:44:16.0Z (the teammate's record of the write is stamped 19:44:17.4Z, when its REPL step returned, so the write precedes the notice), lead resumed 19:44:19.9Z; clock: transcript envelope stamps | no | PASS — the cross-team repeat; the carve-out held (only the hand-rolled loop was counted) |

Execution record (2026-11-21, team session-47d94d14, session 47d94d14-b906-4d03-b258-f2af0fa7a212; operator/subject: team-lead; protocol design + audit: test-engineer):

- **Patch discipline**: installed files byte-verified against `origin/main` pre-backup; checksummed backup + delete-list at `/tmp/pact-1729-probe/backup/`; 5-file patch (3 modified, 2 added) verified byte-identical to the worktree; revert restored + deleted + `__pycache__` cleared + dual-source verified (backup sums AND `origin/main`); stock smoke green. Final resting state: stock.
- **Clock source**: platform hook envelope stamps (UserPromptSubmit `HH:MM UTC`); no session-local `date -u` used for any measured latency.
- **Arm-0 deviation note**: two stimulus turns arrived mid-window from PRE-LAUNCH-enqueued teammate traffic (all message timestamps predate the launch); recorded, not manual interrupts. One smoke invocation used `python3 -I`, which suppresses script-dir on `sys.path` and produced a false import failure — instrument error, corrected to the house invocation shape (no `-I`), which is how hooks and the watcher are actually launched.
- **Arm-A deviation note**: the runbook's "fresh session" was executed as THIS live session (goal hook active, matching the incident shape) under a full-team message quarantine (5 teammates instructed to hold all output; the auditor's worktree monitor suspended) — a second interactive orchestrator session is not spawnable from inside this one. Two consecutive waits ran on tasks 26/27 (teachback + handoff) plus the duplicate re-arm on 26. The PostToolUse arm-supply advisories fired live at BOTH supply shapes (dispatch wiring: teachback_submit/1800; teachback acceptance: handoff/1800) and the armed commands were executed verbatim.
- **Arm-B deviation note (material)**: the stock gate initially denied tier 1 because the state file on disk had been written by the patched gate — which no longer writes `valve_budget` — and stock treats a missing/non-int budget as corrupt (UNKNOWN = deny tier 1, valve closed, file untouched; the stock fail-direction invariant). A faithful #1729 baseline required seeding the stock schema (`valve_budget: 2`) — itself live evidence that the valve's state coupling is gone under the patch. With stock schema restored: allowance → allowance → exhausted FILLER-LOCK, deterministic-termination claims ("Your turn ends NOW/NOW — make NO further tool calls") observed unenforced across the episode.
- **Two-episode rule (§6, "A single passing episode is one trial"), as of 2026-11-21**: three distinct wait episodes ran (tasks 16, 26, 27 — two deliverable types: teachback, handoff; deadlines 60/1800/1800), all one team. A cross-team episode was not run that day; recorded as a limitation, not silently folded into the PASS. The 2026-11-24 record below satisfies the rule.
- **Filler-state census source**: the session's own `pact-filler-state/47d94d14-….json` counters (count 6, total 10, budget 0 post-episode) corroborate the transcript census.
- **Probe-worker compliance**: both episodes ran silent as instructed (no SendMessage; handoff writes landed unseen — 27: six string fields; 28: six string fields, 4211B; both audited on disk post-wake). CORRECTION (post-audit, test-engineer finding): the originally recorded task-27 figure "4806B" was a mid-trim instantaneous read — the probe-worker's disclosed size-trim passes raced the lead's acceptance audit; two later independent measurements agree on 4148B (handoff object; 4216B full metadata). The load-bearing claim (six strings, written unseen, present post-wake) verifies at every recorded moment; the task file keeps no history to arbitrate intermediate values.

Execution record (2026-11-24, teams session-46721530 and session-ccbbfe9c, sessions 46721530-c99c-4dc6-bc04-3436a41103e5 and ccbbfe9c-6dcb-4a7c-bce5-3991fca5c210; operator/subject: each session's team-lead, user present; observer: test-engineer, from the transcripts after the runs):

- **Build**: the installed plugin was patched to commit 4f8e3db7 over 4.8.36 for both sessions and restored to main afterwards. The injected advisory shapes ("teachback gate dispatched", "blocker completed", "work task wired") are the 4f8e3db7 readiness rule's.
- **Clock source**: transcript envelope timestamps (`queue-operation` enqueue of each task notification, the lead's next tool call, the teammate's `TaskUpdate` record in its own transcript). No session-local `date -u` was used for a measured latency.
- **Arm-0 deviation**: the watcher targeted task 1, the secretary's briefing, which completed and was removed before the deadline, so the watcher exited 4 TERMINAL after two missing reads instead of 3 EXPIRED at 60 s. The wake channel is what arm 0 measures and it delivered; the EXPIRED leg remains covered by row 1 (2026-11-21).
- **Two-episode rule (§6, "A single passing episode is one trial")**: now satisfied. Arm A passed in two separate teams on 2026-11-24 (rows 5-6), each with a teachback wait and a handoff wait, after the single-team run of 2026-11-21 (row 2).
- **Tier-3 outlet still unexercised live**: session-46721530 was seeded at tier 3 for both its sections, but neither lead made a filler call in any idle window, so no outlet text was shown in either session. The outlet's text and arm command are covered by fixtures only.
- **Arm-A deviations**: in session-46721530 the lead armed task 4 with deadline 1200 instead of the advised 1800 (the deadline is a stated-editable default). In session-ccbbfe9c the lead also started a hand-rolled background poll loop alongside the watcher; the turn-end gate counted that loop and not the watcher, and the lead stopped it before going idle.
- **Non-user turns in idle windows**: teammates' idle notifications and the harness's "Your previous response had no visible output" nudge arrived in both sessions. Neither is user input, and neither lead made a tool call on them.
- **Advisory on the secretary's harvest task**: in both sessions the lifecycle gate supplied a `handoff` arm for the secretary-owned harvest task the lead created after the work completed ("work task wired"). Neither lead ran it. Recorded as an observation for the arming rule's owner set, not a probe failure.

- **Timing-margin annotation (review cycle 1, 2026-11-21; superseded):**
  the three corrupt-boundary arms in `test_pact_wait.py`
  (`test_tolerates_transient_corrupt_read`,
  `test_second_consecutive_corrupt_read_is_terminal`,
  `test_alternating_corrupt_reads_never_turn_terminal`) once timed their
  file rewrites with threading.Timer against the poll clock, and the
  alternating arm flaked under load. They now write each scripted file
  state from the watcher's own sleep, between two polls, so every poll
  reads exactly one state and the outcome does not depend on timing or
  machine load. The scripted-outcome twins
  (`test_one_bad_read_is_tolerated_then_fires`,
  `test_two_consecutive_bad_reads_are_terminal`,
  `test_a_good_read_resets_the_bad_read_streak`) cover the same properties
  without a file.

Run-dates tracking: `RUNBOOK_RUN_DATES.md` carries the execution ledger
pointer for this file.

## §6 — Known caveats

- **R1 prior:** a prior arc recorded 4 lost background-completion
  notices. Arm 0 exists precisely to measure the CURRENT channel before
  arm A spends an episode on it; a single arm-0 failure is a STOP, not a
  retry-until-green.
- **Cron silence-backstop:** the persona's cron backstop stays
  load-bearing regardless of the probe result — it covers the lost-notice
  tail, never the primary condition-based wake. Do not read an arm-A pass
  as license to remove it.
- **Deadline defaults:** 1800 (advisory) and 900 (tier-3 template) are
  stated-editable latency guesses. Record the ACTUAL episode spans in the
  ledger; if real deliverable waits routinely exceed the defaults, the
  numbers get corrected in a follow-up, not silently survived.
- **A single passing episode is one trial.** The 1620 ledger's retracted
  verdict happened because three same-shape trials read as a law. Repeat
  arm A on at least two separate episodes (different teams/tasks) before
  recording a mechanism-level PASS in the ledger.
