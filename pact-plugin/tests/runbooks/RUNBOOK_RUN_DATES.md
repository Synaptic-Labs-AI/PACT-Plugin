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

## Live-probe-template instances — per-mode gate-record row schema

Any runbook instantiated from `live-probe-template.md` (the missed-wake runbook
`923-missed-wake-live-probe.md` above is the first worked instance) records its
runs with the **per-mode** schema below. This convention is **append-only** —
add a new row per run; never rewrite a prior row (the existing rows above keep
their original column shape on purpose). Specifics MUST be falsifiable: log the
OBSERVED `session_id`s (so the mode claim is checkable, not self-asserted), the
real journal-event timestamp, and the stale age — a fabricated PASS should be
detectable from the row.

**Probe row** — ONE record covers BOTH mode cells in a SINGLE `|`-delimited row.
The per-mode verdict column carries the explicit `PASS|FAIL` token PER MODE so the
verdict is machine-recognizable, not buried in prose (see the parser-coupling note
below):

```
| Run date (UTC) | Operator | Plugin version | Per-mode verdict (tmux PASS|FAIL N/N · in-process PASS|FAIL N/N) | Mode-discriminator evidence (session_ids observed) | Notes (real vs synthetic; journal event ts; stale age; tripwire state) |
```

| Run date (UTC) | Operator | Plugin version | Per-mode verdict (tmux PASS\|FAIL N/N · in-process PASS\|FAIL N/N) | Mode-discriminator evidence (session_ids observed) | Notes (real vs synthetic; journal ts; stale age; tripwire) |
| -------------- | -------- | -------------- | ----------------------------------------------------------------- | -------------------------------------------------- | ---------------------------------------------------------- |
| _pending — execute post-merge per `live-probe-template.md` §2_ | | | tmux PENDING, in-process PENDING | lead `{session_id}`; teammates `{session_id…}` (distinct ⇒ tmux) | real vs synthetic per cell · `{JOURNAL_EVENT}` ts · stale age · §2 Step E tripwire CLEAR/TRIPPED |
| _satisfied-row form_ → | `<operator>` | `<version V>` | tmux PASS 2/2 · in-process PASS 2/2 | lead `<sid>`; teammates `<sid…>` (distinct ⇒ tmux) | tmux real, in-process synthetic; `<journal ts>`; `<stale age>`; tripwire CLEAR |

**Waiver row variant** — for a change that trips the PRIMARY hook-infra signal
but NOT the SECONDARY seam signal (touches `hooks/` but changes no
seam-dependent behavior, e.g. a pure `hooks/shared/` helper edit). The waiver is
a LOGGED row, never a silent pass — it is the gate's non-vacuity-on-the-quiet-side
evidence:

```
| <date UTC> | <operator> | <version> | WAIVED | n/a | hooks/ touched, no seam-dependent behavior changed → live-probe waived; <one-line reason> |
```

**tmux is the MANDATORY real cell.** If tmux is RED or missing, the issue is NOT
closed — record tmux as `pending` and close only after the real tmux probe
lands. A PRIMARY-but-not-SECONDARY change is the only path that closes WITHOUT a
both-modes PASS, and only via an explicit WAIVED row.

**Parser-coupling note (keep these tokens, single row).** The locus-b freshness
advisory (`live_probe_gate.py`) decides WARN-vs-silent by scanning these rows for
a *satisfied* row at the current plugin version. Its match keys on tokens that
MUST stay in ONE `|`-delimited row:

- the plugin **version** in the **"Plugin version" column** — COLUMN-ANCHORED to
  the 3rd cell, NOT matched anywhere-in-line. So a *different*-version row that
  merely mentions this version in its Notes prose does NOT satisfy the gate.
  Keep `Plugin version` as the 3rd column.
- a **PER-MODE** verdict in the **"Per-mode verdict" column** (the 4th cell):
  the parser reads `tmux <verdict>` AND `in-process <verdict>` SEPARATELY and
  requires BOTH to be a genuine `PASS`/`PASSED` **immediately followed by the
  trailing count** (`tmux PASS 2/2`). A row that mixes a real FAIL with a real
  PASS — `tmux FAIL N/N · in-process PASS N/N` — does **NOT** satisfy (per-mode
  parsing closes that dangerous false-satisfy).
  OR `WAIVED` → satisfied iff the change is PRIMARY-not-SECONDARY.

