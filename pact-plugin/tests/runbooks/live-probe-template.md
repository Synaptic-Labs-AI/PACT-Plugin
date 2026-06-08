# Runbook TEMPLATE: Hook Live-Probe Acceptance Gate

> **This is a fill-in template, not a runnable runbook.** Copy it to
> `pact-plugin/tests/runbooks/<issue>-<hook>-live-probe.md` and replace every
> `{PLACEHOLDER}` with the concrete value for the hook under test. The worked
> instance to imitate is **`923-missed-wake-live-probe.md`** (the missed-wake
> surfacer); references to it below point at the canonical shape for each
> section. Record every run in `RUNBOOK_RUN_DATES.md` per the per-mode row
> schema there.

**Why this template exists.** A hook can pass its entire mocked unit suite — and
review, and an architect design-verify — while never FIRING in live operation,
because the one broken seam is the one every mocked test stubs. A live probe is
the only thing that certifies a runtime hook actually fires in a real
Agent-Teams session. This template is the reusable procedure for that probe;
instantiate it for any **seam-dependent** hook (one whose observable effect
depends on state resolved at RUNTIME from ambient context — team_name /
session_id / an env-keyed path — and which FAILS SILENT when that resolution
yields empty/None).

**Fill-in legend** (replace throughout):

| Placeholder | Meaning | 923 instance |
|-------------|---------|--------------|
| `{HOOK}` | the hook module under probe | `missed_wake_scan` |
| `{SEAM}` | the runtime-resolved seam the hook depends on | `get_task_list → ~/.claude/tasks/{team_name}/ glob` |
| `{PRECONDITION}` | the real on-disk state that arms the hook | a teammate task with `intentional_wait` |
| `{THRESHOLD_FIELD}` | the field whose value crosses the firing threshold | `intentional_wait.since` (> 30 min stale) |
| `{TRIGGER_EVENT}` | the genuine platform event that fires the hook | a lead `UserPromptSubmit` / `SessionStart` |
| `{SURFACE}` | the user-observable effect | lead turn-start `additionalContext` alarm |
| `{JOURNAL_EVENT}` | the forensic journal event type | `missed_wake` |
| `{DEDUP_KEY}` | the once-per key for the journal event | `(task_id, since)` |
| `{COMPANION}` | a sibling hook sharing the same seam (if any) | `teammate_idle` |
| `{ENV_INVARIANT}` | a defense-in-depth env invariant the design relies on | `CLAUDE_CODE_TASK_LIST_ID` unset in a team session |

---

## §1 — Acceptance criterion (NOT closed on green tests alone)

State the gate explicitly: **a green test suite is NECESSARY but NOT SUFFICIENT
to close the originating issue.** Mocked unit tests certify pure logic; a
non-mocked integration test certifies the resolution seam wiring; only this live
probe certifies the hook FIRES under real platform hook-dispatch. (See
`923-missed-wake-live-probe.md` §1 for the worked statement.)

The issue is closed only when ALL hold:

1. The non-mocked integration suite for `{HOOK}` is green AND its non-vacuity
   gate FAILS on a source-revert of the fix (a documented revert cardinality,
   e.g. `{N failed, M deselected}` — see the integration test's module
   docstring). A revert against a still-mocked test catches nothing, so the seam
   MUST be un-mocked first.
2. This live probe passes in **both** teammateModes (see the MODE MATRIX in §2).

**Gate PASS-iff** (the evidence record — mirror this into `RUNBOOK_RUN_DATES.md`):
the gate is SATISFIED for plugin version `{V}` iff EITHER

- **(A) PROBED:** (i) every seam-dependent hook the PR touches has ≥1 non-mocked
  L2 integration test whose non-vacuity gate fails on a source-revert; AND (ii)
  for every L3 hook the PR touches, a `RUNBOOK_RUN_DATES.md` row exists at
  version `{V}` with **tmux = PASS (real)** AND **in-process = PASS
  (real-or-faithful-synthetic)**; AND (iii) the originating issue is still OPEN
  at probe time (the probe is post-merge, pre-issue-close); — OR
- **(B) WAIVED:** the change trips the PRIMARY hook-infra signal but not the
  SECONDARY seam signal (no seam-dependent hook touched) AND a WAIVER row for
  version `{V}` is logged in `RUNBOOK_RUN_DATES.md`. A waiver is a LOGGED row,
  never a silent pass.

---

## §2 — Live-probe procedure (per mode)

Run the whole procedure once per mode. Record per-mode PASS/FAIL in
`RUNBOOK_RUN_DATES.md`.

### MODE MATRIX (run both cells)

- **tmux = MANDATORY real live-probe.** The dominant mode (N separate
  processes, N:1 session→team) and where seam bugs bite hardest. A synthetic
  substitute is NOT acceptable for the tmux cell.
- **in-process = real preferred; faithful-synthetic fallback acceptable** (1
  process per team). The synthetic fallback must drive a REAL on-disk
  precondition + a REAL triggering frame in a live process — equivalent to the
  integration test but executed in a running process, not pytest.
