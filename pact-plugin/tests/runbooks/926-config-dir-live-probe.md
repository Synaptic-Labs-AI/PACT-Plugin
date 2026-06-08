# Runbook: CLAUDE_CONFIG_DIR Path-Resolution Live-Probe Acceptance Gate

**Purpose:** the post-merge runtime-confirmation gate for the config-dir-aware
path resolution (`shared/paths.py::get_claude_config_dir`). The comprehensive
L1 (resolver contract) + L2 (non-mocked integration: dispatch-flip,
two-idiom containment, both-modes, classification-stay-fixed) layers ship IN the
PR and run in CI. L3 — that a REAL Claude Code session launched under a
non-default `CLAUDE_CONFIG_DIR` actually resolves state, agents, and `@`-ref
imports from the relocated root — CANNOT be driven by pytest (platform `@`-ref
resolution + agent discovery + state writes happen at real session bootstrap).
This runbook is that gate.

This mirrors the #923 / #861 pattern: a fix is "merged-but-not-runtime-confirmed"
until a live session under the real env demonstrates the behavior end-to-end.

## §1 — Acceptance criterion (NOT closed on green tests alone)

The fix is runtime-confirmed only when ALL hold under a real session whose
`CLAUDE_CONFIG_DIR` is set to a non-default dir (e.g. `~/.claude-kimi`):

- (a) **Dispatch unblocks** — a teammate with an assigned task is NOT blocked by
  `dispatch_gate` rule ⑧ (`has_task_assigned` resolves the task under
  `$CLAUDE_CONFIG_DIR/tasks`, not `~/.claude/tasks`). This is the original bug.
- (b) **Agents resolve** — specialist agents are discovered/loaded from
  `$CLAUDE_CONFIG_DIR/agents` (or the dual-location install), i.e. the team
  spawns specialists at all.
- (c) **`@`-ref imports resolve** — at least one `@~/.claude/protocols/...`
  import inside a loaded `CLAUDE.md` / agent body resolves to readable content
  under the active config (the C6 dual-location protocols symlink makes this
  answer-immune by construction; verify it live anyway).
- (d) **No silent state loss** — the session's `pact-sessions/<slug>/<id>` dir is
  created under `$CLAUDE_CONFIG_DIR/pact-sessions` and the Resume line points
  there (validates the migrated `session_init:433` `_validate_under_pact_sessions`
  anchor — a home-lagged anchor would reject the correctly-stored path → None →
  session-dir silently lost).

## §2 — Live-probe procedure (per teammateMode)

Run once per mode the operator uses (tmux mandatory; in-process if used). The
resolver is mode-independent (keys on the env var, not session topology — proven
by the L2 both-modes pair), so a divergence between modes here is itself a finding.

**Setup.** In a fresh shell:
```bash
export CLAUDE_CONFIG_DIR="$HOME/.claude-kimi"     # any non-default, pre-created or platform-created
mkdir -p "$CLAUDE_CONFIG_DIR"
# launch Claude Code / the PACT session in the target mode under this env
```

- **Step 1 — bootstrap a team.** Start a PACT session; let it create a team +
  spawn the secretary. **OBSERVE:** the team config and tasks appear under
  `$CLAUDE_CONFIG_DIR/teams/<team>/` and `$CLAUDE_CONFIG_DIR/tasks/<team>/`
  (NOT under `~/.claude/...`). → criterion (a) substrate + (b) spawn.
- **Step 2 — dispatch a teammate with a task.** Create a Task A/B dispatch to any
  specialist. **OBSERVE:** the teammate is NOT blocked at dispatch (no
  "no task assigned" deny); it proceeds to teachback. → criterion (a).
- **Step 3 — confirm agent + `@`-ref resolution.** The spawned specialist loads
  its agent body and `CLAUDE.md`. **OBSERVE:** the agent runs (agents resolved
  from the config-dir install) and at least one `@~/.claude/protocols/...`
  import renders non-empty content. → criteria (b)+(c).
- **Step 4 — confirm session-dir placement.** **OBSERVE:** the Resume line / session
  dir is `$CLAUDE_CONFIG_DIR/pact-sessions/<slug>/<id>` and is readable. → (d).
- **Step 5 — settings tip sanity (optional).** If `additionalDirectories` is
  unset, the setup tip should reference the config-dir paths (validates the
  `session_init:350` membership anchor). → corroborates (a)/(d).

**PASS** = (a)+(b)+(c)+(d) all hold in the mode under test (Step 5 corroborating).
On any FAIL, capture the resolved path observed vs expected and file a follow-up
with the session-journal evidence; a home-anchored resolution in any criterion is
a re-anchoring regression (cross-check against the L2 counter-test cardinality
`{4 failed, 7 passed}` documented in `test_config_dir_comprehensive.py`).

## §3 — Sections-passed denominator

Denominator is **4** (criteria a/b/c/d) per mode. Record per-mode in
`RUNBOOK_RUN_DATES.md`. The live run is expected to occur under the user's
non-default-config environment (e.g. a kimi-cli session) or a deliberate
`CLAUDE_CONFIG_DIR` export in a tmux session post-merge.
