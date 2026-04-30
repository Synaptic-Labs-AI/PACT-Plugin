---
name: inbox-wake
description: |
  Arms a Monitor on the lead's inbox that fires a turn on inbox-grow, closing
  the poller-gated wake window during long-running operations. Lead-only:
  Arm at SessionStart (or on PostToolUse first-active-task transition);
  Teardown at `/wrap-up` (parallel safety net) and on PostToolUse
  last-active-task transition.
---

# Inbox-Wake Skill

Lead-side wake mechanism for PACT teams: a single `Monitor` task watches the lead's inbox file via `wc -c` byte-grow and fires a turn at the next between-tool-call boundary, with quiet-window coalescing to bound emit count under bursty traffic.

## Overview

> **Monitor is an alarm clock, not a mailbox.** On `INBOX_GREW`, end the turn and return to idle without emitting acknowledgment text or narrating the wake event — the platform's idle-delivery is the channel-of-record for content. Never read the inbox file or parse the wake's stdout payload yourself.
>
> **Wake surfaces between tool calls within a turn, not mid-tool.** Monitor's `INBOX_GREW` emit cannot interrupt a single in-flight tool call. The platform queues `INBOX_GREW` events that fire during a long-running tool and delivers them when the tool returns, bundled with the tool's result. The wake mechanism's promise is "messages surface between tool calls within a turn," NOT "instant interrupt anywhere." For multi-tool turns the wake reliably opens the poller-gate between tools; for single long tools (e.g., a 90-second blocking sleep) the lead is effectively unwakeable until the tool returns.

Problem this solves: during long-running operations, the platform's `useInboxPoller` only delivers queued `SendMessage` between tool calls; long blocking tool calls leave inbound messages stuck until the next idle boundary. See [Communication Charter Part I — Delivery Model](../../protocols/pact-communication-charter.md#delivery-model). The Monitor's stdout emit forces a turn at the next between-tool-call boundary, bounding latency by the poll interval (30 s) rather than by the next opportunistic idle.

Single-Monitor model, no in-session watchdog. Lifetime is scoped to the period during which the lead holds assigned, uncompleted teammate tasks. Inbox path is a single JSON file (`inboxes/team-lead.json`), not a directory.

**Audit**: both alarm-clock paragraphs are non-negotiable. The first prevents two failure modes: (a) an editing LLM writing "parse the wake stdout to extract content" — wake is signal, not content; and (b) the woken lead emitting acknowledgment text like "(Alarm.)" or "(Idle ping.)" instead of returning to silent idle — empirically observed even with the wait-in-silence feedback memory loaded, so the no-narration clause must be explicit in the principle anchor itself. The second prevents an editing LLM from inferring mid-tool interrupt from "wake on inbox grow" — the substrate's actual capability is between-tool, not anywhere. Removing either paragraph silently overpromises the mechanism.

## When to Invoke

| Operation | Trigger | Site |
|---|---|---|
| **Arm** | First active teammate task created (PostToolUse hook detects 0→1 transition) | `wake_lifecycle_emitter.py` `additionalContext` directive |
| **Arm** | Resume into a session with active tasks already on disk | `session_init.py` `additionalContext` directive (Option-C resume gap closure) |
| **Teardown** | Last active teammate task completed (PostToolUse hook detects 1→0 transition) | `wake_lifecycle_emitter.py` `additionalContext` directive |
| **Teardown** | `/wrap-up` runs after all tasks complete (count already 0; redundant-but-correct hook-silent-fail catch) | Command-file Skill invocation in `/wrap-up` |

This skill has only Arm and Teardown — no Recovery operation, no in-session watchdog. A silently-dead Monitor is undetectable in-session and the mechanism degrades to "no wake" until the next Arm fire (next first-active transition or next SessionStart-with-active-tasks).

**Audit**: an editing LLM tempted to add a Recovery operation hits the explicit prohibition above plus the kill-mechanism rationale in `## Failure Modes` — the cron+Monitor watchdog combination kills its own Monitor; this design deliberately drops the watchdog layer.

