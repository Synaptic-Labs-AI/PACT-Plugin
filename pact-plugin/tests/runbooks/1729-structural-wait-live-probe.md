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
| _pending — execute post-merge per §2–§4_ | 0 | | | | | n/a | | | |
| | A | | | yes | | | | | |
| | B | | | no | n/a | | n/a | | |

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
