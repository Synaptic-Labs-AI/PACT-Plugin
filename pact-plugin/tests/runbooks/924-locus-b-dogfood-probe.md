# Runbook: locus-b live-probe gate — DOGFOOD probe (the gate is its own first subject)

**Purpose.** The pytest layer (`test_live_probe_gate_dogfood.py`) certifies the
locus-b *decision logic* (real git diff + real plugin.json + real RUNBOOK reads).
It CANNOT certify that the PreToolUse(Bash) advisory actually FIRES and that its
`stderr`-on-exit-0 line is SURFACED to the operator in a real session. Green
tests are NECESSARY but NOT SUFFICIENT — this probe closes the residual
"does it surface in a real process?" gap. Run it POST-MERGE, before closing the
originating issue. Instantiates `tests/runbooks/live-probe-template.md` for the
locus-b hook (`hooks/live_probe_gate.py`).

**Gate PASS-iff (mirror into `RUNBOOK_RUN_DATES.md`).** Satisfied for plugin
version `4.4.13` iff a both-mode row exists with **tmux = PASS (real)** AND
**in-process = PASS (real-or-faithful-synthetic)** — logged BEFORE the issue is
closed, while the issue is still OPEN.

---

## §1 — Acceptance criterion

This dogfood probe PASSES iff §2 (a)+(b)+(c)+(d) hold in the mode under test. The
seam hooks this PR touches are the L2/L3 subjects; the locus-b advisory is the
meta-subject (it reminds the operator to run THIS probe).

## §2 — Per-mode procedure

**ARM** (reproducible, no `Date.now()`/random). On the merged PR's branch state:
confirm `RUNBOOK_RUN_DATES.md` has **NO** satisfied `4.4.13` row yet (the
unsatisfied precondition). The branch diff (`git diff origin/main...HEAD`) must
classify PRIMARY (it touches `hooks/`) — true for this PR (it adds
`hooks/live_probe_gate.py` + `hooks/shared/hook_infra_classifier.py`).

**TRIGGER** (real event — NEVER a direct function call): run the genuine merge
command in a real session:

```
gh pr merge <N> --squash    # PreToolUse(Bash) fires live_probe_gate.py
```

**VERIFY** (the gate):
- **(a) SURFACE:** the operator (agent) sees the `[live-probe-gate]` advisory
  via the PreToolUse `hookSpecificOutput.additionalContext` field (surfaced on
  stdout, exit 0) naming the version, the touched seam hooks, and the "log a
  live-probe … before closing the originating issue" instruction. (The prior
  stderr-on-exit-0 channel was confirmed NOT surfaced to the agent — the
  Finding-B fix moved the advisory to additionalContext, the channel that does.)
- **(b) NON-BLOCKING:** the command still runs (exit 0) — the advisory NEVER
  blocks the merge (WARN-not-BLOCK). The WARN path emits only the
  `hookSpecificOutput` field (no `suppressOutput`, which would hide it); the
  silent path still emits `{"suppressOutput": true}`.
- **(c) FALLBACK CHECK (the gate's own first-probe finding):** confirm the
  `additionalContext` line is actually surfaced by the platform for a PreToolUse
  exit-0 hook (the agent-visible reminder is the whole point of the gate).
- **(d) SILENCE-AFTER-PROBE:** run the REAL both-mode live-probe for the seam
  hooks this PR touches (per `live-probe-template.md`), log the `4.4.13`
  both-mode PASS row in `RUNBOOK_RUN_DATES.md`, then re-run `gh pr merge` /
  `gh pr close` → the advisory is now SILENT (`_has_satisfied_row` → True).

## §3 — Per-subject audit (what this PR touches)

| Subject | Tier | Probe |
|---------|------|-------|
| `live_probe_gate` (locus-b advisory) | meta | THIS dogfood probe (a)–(d) |
| `hook_infra_classifier` (pure SSOT) | L1/L2 | unit + `test_live_probe_gate_structure.py` |
| any L3 seam hook the PR also touched | L3 | `live-probe-template.md` both-mode probe |

## §4 — Sections-passed denominator

Denominator = **2** (tmux dogfood probe; in-process dogfood probe). Record
per-mode in `RUNBOOK_RUN_DATES.md`.

## MODE MATRIX

- **tmux = MANDATORY real.** **in-process = real-or-faithful-synthetic.**
- Mode discriminated **STRUCTURALLY** — distinct teammate `session_id`s /
  `backendType=='in-process'`. **FORBID `leadSessionId == session_id` as a mode
  signal** (true in BOTH modes → non-discriminating). The row logs the
  **observed** session_ids, never a self-asserted mode label.

## CONTINGENCY

- The locus-b advisory fail-SAFES to silent-allow on every resolution failure
  (not the dev repo, unreadable version, git/classifier error). A FALSE WARN in
  a consumer (hook-less) project would be a regression — if observed, HALT and
  escalate: the `_plugin_marker` identity guard has failed open.
- Issue-close is enforced PROCEDURALLY (the project pin + the no-`Closes-#N`
  sub-rule), NOT by this hook: on `main` post-merge the `base...HEAD` diff is
  EMPTY, so a runtime `gh issue close` arm would silently no-op (a false
  "checked & clear"). Close the issue MANUALLY after the `4.4.13` row is logged.

## ACTUAL-not-claimed coverage (the recursive guard)

This probe checks the **ACTUAL** hooks/ diff + the **ACTUAL** RUNBOOK row, never
a claimed/asserted coverage flag. A green test suite does NOT satisfy the gate —
only a logged both-mode PASS row does. That is the whole point: the gate that
prevents inert-ship must not itself rely on a "checked & clear" signal that was
never actually checked.