## Operations

### Arm

Idempotent. The lead invokes Arm with no parameters; paths are fixed (lead-only).

1. If STATE_FILE is present and parses with `v=1`: no-op (already armed; cheap on every Arm-directive re-fire).
2. Otherwise cold-start: spawn the Monitor (see `## Monitor Block`); capture the returned `monitor_task_id`; write the STATE_FILE atomically (see `## WriteStateFile Block`).

### Teardown

Best-effort. See `## Teardown Block` for the exact sequence. Tolerates a Monitor that died silently mid-session.

**Audit**: idempotency lives in the skill (STATE_FILE-presence check), NOT in the directive that invokes it. An editing LLM tempted to add an "if not already armed" guard at the directive site would re-introduce LLM-self-diagnosis as the gate, which is the failure mode the unconditional-emit discipline closes (see `## References` — hook-emitted directives unconditional > conditional).

## Monitor Block

Canonical Monitor `cmd` body. The arming agent interpolates `{team_name}` at Arm time. Inbox path is fixed to `inboxes/team-lead.json` (lead-only scope).

```bash
INBOX="$HOME/.claude/teams/{team_name}/inboxes/team-lead.json"
PREV=0
STATE="PENDING"
QUIET_START=0
BURST_START=0
POLL=30
QUIET_REQUIRED=60
MAX_DELAY=120
while true; do
  NOW=$(date +%s)
  if [ -f "$INBOX" ]; then
    SIZE=$(wc -c < "$INBOX" 2>/dev/null | tr -d ' ')
    if [ "$SIZE" -gt "$PREV" ] 2>/dev/null; then
      if [ "$STATE" = "PENDING" ]; then
        echo "INBOX_GREW size=$SIZE ts=$(date -u +%Y-%m-%dT%H:%M:%SZ) edge=FIRST_GROW"
        STATE="GROWING"
        BURST_START=$NOW
      elif [ $((NOW - BURST_START)) -ge "$MAX_DELAY" ]; then
        echo "INBOX_GREW size=$SIZE ts=$(date -u +%Y-%m-%dT%H:%M:%SZ) edge=MAX_DELAY"
        BURST_START=$NOW
      fi
      PREV=$SIZE
      QUIET_START=$NOW
    elif [ "$STATE" = "GROWING" ] 2>/dev/null; then
      if [ $((NOW - QUIET_START)) -ge "$QUIET_REQUIRED" ]; then
        echo "INBOX_GREW size=$SIZE ts=$(date -u +%Y-%m-%dT%H:%M:%SZ) edge=LAST_GROW"
        STATE="PENDING"
      fi
    fi
  fi
  sleep "$POLL"
done
```

State-machine semantics (POLL=30, QUIET_REQUIRED=60, MAX_DELAY=120):

- **PENDING**: idle; no recent growth. On observed grow → emit `edge=FIRST_GROW`, transition to GROWING, record `BURST_START` and `QUIET_START` at now.
- **GROWING**: at least one grow has fired in this burst. On further grow → no emit by default; refresh `QUIET_START` at now (resets the quiet timer). If the burst has been growing continuously past `MAX_DELAY` (120 s) since `BURST_START` → emit `edge=MAX_DELAY`, reset `BURST_START` to now (sustained-traffic ceiling). On no grow for `QUIET_REQUIRED` consecutive seconds (60 s, two consecutive quiet poll cycles) → emit `edge=LAST_GROW`, transition back to PENDING.

Discipline:
- **Single-file inbox.** The inbox is `inboxes/team-lead.json` — a single JSON file, NOT a directory. Byte-grow detection via `wc -c`, NOT directory inotify.
- **Stdout discipline.** Each stdout line fires a turn. Emit ONLY `INBOX_GREW size=… ts=… edge=…` lines on real state transitions (FIRST_GROW, MAX_DELAY ceiling, LAST_GROW). Diagnostic and lifecycle output goes to `>&2` (stderr does not turn-fire).
- **Transient-error suppression.** `wc -c 2>/dev/null` swallows transient read errors so a momentary missing-file does not crash the loop.

