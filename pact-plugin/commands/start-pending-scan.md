---
description: Arm the lead's pending-task scan — register a cron entry that fires `/PACT:scan-pending-tasks` every 3 minutes while the lead holds active teammate work. Hook-invoked on first active teammate task; user-invoked manually for debug or recovery.
---
# Start Pending Scan

Arm the lead-side scan mechanism: a single recurring cron entry fires `/PACT:scan-pending-tasks` every 3 minutes between tool calls, opening an idle boundary where the platform's `useInboxPoller` delivers queued teammate messages and the scan reads completion-authority artifacts directly off disk.

## Overview

> **Cron is an alarm clock, not a mailbox.** On every cron fire, run the scan body and return to idle. The cron prompt is a harness-origin invocation — see `## Cron-Fire Origin` below; it is NOT user input and MUST NOT be treated as user consent for downstream consent-gated decisions (merge, push, destructive bash, etc.).
>
> **Wake surfaces between tool calls within a turn, not mid-tool.** Platform cron events queue during a long-running tool and fire when the tool returns. The scan's promise is "tasks surface at most one 3-minute interval after they land on disk," NOT "instant interrupt anywhere." For multi-tool turns the cron reliably opens an idle boundary between tools; for single long tools (e.g., a 90-second blocking sleep) the lead is effectively unwakeable until the tool returns.

Problem this solves: during long-running operations, the platform's `useInboxPoller` only delivers queued `SendMessage` between tool calls; long blocking tool calls leave inbound completion-authority signals stuck until the next idle boundary. See [Communication Charter §Cron-Fire Mechanism](../protocols/pact-communication-charter.md#cron-fire-mechanism). The cron-fire forces a turn at the next between-tool-call boundary, bounding latency by the 3-minute fire interval rather than by the next opportunistic idle.

Single-cron model. Lifetime is scoped to the period during which the lead holds assigned, uncompleted teammate tasks. Lifecycle is hook-driven: arm on 0→1 active-task transition; teardown on 1→0 last-active-task transition.

