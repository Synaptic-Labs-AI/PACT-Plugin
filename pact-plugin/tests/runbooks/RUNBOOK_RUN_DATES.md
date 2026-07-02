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

**Row schema (human-readable run-log convention).** Each probe section records,
per run: the plugin **version** (3rd column), a **per-mode verdict** (4th column)
written `tmux PASS|FAIL N/N · in-process PASS|FAIL N/N` (both modes in ONE row;
`WAIVED` for a PRIMARY-not-SECONDARY change; `n/a` + a `_deferred — <mode> …`
label for a mode that genuinely cannot run), and discriminator evidence. A
both-modes PASS with complete `N/N` counts is what certifies a probe; the older
single-mode sections (923-missed-wake `tmux 6/6 — PASS` + `in-process 6/6 — PASS`;
926-config-dir `in-process 4/4 — PASS` + a deferred tmux row) certify across
their two rows. This is now a documentation convention for human reviewers — the
live_probe_gate row-parser that formerly consumed it was removed per #997.

## locus-b live-probe — RETIRED

The locus-b live-probe (`924-locus-b-dogfood-probe.md`) is retired: the `live_probe_gate` advisory hook was removed from the shipped plugin per #997 (maintainer-internal tooling, zero consumer value); its runbook + acceptance rows go with it.

## 926-config-dir-live-probe.md

| Run date (UTC) | Operator | Plugin version | Sections passed | inline-mission mode | Notes / per-mode observations |
| -------------- | -------- | -------------- | --------------- | ------------------- | -------------------------------- |
| 2026-06-09 | michael-wojcik | 4.4.13 | in-process 4/4 — PASS | n/a | (a) PASS — team `pact-76403890` created in `$CLAUDE_CONFIG_DIR/teams/` and `$CLAUDE_CONFIG_DIR/tasks/`, absent from `~/.claude/teams/` and `~/.claude/tasks/` · (b) PASS — preparer dispatched with Task A/B teachback gate, no dispatch_gate blockage; teachback accepted, HANDOFF delivered · (c) PASS — agent body loaded (8758 bytes from `~/.claude-kimi/plugins/cache/pact-plugin/PACT/4.4.13/agents/pact-preparer.md`), `@~/.claude-kimi/protocols/pact-plugin/pact-phase-transitions.md` resolved non-empty (3245 bytes via symlink) · (d) PASS — session dir `/Users/mj/.claude-kimi/pact-sessions/PACT-prompt/76403890-4caf-454b-b134-a9dfb18e8007` under `$CLAUDE_CONFIG_DIR`, readable, bootstrap-complete marker written. |
| _deferred — tmux mode under non-default CLAUDE_CONFIG_DIR_ | | 4.4.13 | n/a | n/a | **OPERATIONAL CONSTRAINT**: tmux teammateMode is only used with default Claude Code config (no custom `CLAUDE_CONFIG_DIR`). Kimi-wrapped sessions run in-process only because the tmux pane launcher invokes default `claude` from `~/.zshrc`, not the Kimi function — `CLAUDE_CONFIG_DIR` would not propagate. The L2 both-modes pair (`test_config_dir_comprehensive.py`) already validates the resolver's mode-independence at code level; the in-process live-probe (4/4 PASS) confirms runtime correctness for this operational configuration. If the tmux launcher is ever unified with the Kimi wrapper, re-run this probe. |

