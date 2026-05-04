# Runbook Run Dates

Tracks manual runbook execution dates. Each runbook in this directory has a
section below; append a new dated entry on each manual run.

Manual runbook execution is REQUIRED before each minor release tag and
informs the fallback-ladder trigger criteria evaluation across sessions
(the lazy-load fidelity dogfooding signal cannot be CI-tested; runbook
data is the substitute).

## v4.0.0-launch-and-isolation.md

| Run date (UTC) | Operator | Plugin version | Sections passed | Notes / fallback-ladder signals |
| -------------- | -------- | -------------- | --------------- | -------------------------------- |
| _(not yet run)_ | _-_ | _-_ | _-_ | Initial run pending v4.0.0 release tag |

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
