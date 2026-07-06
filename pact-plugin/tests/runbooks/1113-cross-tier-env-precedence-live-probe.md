# Runbook: Cross-tier `env`-block precedence Build-Time Empirical Confirmation

**Purpose:** confirm which `settings.json` tier wins when the same `PACT_*` var is
set in more than one tier (user vs project vs local). This is **deliberately a
documented empirical build-time check, NOT an automated pytest gate** — and the
reason is load-bearing:

> `pact_config` is **`os.environ`-blind**. Claude Code merges the settings tiers
> (managed → CLI args → `env` vars → settings files [local → project → user] →
> defaults) into `os.environ` **before** the resolver ever runs. So by the time
> `get_bool`/`get_enum` reads a var, the tier contest is already decided by CC.
> A pytest "precedence gate" could only set `os.environ[X]` and re-assert
> `parse(X)` — it would test the resolver, not CC's merge, and be **vacuous for
> precedence**. Building that gate would be theater.

The resolver's own contract (os.environ → typed value) IS automated and green in
`test_pact_config.py`. What remains is a CC-platform behavior, confirmed here by
observation, and re-verified at build time against the current CC version (the
plan Open-Questions residual; CC-2.1.201-anchored at design time).

## §1 — Acceptance criterion

Confirmed when a real session demonstrates the documented precedence for a
`PACT_*` var set at two tiers simultaneously, as reflected in the injected
`## PACT Runtime Config` block (the observable resolver output).

## §2 — Procedure

Pick `PACT_PR_GREEDY_FIX` (bool, LLM-consumed → visible in the injected block).

### Step A — user vs project
1. Set `{"env": {"PACT_PR_GREEDY_FIX": "0"}}` in the **user** settings
   (`$CLAUDE_CONFIG_DIR/settings.json`, default `~/.claude/settings.json`).
2. Set `{"env": {"PACT_PR_GREEDY_FIX": "1"}}` in the **project** settings
   (`.claude/settings.json` in the repo root).
3. Launch a real session in that repo. Inspect the SessionStart
   `additionalContext` `## PACT Runtime Config` block.
4. **Record** which value won: `- PR greedy-fix: ON` (project won) or `OFF`
   (user won). CC docs place project above user; confirm empirically.

### Step B — local vs project
Repeat with `.claude/settings.local.json` (local) = `1` and
`.claude/settings.json` (project) = `0`. Record which won (CC docs place local
above project).

### Step C — shell-vs-`env`-block gotcha (design §3a gotcha 1)
With `PACT_PR_GREEDY_FIX` pinned in the user `env` block to `0`, launch with a
one-shot shell override `PACT_PR_GREEDY_FIX=1 claude …`. **Record** whether the
`env`-block pin wins (block shows `OFF`) — the design documents that a
`settings.json` `env` pin overrides a same-named shell var, so a one-shot does
NOT override a pinned value. This is the gotcha the config reference warns about.

## §3 — Record

Append a dated row to `RUNBOOK_RUN_DATES.md` under
`## 1113-cross-tier-env-precedence-live-probe.md`. Sections-passed denominator =
**3** (Step A user/project, Step B local/project, Step C shell-vs-pin gotcha).
Record the observed winner per step and the CC version. If observed precedence
diverges from what `reference/config.md` documents, update the reference in the
same follow-up and note the CC version that changed it.
