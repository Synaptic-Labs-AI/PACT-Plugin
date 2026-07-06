# Runbook: PACT Runtime Config injection HONORING Live-Probe Acceptance Gate

**Purpose:** the post-merge runtime-confirmation gate for whether the orchestrator
LLM **acts on** the injected `## PACT Runtime Config` block. The L1 (composer
unit — `format_pact_runtime_config`) and L2 (non-mocked real-`main()` emission —
the block LANDS in `hookSpecificOutput.additionalContext`, greedy-on ≠ greedy-off,
teammate-gated) layers ship IN the PR and run in CI
(`test_pact_config_injection.py`, `test_config_injection_both_modes.py`). What
those layers CANNOT prove is **honoring**: that the LLM reading the delivered
block actually changes behavior. That is LLM behavior, not Python, and is
runtime-only — this runbook is that gate.

This mirrors the #923 / #926 pattern: a fix is "merged-but-not-runtime-confirmed"
until a live session demonstrates the behavior end-to-end. Do NOT build a fake
automated "honoring" test — an assertion that the LLM "acted" would be theater.

**Prior evidence (pre-build).** The design's pre-build injection-honoring probes
(session tasks #20/#21/#22 — CLAUDE.md-block vs additionalContext A/B, nonce-gated
enactment, benign surface disambiguation) already showed additionalContext-delivered
config is honored for reversible actions (4/4). This runbook is the **post-build
re-confirm** on the shipped composer + wiring, not the first validation.

## §1 — Acceptance criterion (NOT closed on green tests alone)

A green suite is NECESSARY but NOT SUFFICIENT. The injection path is
runtime-confirmed only when BOTH hold under a real Claude Code session:

1. **Delivery (already CI-covered, re-observe live):** with
   `PACT_PR_GREEDY_FIX=1` in the launch env, the emitted SessionStart
   `additionalContext` contains `## PACT Runtime Config (resolved at session
   start)` with `- PR greedy-fix: ON (PACT_PR_GREEDY_FIX)`. (The L2 test pins
   this; confirm it survives real platform hook-dispatch.)
2. **Honoring (the runtime-only gate):** in a real `/PACT:peer-review` run whose
   review surfaces at least one *minor* or *future* finding, the orchestrator
   under `PACT_PR_GREEDY_FIX=1` **batches** those findings into the remediation
   path (greedy) instead of running the per-finding Step A/B/C gate; and under
   `PACT_PR_GREEDY_FIX=0` (or unset) it runs today's per-finding gate unchanged.
   The **visible** difference in peer-review behavior between the two launches is
   the honoring evidence.

## §2 — Procedure

### Step A — DELIVERY re-observe
Launch two real sessions (or one, re-launched): `PACT_PR_GREEDY_FIX=1 claude …`
and `PACT_PR_GREEDY_FIX=0 claude …`. In each, inspect the SessionStart
`additionalContext` (the orchestrator's own context) and record whether the
`## PACT Runtime Config` block shows `ON` vs `OFF` for PR greedy-fix. Both must
match the launch value.

### Step B — HONORING (reversibility-scoped, safe)
In the greedy-ON session, run `/PACT:peer-review` on a branch whose review
yields ≥1 minor/future finding. Observe whether the orchestrator **auto-batches**
the minor/future remediation (greedy) and **surfaces guardrail-excluded findings
as an end-of-run summary** — rather than prompting the Step A "review the minor
recommendations?" gate. In the greedy-OFF session, confirm the per-finding gate
still fires.

**SACROSANCT tripwire.** Confirm greedy NEVER auto-merges, closes, pushes, or
performs any irreversible/outward-facing action without the explicit user
checkpoint (reversibility scoping). If greedy takes an irreversible action
autonomously, **HALT and escalate** — that is a reversibility-scope breach, not a
pass.

## §3 — Record

Append a dated row to `RUNBOOK_RUN_DATES.md` under `## 1113-config-injection-honoring-live-probe.md`:
Sections-passed denominator = **2** (Step A delivery, Step B honoring). Record the
observed `ON`/`OFF` block text and the observed greedy-vs-gated peer-review
behavior. If Step B shows no behavioral difference between the two launches, the
injected config is delivered-but-not-honored → re-open and route to the injection
consumer prose in `commands/peer-review.md`. Do not close the issue on Step A alone.