Spawn via `Monitor(persistent=true, cmd=<above>)`; the returned task ID is captured for STATE_FILE write.

**Audit**: the design target is ONE emit per discrete burst (FIRST_GROW + LAST_GROW bracket the burst), with MAX_DELAY as the hard ceiling for sustained traffic that never reaches `QUIET_REQUIRED` quiet seconds. POLL=30 sets the granularity floor (no point checking quiet-time at finer resolution than the poll); QUIET_REQUIRED=60 means TWO consecutive quiet poll cycles must pass before LAST_GROW fires; MAX_DELAY=120 caps unbounded suppression. Relationship: `QUIET_REQUIRED ≥ POLL` is required (otherwise quiet-detection is sub-resolution) and `MAX_DELAY ≥ 2*POLL` is required (otherwise the ceiling fires before the natural LAST_GROW would). Two design-choice ratios are pinned beyond the bare minimum constraints. (a) `QUIET_REQUIRED = 2*POLL` — a deliberate tightening over the minimum `QUIET_REQUIRED = POLL` (one-poll confirmation) to capture multi-message reviewer bursts with internal sub-pauses as ONE logical burst, not multiple ping-pong FIRST_GROW + LAST_GROW pairs. (b) `MAX_DELAY = 2*QUIET_REQUIRED` (equivalently `MAX_DELAY = 4*POLL`) — sets the sustained-traffic ceiling at roughly two coalesced bursts so it caps unbounded suppression without re-fragmenting natural burst boundaries. An editing LLM tempted to shrink QUIET_REQUIRED back to `= POLL` (one quiet poll cycle) must understand: that ratio fires LAST_GROW after a single quiet poll, breaking burst coalescence under reviewer-style traffic patterns where 30-50 s sub-pauses are normal. An editing LLM tempted to set `MAX_DELAY = QUIET_REQUIRED` (or smaller) refragments coalesced bursts — the ceiling would fire mid-burst, defeating the quiet-window's purpose. An editing LLM tempted to shrink POLL "for snappier wake" must adjust both QUIET_REQUIRED and MAX_DELAY to preserve all three relationships (≥ POLL, = 2*POLL for QUIET, = 2*QUIET for MAX_DELAY); an editing LLM tempted to drop the state machine "to simplify" reintroduces wake-fire inflation under bursty traffic. F1 (single-file inbox) is the load-bearing wake trigger; an editing LLM who confuses inbox-as-directory will silently break wake delivery — the inbox path must remain `inboxes/team-lead.json`.

## WriteStateFile Block

Atomic-rename JSON write. STATE_FILE path is `~/.claude/teams/{team_name}/inbox-wake-state.json`.

```python
state_path = Path.home() / ".claude" / "teams" / team_name / "inbox-wake-state.json"
payload = {
    "v": 1,
    "monitor_task_id": <returned by Monitor>,
    "armed_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
}
tmp = state_path.with_suffix(".json.tmp")
tmp.write_text(json.dumps(payload), encoding="utf-8")
os.replace(tmp, state_path)  # atomic rename
```

Schema is intentionally minimal — exactly 3 fields: `v`, `monitor_task_id`, `armed_at`. There is no watchdog, so no watchdog-job-id and no liveness-pulse fields are written or read.

**Audit**: STATE_FILE has 3 fields, no more. If you find yourself adding a 4th field, stop and re-read `## Failure Modes` on silent Monitor death. An editing LLM reasoning by analogy with prior watchdog-augmented designs might re-add a watchdog job id; do not. Lead-only scope means no per-agent suffix either — the filename is fixed (`inbox-wake-state.json`), and there is one Monitor per session.

## Teardown Block