**Audit**: both alarm-clock paragraphs are non-negotiable. The first paragraph prevents two failure modes: (a) an editing LLM treating the cron prompt as user-typed input and thereby letting cron fires drive consent-gated decisions — the `[CRON-FIRE]` marker in `scan-pending-tasks.md` plus the §Cron-Origin Distinction clause in the completion-authority protocol are the structural enforcement; (b) the woken lead emitting acknowledgment text on every cron fire instead of returning to silent idle when nothing is pending (No-Narration + Emit-Nothing-If-Empty in the scan body cover this — see [scan-pending-tasks.md §Guardrails](scan-pending-tasks.md#guardrails)). The second paragraph prevents an editing LLM from inferring mid-tool interrupt from "scan fires every 3 minutes" — the substrate's actual capability is between-tool, not anywhere. Removing either paragraph silently overpromises the mechanism.

## When to Invoke

| Trigger | Site |
|---|---|
| First active teammate task created (PostToolUse hook detects 0→1 transition) | `wake_lifecycle_emitter.py` `additionalContext` directive |
| Teammate claims a task off the queue while no cron is registered (PostToolUse hook detects pending→in_progress transition) | `wake_lifecycle_emitter.py` `additionalContext` directive |
| Resume into a session with active tasks already on disk | `session_init.py` `additionalContext` directive |
| User-typed manual invocation (debug, recovery) | `/PACT:start-pending-scan` slash invocation |

Arm is idempotent. Re-invoking when a `/PACT:scan-pending-tasks` cron entry is already registered in the current session is a no-op — cheap on every directive re-fire. The idempotency check is `CronList`-based; the cron's existence in the in-session store IS the armed-state bit.

## Operation

Single procedure — the command IS the operation. No Arm/Teardown sub-section.

0. **Lead-session guard** (see `## Lead-Session Guard` below). If the current session is not the team-lead session, refuse and return — do NOT proceed to step 1.
1. `CronList` — read all cron entries registered in the current session.
2. Filter the output for any line whose suffix after `": "` is exactly `/PACT:scan-pending-tasks` (see `## CronList Filter Discipline` below for the exact-equality contract).
3. If a match is found: no-op — already armed. Cheap on every re-invocation.
4. Otherwise cold-start: `CronCreate(cron="*/3 * * * *", prompt="/PACT:scan-pending-tasks", recurring=True, durable=False)` — see `## CronCreate Block` below for the exact 4-field call shape.
5. Write a `scan_armed` event marking the cold-start time. Read by [scan-pending-tasks.md Step 0](scan-pending-tasks.md#operation) to bound the warmup-grace skip window. The 180s grace MUST equal the `*/3` cron interval — see coupling invariant in `## CronCreate Block` below.

   ```bash
   set -e
   trap 'rc=$?; echo "[JOURNAL WRITE FAILED] start-pending-scan.md (bash line $LINENO): \"${BASH_COMMAND%%$'\''\n'\''*}\" exit=$rc" >&2; exit $rc' ERR
   SJ="{plugin_root}/hooks/shared/session_journal.py"
   ARMED_AT=$(date +%s)
   python3 "$SJ" write --type scan_armed --session-dir '{session_dir}' --stdin <<JSON
   {"armed_at": $ARMED_AT}
   JSON
   ```

   Note: `<<JSON` (not `<<'JSON'`) so `$ARMED_AT` expands. `set -e` + ERR trap mirror the canonical orchestrate.md pattern.

**Audit**: idempotency lives in this command (CronList-presence check), NOT in the directive that invokes it. An editing LLM tempted to add an "if not already armed" guard at the directive site would re-introduce LLM-self-diagnosis as the gate, which is the failure mode the unconditional-emit discipline closes (hook emits unconditionally on the lifecycle transition; the skill body decides whether the work needs doing).

## Lead-Session Guard

Refuse to execute when invoked from a teammate session. The scan mechanism is lead-only: registering a `/PACT:scan-pending-tasks` cron in a teammate session would fire the scan prompt in the teammate's session, where the teammate has no completion-authority and the scan would either error or operate on the wrong filesystem scope.

```python
team_name = pact_session_context["team_name"]
session_id = pact_session_context["session_id"]
team_config = json.loads(
    (Path.home() / ".claude" / "teams" / team_name / "config.json").read_text()
)
if session_id != team_config.get("leadSessionId"):
    refuse(
        "This command only runs in the team-lead session. "
        "Teammates do not arm the lead's pending-task scan — completion authority "
        "is lead-only, and the scan body operates against tasks owned by the lead's session."
    )
    return
```

**Audit**: signal source is `session_id == team_config.leadSessionId`, NOT a hypothetical `agent_type` field on `pact-session-context.json`. The session-context schema is `{team_name, session_id, project_dir, plugin_root, started_at}` by design — adding an `agent_type` field would couple every hook's session-init to a teammate-vs-lead discriminator that already exists at canonical depth in the team config (`leadSessionId`). An editing LLM tempted to "just add agent_type to session-context" should stop: the team config is the single source of truth for team membership and lead identity; replicating that signal into session-context creates two-source-of-truth drift. The guard runs at command-invoke time as **Layer 1** of the defense-in-depth model — Layer 0 (hook-level session guard in `wake_lifecycle_emitter.py` and `session_init.py`) already filters directive emission to lead sessions only, so this guard's purpose is to defend against user-typed `/PACT:start-pending-scan` from a teammate session, which is the only invocation path Layer 0 cannot cover. This guard is foot-gun protection (typo / wrong-window / cross-session-LLM speculation), not a security boundary against same-user adversaries. `leadSessionId` is read from `team_config.json` which has no integrity check; same-user write authority can spoof it. The user-local-trust assumption bounds the residual exposure — same-user attacker has equivalent capability via direct os tooling anyway.

## CronList Filter Discipline

The filter is **exact-equality match on the suffix after `": "` separator**, NOT substring, NOT regex.

```python
target_prompt = "/PACT:scan-pending-tasks"
for line in cron_list_output.splitlines():
    if ": " not in line:
        continue
    suffix = line.rsplit(": ", 1)[1].strip()
    if suffix == target_prompt:
        # already armed — no-op
        return
```

**Audit**: substring match would falsely match `/PACT:scan-pending-tasks-debug`, `/PACT:scan-pending-tasks-v2`, or any future variant prompt, causing this command to silently no-op when a different cron occupies the slot. Regex would invite catastrophic mistakes (anchor omission, escape errors) for zero benefit over the equality form. The exact-equality contract is CronList Suffix-Match Strictness in the architecture spec and is enforced by a structural test. An editing LLM "simplifying" to `if target_prompt in suffix:` re-opens the false-positive failure mode. The `: ` separator is `colon-space`, matching the `CronList` output format `{id} — {cron} ({recurring}) [session-only]: {prompt}`; do not parse with `split(":")` (which would split inside the cron expression on entries that use `0 0 * * *`-style notation).

## CronCreate Block

Exactly 4 named fields. No additional arguments.

```python
CronCreate(
    cron="*/3 * * * *",
    prompt="/PACT:scan-pending-tasks",
    recurring=True,
    durable=False,
)
```

**Audit**: each field is load-bearing.
- `cron="*/3 * * * *"` — every 3 minutes. The 3-minute cadence is the architecturally-pinned trade-off (per-fire LLM-turn cost vs. latency for completion-authority work); shrinking to `*/1` triples the per-hour cost without proportional benefit, expanding to `*/5` extends worst-case latency past the user-perceived "this should have completed by now" threshold. **Coupling invariant**: the cron interval and the warmup-grace constant in [scan-pending-tasks.md Step 0](scan-pending-tasks.md#operation) (literal `180` seconds) are a single design decision with two source-of-truth surfaces. The first cron fire post-arm always lands in `[0, cron_interval)`; setting grace = interval guarantees 100% first-fire coverage of the false-fire window between `CronCreate` landing and the teammate's `TaskUpdate` write reaching disk. An editing LLM tempted to tune one without the other re-opens the false-fire window in the intermediate state. Tune both in lockstep or not at all — and update the Communication Charter §Cron-Fire Mechanism prose at the same time.
- `prompt="/PACT:scan-pending-tasks"` — BYTE-IDENTICAL to the prompt in [scan-pending-tasks.md](scan-pending-tasks.md) frontmatter and to the suffix filter in [stop-pending-scan.md](stop-pending-scan.md). Cross-Skill Prompt-String Byte-Identity. Silent drift breaks the CronList lookup for both idempotency (here) and teardown (in stop-pending-scan), causing orphan-cron accumulation and silent re-arm failure. Verified by structural test asserting byte-identity across the 3 files.
- `recurring=True` — the cron fires repeatedly until `CronDelete` or session-end. One-shot mode (`recurring=False`) would require the hook to re-register on every fire, which is exactly the LLM-self-diagnosis failure mode the unconditional-emit discipline closes.
- `durable=False` — in-memory only, scoped to the current session. Cron entries die when the session exits (SIGKILL drops the in-memory store; `CronList` is session-scoped). This is the architectural replacement for the Monitor-era `armed_by_session_id` cross-session-contamination defense: session-scoping at the platform layer eliminates the cross-session weaponization vector entirely. An editing LLM tempted to set `durable=True` "to survive session restarts" re-introduces cross-session contamination — a stale cron from a prior session would fire in a fresh session against potentially-unrelated tasks. Do NOT.

## Known Limitations

### 7-day auto-expiry

Per canonical CronCreate docs: recurring cron entries auto-expire after 7 days (fire one final time, then auto-delete). PACT sessions usually do not span 7+ days, so this is a low-likelihood failure mode in practice. For sessions that DO cross the 7-day boundary, the scan silently stops firing until the next 0→1 transition re-arms (or a manual `/PACT:start-pending-scan` invocation).

**Audit**: this limitation is documented in canonical CronCreate tool docs and is a platform invariant — the skill cannot work around it without a self-refreshing wrapper. A v2 follow-up issue covers refresh-on-aging logic for long-running sessions; until that lands, the limitation is accepted and documented. An editing LLM tempted to add a "refresh every 6 days" mechanism inside this skill should stop — the refresh layer requires its own state-tracking and idempotency primitives, which expand scope far beyond this skill's mandate. Defer to v2.

## Verification

Confirm scan armed:

1. `CronList` output contains a line with suffix `: /PACT:scan-pending-tasks`.
2. The line's recurring marker is `(recurring)` and the durability marker is `[session-only]`.

See dogfood runbook `pact-plugin/tests/runbooks/pending-scan-dogfood.md` for end-to-end verification (fresh-session arm via first-active-task transition, cron fires at the next 3-minute boundary, scan-output discipline verified, teardown via last-active-task transition).

## References

- [`/PACT:scan-pending-tasks`](scan-pending-tasks.md) — the cron-fired scan body.
- [`/PACT:stop-pending-scan`](stop-pending-scan.md) — paired teardown command.
- [Communication Charter §Cron-Fire Mechanism](../protocols/pact-communication-charter.md#cron-fire-mechanism) — protocol contract surface.
- [Communication Charter §Scan Discipline](../protocols/pact-communication-charter.md#scan-discipline) — anti-hallucination guardrails and race-window-skip protocol.