- **Discriminate the mode STRUCTURALLY, never by an asserted flag.** Read the
  teammate registry / team config and record the OBSERVED `session_id`s: tmux
  shows teammates in their OWN sessions (distinct `session_id`s); in-process
  shows `backendType == 'in-process'` / teammates sharing the lead process.
  **FORBIDDEN signal:** `leadSessionId == session_id` — it is TRUE in BOTH modes
  (the lead's own session always matches itself), so it does not discriminate.
  Keying on it is the exact mis-classification `923` corrected; the run row logs
  the observed `session_id`s, never a self-asserted mode label.

### Step A — ARM (reproducible precondition)

Drive a REAL on-disk `{PRECONDITION}`, then cross the firing threshold by
editing `{THRESHOLD_FIELD}` to a **concrete back-dated literal written directly
into the JSON** (no `Date.now()` / random / clock-relative arithmetic — a fixed
literal is reproducible and scripting-constraint-safe), or wait out the real
window. Record the literal you wrote.

> **Stateless-hook fallback.** If `{HOOK}` fires on a pure stdin event with NO
> disk state to arm (nothing on disk gates it), REPLACE Step A with: **construct
> the triggering stdin frame directly** and feed it to the real hook process.
> The rest of the procedure (TRIGGER via a genuine event, VERIFY surface +
> journal) is unchanged. Document which path (`disk-arm` vs `stdin-frame`) the
> run used.

### Step B — TRIGGER (a real platform event)

Cause `{TRIGGER_EVENT}` — the genuine platform action that fires `{HOOK}` on the
real process. **NEVER call the hook function directly**: a direct call is just
another unit test and bypasses the platform-dispatch surface this probe exists
to certify.

### Step C — VERIFY (the gate)

Assert ALL of:

- **(a) SURFACE.** The user-observable `{SURFACE}` appears, carrying the correct
  payload (the entity/id/age/corrective-action the hook is supposed to name).
- **(b) FORENSIC — exactly one.** **Exactly one** `{JOURNAL_EVENT}` event lands
  in the REAL `~/.claude/pact-sessions/{slug}/{session_id}/session-journal.jsonl`
  (`grep '"type":"{JOURNAL_EVENT}"'`). Record its timestamp.
- **(c) PERSISTENT + DEDUP.** Re-trigger while still armed: the surface RE-SHOWS
  (no surface dedup) but writes **NO 2nd** journal event (once per
  `{DEDUP_KEY}`).
- **(d) AUTO-CLEAR.** Resolve `{PRECONDITION}` (or otherwise clear the threshold):
  the next trigger shows the surface GONE (`suppressOutput`), and NO new journal
  event is written.

### Step D — COMPANION-hook probe (only if `{COMPANION}` exists)

Where a sibling hook shares the same `{SEAM}` (e.g. `teammate_idle` beside
`missed_wake_scan`), drive its precondition too and confirm IT now fires — the
same seam fix typically repairs both, and a companion that stays inert is a
partial fix. Omit this step if the hook has no seam-sharing sibling.

### Step E — CONTINGENCY tripwire (defense-in-depth)

Assert the design's `{ENV_INVARIANT}` holds during the probe (e.g.
`env | grep {ENV_INVARIANT}` from the hook context, or inspect the captured
stdin). **If the invariant is VIOLATED, HALT and escalate to the architect** —
a violated invariant means the design's mode/branch assumptions would diverge
for real sessions and the affected matrix cell needs re-derivation. Do not
record a PASS over a tripped tripwire.

**PASS (per mode)** = (a)+(b)+(c)+(d) hold, the companion probe (Step D, if
applicable) holds, and the Step E tripwire is clear. tmux PASS is MANDATORY;
in-process PASS may use the faithful-synthetic fallback.

---

## §3 — Per-caller / blast-radius audit

If the fix is a GLOBAL repair of a shared seam (`{SEAM}`) with multiple
production callers, enumerate every caller and rank by blast radius. Distinguish
the INERT callers (the surfacing hooks that NEVER fire pre-fix — the mandatory
live-probe targets) from callers the global fix merely REGRESSION-pins (already
correct paths whose own tests prevent a future re-break, not bug-discovery).
(See `923-missed-wake-live-probe.md` §3 for the worked table.)

| # | Caller (`file:line`) | Severity pre-fix (under Agent Teams) | Own non-mocked test? | Test shape |
|---|----------------------|--------------------------------------|----------------------|------------|
| 1 | `{caller}` | `{INERT / PARTIAL / MINOR}` | `{DONE / RECOMMENDED / OPTIONAL}` | `{e2e via real team dir → surface + 1 journal event}` |
| … | … | … | … | … |

**Framing note.** Name which callers are the live-probe targets (INERT
surfacers) vs which are regression-pinning coverage (already repaired by the
global fix). Staging the regression-pin tests as a fast follow-up is acceptable;
the INERT surfacers are NOT deferrable — they are the gate.

---

## §4 — Sections-passed denominator

Denominator = **2** (tmux live-probe; in-process live-probe). Each cell must
satisfy ALL of §2 Step C (a)+(b)+(c)+(d) + the Step D companion probe (if
applicable) + a clear Step E tripwire. Record per-mode PASS/FAIL in
`RUNBOOK_RUN_DATES.md`. If **tmux is RED**, do NOT declare the issue closed —
re-open and route back to the seam fix. If only in-process is feasible this
cycle, record tmux as `pending` and close only after the tmux probe lands. If
Step E trips, HALT + architect escalation rather than recording a partial PASS.