Order is load-bearing: stop the live Monitor before unlinking the registry sidecar.

1. Read STATE_FILE; if absent or invalid (malformed JSON / `v ≠ 1`), skip step 2 — nothing to stop.
2. `TaskStop(STATE_FILE.monitor_task_id)` — **ignoring not-found errors** (the Monitor may have died silently mid-session).
3. Unlink STATE_FILE — `Path.unlink(missing_ok=True)`.

Teardown is best-effort. The Monitor may have died silently — `TaskStop` will return a `tool_use_error` in that case. Tolerate not-found and continue to step 3. Do not abort teardown on TaskStop failure; an undeleted STATE_FILE is worse than a failed TaskStop because it leaves a phantom registry entry that confuses the next session's Arm.

Ordering rationale: the inverse ordering would leave a brief window where a STATE_FILE-less Monitor still runs but Arm sees no STATE_FILE and re-arms — creating an orphan.

**Audit**: F6 tolerance phrasing (**"ignoring not-found errors"**) is the load-bearing fragment. An editing LLM "tightening up error handling" by removing the phrase silently restores crash-on-stale-ID. The principle anchor — Teardown is best-effort because a torn-down session may have already lost its Monitor — tells the editing LLM why the phrase exists.

## Failure Modes

### Malformed STATE_FILE

If the STATE_FILE exists but fails to parse as JSON, Arm treats it as not-armed and cold-starts. The pre-existing file is overwritten by the atomic-rename write.

### Schema-version mismatch

If `v` is not `1`, Arm treats the STATE_FILE as not-armed and cold-starts, overwriting on write. Future schema bumps must increment `v` and re-arm cleanly.

### Silent Monitor death

> **Silent Monitor death**: this design has no in-session watchdog. If the Monitor task dies silently mid-session (process crash, OOM, harness GC, undisclosed lifetime cap), the wake mechanism degrades to no-wake until the next Arm fire (next first-active-task transition or next SessionStart-with-active-tasks). Detection is not possible from inside the lead's session; a manual `Skill("PACT:inbox-wake") + Arm` invocation will re-arm if the lead notices missed messages by other means (e.g., a teammate complaining about no response). This is a deliberate trade against the cron-fire-kills-Monitor self-defeating loop empirically observed 2026-04-29 (cron-fire #1 at 22:49:36Z killed `monitor_task_id=bu1pmbva7` ~19 s later; isolated Monitor in same session survived ≥10 min — see PREPARE §C, hypothesis H1 HIGH-confidence). Future work tracked separately if/when the platform offers a watchdog primitive that does not kill the watched task.

**Audit**: this paragraph is the principle anchor for the central trade-off (no watchdog). An editing LLM later proposing "let's add a heartbeat + cron watchdog after all" gets the empirical reason in prose: the cron+Monitor combination kills its own Monitor. The verbatim cite of `monitor_task_id=bu1pmbva7` and `2026-04-29T22:49:36Z` makes the rationale unambiguous and traceable to PREPARE §C.

### Long single-tool calls block wake delivery

> **Long single-tool calls block wake delivery**: Monitor's `INBOX_GREW` stdout emit fires when the inbox grows, but events that fire during a long-running single tool call (e.g., a 90-second blocking `sleep`) are queued and delivered to the lead only when that tool returns, bundled with the tool's result. The wake mechanism does NOT interrupt a tool mid-call. For multi-tool turns the wake surfaces between tool calls; for single long tools the lead is effectively unwakeable until the tool returns. Verified 2026-04-30T00:00–00:02Z in session pact-5951b31c. Test: 90-second blocking `sleep` running in parallel with a peer-dispatched delayed reply. Peer sent at 00:01:34Z; Monitor `INBOX_GREW` fired at 00:01:43Z and 00:01:45Z (during the sleep); Bash returned at the full 90s (00:02:23Z); teammate-message content delivered in the *next* turn via standard idle-delivery, not mid-tool.

