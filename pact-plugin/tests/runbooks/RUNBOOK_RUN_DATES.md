# Runbook Run Dates

Tracks manual runbook execution dates. Each runbook in this directory has a
section below; append a new dated entry on each manual run.

Manual runbook execution is REQUIRED before each minor release tag and
informs the fallback-ladder trigger criteria evaluation across sessions
(the lazy-load fidelity dogfooding signal cannot be CI-tested; runbook
data is the substitute).

## 662-dispatch-gate.md

| Run date (UTC) | Operator | Plugin version | Sections passed | inline-mission mode | Notes / counter-test outcomes |
| -------------- | -------- | -------------- | --------------- | ------- | ------------------------------- |
| _pending — execute post-merge in fresh session_ | | 4.1.3 | /8 | warn (default) | matcher-fidelity (§1+§1.1) PASS/FAIL · Bash-touch bypass (§2+§2.1) PASS/FAIL · inline-mission advisory injection (§3) WARN-visible / WARN-dropped → flipped to deny · inline-mission shadow (§3.1) PASS/FAIL · inline-mission deny (§3.2) PASS/FAIL · sabotaged-import fail-closed (§4) PASS/FAIL. |

Sections-passed denominator is 8 per runbook §5 (§1, §1.1, §2, §2.1, §3, §3.1, §3.2, §4). The inline-mission mode column records whether the empirical observation kept the production default at `warn` or motivated a flip to `deny`. If a section fails, file a follow-up issue and link it in the Notes column.

## 691-bootstrap-secretary-dispatch.md

| Run date (UTC) | Operator | Plugin version | Sections passed | inline-mission mode | Notes / per-section observations |
| -------------- | -------- | -------------- | --------------- | ------------------- | -------------------------------- |
| _pending — execute post-merge in fresh session_ | | | /4 | warn (default) | gate-clean spawn (§1) PASS/FAIL · Task A/B structural shape (§2) PASS/FAIL · acceptance two-call pair (§3) PASS/FAIL · briefing-within-one-wake (§4) PASS/FAIL. |

Sections-passed denominator is 4 per runbook §5 (§1, §2, §3, §4). The inline-mission mode column records whether the production default (`warn`) was in effect during the run or whether the operator overrode to `deny` / `shadow`. If a section fails, file a follow-up issue per the severity tiers in runbook §5 and link it in the Notes column.

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