The per-mode verdict is matched case-sensitively and accepts either **`PASS`** or
**`PASSED`** per mode — but the COUNT IS REQUIRED: the verdict must be written
`PASS N/N` (a digit follows the verdict, e.g. `PASS 2/2`). This single rule
rejects EVERY unfilled-placeholder form — `PASS/FAIL`, `PASS|FAIL`, `PASS, FAIL`,
`PASS FAIL`, and a bare `PASS` with no count — because each has a non-digit after
`PASS`; it also rejects `bypass`/`BYPASSED`/lowercase. That is what stops a
pending/template row from silently satisfying the gate. So: keep both modes in a
SINGLE row with the per-mode `tmux PASS|FAIL N/N · in-process PASS|FAIL N/N`
shape (do not split tmux/in-process across rows), keep `Plugin version` as the
3rd column and the per-mode verdict as the 4th, and **always write each mode's
verdict WITH its count** — `PASS 2/2` / `FAIL 0/2` (or `WAIVED`); a count-less
`PASS` will NOT satisfy. (The older single-mode `Sections passed` sections —
923-missed-wake, 926-config-dir — predate the per-mode cell and are still
matched by the legacy token-presence path; don't retrofit them.) If a future
layout moves the version column, changes the per-mode verdict cell, or renames
the verdict tokens, update `live_probe_gate.py`'s row parser to match.

## 926-config-dir-live-probe.md

| Run date (UTC) | Operator | Plugin version | Sections passed | inline-mission mode | Notes / per-mode observations |
| -------------- | -------- | -------------- | --------------- | ------------------- | -------------------------------- |
| 2026-06-09 | michael-wojcik | 4.4.13 | in-process 4/4 — PASS | n/a | (a) PASS — team `pact-76403890` created in `$CLAUDE_CONFIG_DIR/teams/` and `$CLAUDE_CONFIG_DIR/tasks/`, absent from `~/.claude/teams/` and `~/.claude/tasks/` · (b) PASS — preparer dispatched with Task A/B teachback gate, no dispatch_gate blockage; teachback accepted, HANDOFF delivered · (c) PASS — agent body loaded (8758 bytes from `~/.claude-kimi/plugins/cache/pact-plugin/PACT/4.4.13/agents/pact-preparer.md`), `@~/.claude-kimi/protocols/pact-plugin/pact-phase-transitions.md` resolved non-empty (3245 bytes via symlink) · (d) PASS — session dir `/Users/mj/.claude-kimi/pact-sessions/PACT-prompt/76403890-4caf-454b-b134-a9dfb18e8007` under `$CLAUDE_CONFIG_DIR`, readable, bootstrap-complete marker written. |
| _deferred — tmux mode under non-default CLAUDE_CONFIG_DIR_ | | 4.4.13 | n/a | n/a | **OPERATIONAL CONSTRAINT**: tmux teammateMode is only used with default Claude Code config (no custom `CLAUDE_CONFIG_DIR`). Kimi-wrapped sessions run in-process only because the tmux pane launcher invokes default `claude` from `~/.zshrc`, not the Kimi function — `CLAUDE_CONFIG_DIR` would not propagate. The L2 both-modes pair (`test_config_dir_comprehensive.py`) already validates the resolver's mode-independence at code level; the in-process live-probe (4/4 PASS) confirms runtime correctness for this operational configuration. If the tmux launcher is ever unified with the Kimi wrapper, re-run this probe. |

Sections-passed denominator is 4 per mode per runbook §3 (criteria a/b/c/d). This is the **POST-merge runtime-confirmation gate** (#861 class) — a green CI suite (L1+L2, 8246 passed) does NOT close #926; the live run under a non-default `CLAUDE_CONFIG_DIR` is mandatory. The resolver is mode-independent (keys on the env var, not session topology — proven by the L2 both-modes pair), so a divergence between in-process and tmux is itself a finding. On any FAIL, capture resolved-vs-expected path and route back to the resolver / call-site.

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