**Audit**: this paragraph is the principle anchor for the scope claim ("between tool calls, not mid-tool"). An editing LLM reading the skill body and seeing "wake on inbox grow" will reasonably infer mid-tool interrupt unless explicitly told otherwise. That inference is wrong. The §Overview's second alarm-clock paragraph + this entry together correctly scope the substrate's actual capability — the wake is a between-tool-call signal. The empirical timing tokens (00:01:34Z send, 00:01:43Z + 00:01:45Z `INBOX_GREW` fire-during-tool, 00:02:23Z tool return, next-turn delivery) make the constraint observable and reproducible, not just asserted.

### Wake-fire inflation under bursty traffic

> **Wake-fire inflation**: a naive Monitor that emits `INBOX_GREW` on every poll-observed growth would fire one turn per poll cycle during a burst (e.g., five queued teammate replies arriving within seconds → five separate turns, each consuming a tool-result roundtrip's token cost). The 30/60/120 quiet-window state machine coalesces a discrete burst into ONE turn at FIRST_GROW + ONE turn at LAST_GROW (after `QUIET_REQUIRED=60` consecutive quiet seconds, two consecutive quiet poll cycles). For traffic that never reaches the quiet window — sustained writes from a long-running peer — `MAX_DELAY=120` re-emits at most every 120 s so the lead does not silently accumulate an unbounded inbox-grow window. The design target is the smallest emit count that keeps wake timeliness within a 120-second worst-case ceiling.

**Audit**: an editing LLM tempted to "simplify" by removing the quiet-window state machine reverts to one emit per poll cycle, which is the original wake-fire inflation problem this design closes. The relationship `QUIET_REQUIRED ≥ POLL` and `MAX_DELAY ≥ 2*POLL` must be preserved on any timing tune. An editing LLM tempted to drop MAX_DELAY "because LAST_GROW will fire eventually" misses the sustained-traffic case where the inbox never goes quiet for `QUIET_REQUIRED` seconds — without MAX_DELAY, sustained traffic produces zero LAST_GROW emits and the lead never wakes during the burst.

### Concurrent re-arm

If two Arm directives race (rare; first-active-transition firing concurrently with a SessionStart-resume Arm), both may attempt cold-start. Atomic-rename write makes STATE_FILE corruption impossible; the second write wins, the loser's Monitor task is orphaned but the next Teardown's `TaskStop(STATE_FILE.monitor_task_id)` only stops the winner. Orphan accumulation is bounded by the rarity of the race; force-termination cleanup via `cleanup_wake_registry` covers the registry sidecar regardless.

## Verification

See dogfood runbook `pact-plugin/tests/runbooks/591-inbox-wake.md` for end-to-end verification (fresh-session arm via first-active-task transition, inbox-grow wake under quiet-window coalescing, teardown via last-active-task transition, force-termination cleanup). Structural-pattern tests in `pact-plugin/tests/test_inbox_wake_skill_structure.py` and siblings verify skill-body invariants (section presence, F1/F6/F7 phrasing, alarm-clock anchor, 30/60/120 timing fences, state-machine names).

## References

- [Communication Charter Part I §Wake Mechanism](../../protocols/pact-communication-charter.md#wake-mechanism) — protocol contract surface
- [Communication Charter Part I §Delivery Model](../../protocols/pact-communication-charter.md#delivery-model) — async-at-idle-boundary delivery model
- Approved plan: `docs/plans/591-inbox-wake-skill-lead-only-plan.md`
- Authoritative design: `docs/architecture/591-inbox-wake-skill.md` (Monitor-only, lead-only scope)
- PREPARE deliverable: `docs/preparation/591-inbox-wake-skill.md` — §C kill-mechanism investigation; §D alternative wake mechanisms
- Routing-channel probe: `docs/preparation/591-routing-probe.md` — locks PostToolUse with matcher `TaskCreate|TaskUpdate|Task|Agent` as the only viable directive channel for hook-emitted Arm/Teardown