Sections-passed denominator is 4 per mode per runbook §3 (criteria a/b/c/d). This is the **POST-merge runtime-confirmation gate** (#861 class) — a green CI suite (L1+L2, 8246 passed) does NOT close #926; the live run under a non-default `CLAUDE_CONFIG_DIR` is mandatory. The resolver is mode-independent (keys on the env var, not session topology — proven by the L2 both-modes pair), so a divergence between in-process and tmux is itself a finding. On any FAIL, capture resolved-vs-expected path and route back to the resolver / call-site.

## 994-fork-session-context-fate.md

| Run date (UTC) | Operator | Plugin version | Verdict | Raw evidence (NEW/OLD field values) |
| -------------- | -------- | -------------- | ------- | ----------------------------------- |
| 2026-06-22 | self-run (disposable) | 4.4.36 | **CASE 1 (FRESH)** — case 3 refuted; durable write-back has no scenario | SEED-OLD ctx FIELD = OLD; fork WITHOUT `--agent` ctx FIELD = own/NEW, OLD-in-journal=0; fork WITH `--agent` ctx FIELD = own/NEW, OLD-in-journal=0. NON-VACUITY control (`cp -R OLD->SIMNEW`) ctx FIELD = OLD, OLD-in-journal=3 → fired CASE 3 (detector non-vacuous). `--resume` INHERITS agent_type (the no-`--agent` fork persisted a fresh context as a lead). Leadness-robust: verdict keys on un-overwritten OLD journal events, not the overwritable context field. Safety: live CLAUDE.md byte-unchanged (sha matched baseline), registry intact, zero leftovers. |

This is a single-verdict empirical probe (not a per-mode live-probe), so the denominator is the one VERDICT line; tmux-vs-in-process does not apply (the context write is a lead-frame property, mode-independent). The verdict drives the #994 PR-2 re-scope decision: CASE 1/2 → the durable write-back has no scenario (re-diagnose); CASE 3 → unblock + run the §4 timing probe. A wrong verdict mis-directs the decision, so the row records the raw field/journal evidence + the non-vacuity result, not just the label.

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

## merge-guard-auth-symmetry-live-probe (#1031 false-REJECT + #1032 false-AUTHORIZE)

| Run date (UTC) | Operator | Plugin version | Verdict | Harness-independence evidence | Notes (real cases; deny paths; stub gate) |
| -------------- | -------- | -------------- | ------- | ----------------------------- | ----------------------------------------- |
| 2026-06-26 | michael-wojcik | 4.4.43 | #1031 PASS · #1032 PASS · stub PASS | DUAL independent harnesses — security-engineer (primary) + test-engineer (clean-room: re-derived envelopes from source, did NOT read peer); verdicts CONVERGE; test-engineer subprocess==in-process 10/10 + R-B positive control proves mint capability | Post-install probe of the INSTALLED v4.4.43 hooks (not pytest). Mode-independent (file-on-disk token store; session scoping intentionally INERT → every deny on the (op,target) axis alone). #1031: R-A minted pr 1029 not prose distractor 1028; R-B minted pr 1034 from clicked-option command only (padded question ignored); R-C kept full colon-bearing branch origin:feature → all PRE allow exit0 + consume. #1032: A1/A1b/A2/A3/A4/A5 NO-MINT (step-3b option-anchoring / no-options fail-closed) → DENY-NOTOKEN exit2; A6 token-exists-mismatch (valid {merge,1029} token vs force-push exec, _token_matches_command False) → DENY-MISMATCH exit2, token unchanged. Stub gate AST-confirmed + full-source-read: NO unconditional always-allow in check_merge_authorization. No HALT. Gated closure (#924 discipline) satisfied → #1031/#1032 closed. |

## merge-guard-1042-privileged-flags-live-probe (#1042 privileged flags ride past auth binding)

| Run date (UTC) | Operator | Plugin version | Verdict | Harness-independence evidence | Notes (real cases; deny paths; non-vacuity) |
| -------------- | -------- | -------------- | ------- | ----------------------------- | ------------------------------------------- |
| 2026-06-27 | michael-wojcik | 4.4.44 | C1 AUTHORIZE · C2–C5 REFUSE (both arms) · dual-independent CONVERGE PASS | DUAL independent harnesses under an independence guard — security-engineer (primary; crafted-stdin through the REAL hooks.json pre/post entry points, isolated `CLAUDE_CONFIG_DIR`, zero `~/.claude` contamination) + test-engineer (independent faithfulness; deterministic-token READ arm + genuine post-hook MINT seam `minted=True`, isolated `te_indep/` dir; observed only peer FILENAMES via one `ls`, never contents). Each LOCKED its verdict BEFORE cross-reading; reconciliation routed through the lead; both honesty-disclosed sources. Verdicts CONVERGE; the two probes surfaced DIFFERENT peripheral over-blocks (genuine-independence signature). | Post-install probe of the INSTALLED v4.4.44 hooks (== merge commit `b01639d5`), not pytest. Mode-independent (file-on-disk token store; session scoping intentionally INERT → deny keys on op+target+`bound_flags` alone). Cases (read-arm `pre` + mint→read seam `post→pre`): C1 faithful bare merge → AUTHORIZE (+token consumed); C2 `--admin` / C3 `-R other/repo` / C4 `--no-verify` / C5 dropped `--match-head-commit <sha>` → all REFUSE with reason TOKEN_MISMATCH (op+target IDENTICAL, only `bound_flags` differ). Non-vacuity proven 3 ways per case: in-process denylist-pop FLIPs REFUSE→AUTHORIZE; faithful-with-flag (C2c/C3c/C4c/C5c) AUTHORIZEs; ALLOW path consumes the token (AUTHORIZE branch genuinely reached — rules out always-deny/always-allow/silent-no-op/parse-error). Completeness: 11/11 CLI spelling variants REFUSE incl. the `-dR` cluster end-to-end DENY (the R4 redirect-under-block class) + `git --no-veri` abbreviation expansion. Residuals (NONE an under-block): backtick-into-value capture = over-block-only usability; shell-quote/op-token obfuscation = control's pre-existing literal-string boundary (accepted in #1042 review); `--no-verify` APPROVAL refused by post-hook decline-veto = independent over-block, decline-veto false-positive class (#1049), not a #1042 gap. No HALT. Gated closure (#924 discipline) satisfied → #1042 closed. |

## merge-guard-1058-gh-comment-carrier-strip-live-probe (#1058 narrow gh-comment carrier-strip over-block fix)

| Run date (UTC) | Operator | Plugin version | Verdict | Harness-independence evidence | Notes (before/after flip; preserved-block non-vacuity) |
| -------------- | -------- | -------------- | ------- | ----------------------------- | ------------------------------------------------------ |
| 2026-06-28 | michael-wojcik | 4.4.45 | P1/P2 over-block FIX flips DENY→ALLOW · N1/N2/N3 preserved-block DENY→DENY · 5/5 PASS | SINGLE-harness empirical before/after (lead self-run): the SAME crafted PreToolUse stdin run through BOTH the pre-#1058 hook (`7f98de2f^` = `bee5da82`) AND the installed v4.4.45 hook (`== 7f98de2f`; byte-identical to source confirmed via `diff`); isolated `CLAUDE_CONFIG_DIR`, zero `~/.claude` contamination. Single-harness is adequate here because this is an OVER-BLOCK fix — the verdict confirms ALLOW (the SAFE direction); the never-under-block direction is not what is exercised here (it stays covered by N1/N2/N3 + the #1042 / auth-symmetry probes). | Post-install probe of the INSTALLED v4.4.45 `merge_guard_pre.py` (not pytest). #1058 added `comment` to the carrier verb alternation (`issue create\|edit\|comment`, `pr create\|comment`). FIX: P1 `gh issue comment <N> --body "...git branch -D <name>..."` and P2 `gh pr comment <N> --body "...rm -rf .../git push --force..."` → pre-#1058 DENY (comment body NOT stripped → DANGEROUS_PATTERNS fires on the quoted literal), v4.4.45 ALLOW (body stripped). NON-VACUITY: N1 bare `git branch -D <name>` → DENY both (the literal is a live dangerous pattern, so the ALLOW is the carrier-strip, not a benign literal); N2 `gh issue comment ... && git branch -D real` → DENY both (the executing tail falls OUTSIDE the quote-aware carrier span — INV-D2 boundary intact); N3 `gh pr close <N> --delete-branch` → DENY both (`close` is absent from the alternation by construction). No HALT. #1058/v4.4.45 already shipped; this row satisfies the deferred post-install live-probe gate for the over-block fix. |

## merge-guard-1063-branch-protection-live-probe (#1063 branch-protection API mutation gating; systemic method-delimiter under-block → #1079)

| Run date (UTC) | Operator | Plugin version | Verdict | Harness-independence evidence | Notes (charter legs; the under-block; live confirmation; disposition) |
| -------------- | -------- | -------------- | ------- | ----------------------------- | -------------------------------------------------------------------- |
| 2026-07-01 | michael-wojcik | 4.4.50 | 4 charter legs GREEN (dual-independent) · completeness GAP found (method-delimiter under-block, systemic/pre-existing → filed #1079) · #1063 CLOSED on charter | DUAL independent harnesses. Harness#1 (lead self-run): 26/26 hermetic subprocess checks against the INSTALLED v4.4.50 binaries (isolated `CLAUDE_CONFIG_DIR`, nonexistent sandbox targets, zero `~/.claude` contamination) + FULLY-LIVE end-to-end in the running session — read-floor DENY (real Bash dispatch), mint round-trip (real AskUserQuestion approval → token minted → byte-identical command AUTHORIZED → 404), negative POST ungated → 404. Harness#2 (blind pact-security-engineer, clean-room): authored its OWN 2-harness suite (39-case read-floor hunt + 8-form mint sweep) WITHOUT reading harness#1 or its results; LOCKED verdict before any cross-read; disclosed only general-methodology memory (no prior #1063 record). Genuine-independence signature: harness#1 tested only `-X <space> METHOD` and MISSED the delimiter class; harness#2 CAUGHT it; the lead then independently reproduced + live-confirmed it. Reconciliation routed through the lead. | Charter legs (both harnesses): (a) read-floor DENY for every FAITHFUL charter-IN form (gh api/curl `-X`/`--method`/`--request` <space> DELETE\|PUT\|PATCH; wget `--method=`; incl. subresources required_status_checks/required_pull_request_reviews/enforce_admins/restrictions, trailing `?query`, `%2F` branch); (b) mint symmetry — approve→token→byte-identical ALLOW, token binds `protected_branch` (main-token does NOT authorize develop), NO #1064 gated-but-unmintable; (c) host-agnostic — enterprise `--hostname` + `api/v3` URL + `gh -R` global-flag all DENY; (d) negatives UNGATED — `-X POST` (strengthen), repo/release/api-repo-delete, httpie (documented-excluded). Non-vacuity both directions (DENIES `gh pr merge`/`curl -X DELETE git/refs`; ALLOWS `git status`). **THE GAP:** read-floor `FLAG\s+METHOD` (space-required) under-approximates the classifier's match-anywhere `\b(DELETE\|PUT\|PATCH)\b`, so `gh api --method=DELETE`, `curl -XDELETE`/`-X 'DELETE'`/`--request=DELETE`, `wget --method DELETE` on `branches/*/protection` run UNGATED (`is_dangerous=False`; `detect_op=branch-protection`-or-`None`). NOT a mint bug (mint is `is_dangerous`-write-gated → also refuses). SYSTEMIC/PRE-EXISTING: reproduced on the shipped `git/refs` + `pulls/N/merge` arms too (the #1063 arm faithfully mirrors its charter-specified git/refs sibling → inherits, not introduces). LIVE-CONFIRMED: `gh api --method=DELETE .../branches/main/protection` reached GitHub's delete-branch-protection endpoint unblocked (404 = nonexistent sandbox). DISPOSITION (maintainer S5 call): #1063 closed on its charter (legs GREEN + faithful parity); the systemic delimiter under-block filed HIGH-priority as #1079 covering branch-protection + git/refs + merge arms (contents flagged for separate verification). No HALT (defense-in-depth, pre-existing, no regression). |

## merge-guard-over-block-batch-live-probe (#1064/#1077/#1078 over-block batch)

| Run date (UTC) | Operator | Plugin version | Verdict | Harness-independence evidence | Notes (per-probe outcomes; adequacy rationale; identity evidence) |
| -------------- | -------- | -------------- | ------- | ----------------------------- | ----------------------------------------------------------------- |
| 2026-07-02 | michael-wojcik | 4.4.50 installed (PREDATES batch) — probe ran the STAGED batch hooks @ `e9356ae1` (branch fix/merge-guard-over-block-batch; ships as the next PATCH) | **17/17 PASS, BOTH SIDES** — all three fixed forms flip DENY→ALLOW, every preserved-block canary holds, zero runtime-vs-unit divergence · no HALT | SINGLE-harness empirical before/after (test-engineer run; lead-ruled single-harness): the SAME crafted hook stdin through the PRE-FIX hooks staged from the batch parent `80cb08fe` AND the POST-FIX hooks staged from batch HEAD `e9356ae1`, as REAL SUBPROCESSES of the shipped entry points (`merge_guard_pre.py`/`merge_guard_post.py` mains, matching the hooks.json `python3 <hook>.py` shape) under the macOS system python3 3.9.6 (the runtime floor); per-probe isolated `CLAUDE_CONFIG_DIR` + `HOME` (TOKEN_DIR derives from `$CLAUDE_CONFIG_DIR` at import — verified env-redirectable, NO fidelity fallback needed), zero `~/.claude` contamination; dangerous literals confined to script files. Single-harness is adequate because all three fixes are OVER-BLOCK removals — ALLOW/mintable is the direction needing live proof; never-under-block stays covered by the N-row canaries + the full suite + the independent blind adversarial review (which separately converged SAFE-TO-MERGE with its own probes). Same-engineer note: the runner also authored the unit/envelope certifications; independence comes from the blind review, not this run. Identity evidence: post-fix staged copy `diff -r` byte-identical to the worktree hooks at `e9356ae1` (excluding untracked `__pycache__`); pre-fix copy `git archive 80cb08fe`. | FIX rows (pre→post): P1a mint bare-lease `git push --force-with-lease origin main` no-token→token{push-to-main, target=main, bound_flags=[--force-with-lease]}; P1b byte-identical exec DENY→ALLOW; P1c mint+exec for the `=main:abc123` spelling no-token/DENY→token/ALLOW (canonical bare bind); P2a httpie DELETE git/refs + P2b httpie PUT merge DENY→ALLOW (runs free, no token); P3a `push origin feature && rm -rf` + P3b the formerly-PERMANENT `git push && rm -rf` + P3c close sibling `gh pr close 42 && git branch -d temp` DENY→ALLOW; R17 https-alias addendum (`https DELETE …git/refs…`) DENY→ALLOW. PRESERVED-DENY canaries (both sides): N1 force-push, N2 plain push-to-main (no token), N3 wget DELETE git/refs, N4 `git branch -Df`, N5 the #1082 residual `push … && rm -f` (still gated by design, out-of-batch). PRESERVED-ALLOW: A1 lease-to-topic, A2 httpie GET. Every runtime verdict matches its TEST-phase unit/envelope certification — the runtime-path-vs-unit-path unobservable is closed. No HALT. Satisfies the §8 pre-merge gate; gated closure of #1064/#1077/#1078 rides the batch merge per the no-auto-close discipline. |

## merge-guard-leg-isolation-completion-live-probe (#1082 literal per-leg + #1083 leg-bounded bind window; N5 supersede)

| Run date (UTC) | Operator | Plugin version | Verdict | Harness-independence evidence | Notes (per-probe outcomes; N5 supersede; the emergent close row) |
| -------------- | -------- | -------------- | ------- | ----------------------------- | ---------------------------------------------------------------- |
| 2026-07-02 | michael-wojcik | 4.4.51 (rides; NO bump) — probe ran STAGED hooks: PRE-FIX parent `baef503b` (before C-i) vs POST-FIX `99d39baa` (branch fix/merge-guard-over-block-batch) | **14/14 PASS, BOTH SIDES** — #1082 cross-leg literal over-block CURED (incl. the permanent form) + same-leg preserved; #1083 all four laundering channels CLOSED end-to-end (mint→token→escalated-exec) + faithful members AUTHORIZE; ZERO runtime-vs-re-cert divergence · no HALT | SINGLE-harness empirical before/after (test-engineer run): the SAME crafted hook stdin through the REAL `merge_guard_pre.py`/`merge_guard_post.py` mains as SUBPROCESSES (matching hooks.json `python3 <hook>.py`) under macOS system python3 3.9.6 (runtime floor); per-probe isolated `CLAUDE_CONFIG_DIR` + `HOME` (TOKEN_DIR derives from `$CLAUDE_CONFIG_DIR` at import — env-redirectable, no fidelity fallback), zero `~/.claude` contamination; dangerous literals in script files. SAME-ENGINEER probe (also authored the unit/re-cert certifications) — INDEPENDENCE is attributed to the sec-reviewer2 blind re-review (Task #38, SAFE-TO-MERGE, ~60 real-seam rows each attributed vs a baef503b baseline; independently corroborated the laundering closures + #1087 pre-existing). Identity: post-fix staged copy `diff -r` byte-identical to the worktree hooks at `99d39baa` (excl. untracked `__pycache__`); pre-fix `git archive baef503b` verified to LACK the delta symbols (`_slice_stripped_legs`/`_FORCE_PUSH_LITERAL_ARMS`/`_single_detectable_leg`). | READ-FLOOR (#1082, pre→post): `git push origin feature && rm -f stale.txt` DENY→ALLOW; the PERMANENT `git push && rm -f x.txt` DENY→ALLOW; same-leg `cd /repo && git push --force origin main` DENY→DENY (no new under-block). LAUNDERING-CLOSED (#1083, end-to-end mint→token→ESCALATED exec, pre AUTHORIZE→post REFUSE): close fwd `gh pr close 42 && echo --delete-branch`→`gh pr close 42 --delete-branch`; close REVERSED `echo --delete-branch && gh pr close 42`; push-lease `git push origin main && echo --force-with-lease`→`git push --force-with-lease origin main`; merge-admin `gh pr merge 42 && echo --admin`→`... --admin`; force-noverify `git push --force origin main && echo --no-verify`→`... --no-verify`. FAITHFUL ROUND-TRIP (byte-identical member, pre→post): push-lease / merge-admin / force-noverify DENY→ALLOW (the mint/read bind-asymmetry over-block the window cures); close fwd/rev ALLOW→ALLOW — the EMERGENT-danger member was pre-fix mint/read-SYMMETRIC (both surfaces fall back to whole-command with 0 individually-dangerous legs → both bind `[--delete-branch]`), so its byte-identical already AUTHORIZED pre-fix; the fix's job for close is to CLOSE THE ESCALATED LAUNDERING (its L-row above) while PRESERVING the faithful authorize (this matches the coder's `test_close_emergent_member_round_trips_both_shapes` + `test_close_laundering_channel_closed`, NOT a divergence). **N5 SUPERSEDE**: the #1064/#1077/#1078 batch live-probe row (`baef503b`) has an N5 preserved-DENY row for `git push origin feature && rm -f stale.txt` (DENY→DENY) — that is HISTORICAL: post-C-ii this form runs free (the D1082-cured row above, DENY→ALLOW). The append-only prior row is untouched; this row records the supersede. **NOT probed** (per dispatch): #1087 multi-close ambiguity laundering — TRACKED, PRE-EXISTING (byte-identical both trees), NOT fixed in this delta; deliberately excluded from the cured set. No HALT. Satisfies the fix-level pre-merge gate for #1082/#1083; gated closure of #1082/#1083 rides the batch merge per the no-auto-close discipline. |
