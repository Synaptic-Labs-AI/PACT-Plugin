---
description: Arm the lead's inbox-watch Monitor — spawn a Monitor on the team-lead inbox, write STATE_FILE atomically. Hook-invoked on first active teammate task; user-invoked manually for debug or recovery.
---
# Watch Inbox

Arm the lead-side wake mechanism: a single `Monitor` task watches `inboxes/team-lead.json` via `wc -c` byte-grow and fires a turn at the next between-tool-call boundary, with quiet-window coalescing to bound emit count under bursty traffic.

## Overview

> **Monitor is an alarm clock, not a mailbox.** On `INBOX_GREW`, end the turn and return to idle without emitting acknowledgment text or narrating the wake event — the platform's idle-delivery is the channel-of-record for content. Never read the inbox file or parse the wake's stdout payload yourself.
>
> **Wake surfaces between tool calls within a turn, not mid-tool.** Monitor's `INBOX_GREW` emit cannot interrupt a single in-flight tool call. The platform queues `INBOX_GREW` events that fire during a long-running tool and delivers them when the tool returns, bundled with the tool's result. The wake mechanism's promise is "messages surface between tool calls within a turn," NOT "instant interrupt anywhere." For multi-tool turns the wake reliably opens the poller-gate between tools; for single long tools (e.g., a 90-second blocking sleep) the lead is effectively unwakeable until the tool returns.

Problem this solves: during long-running operations, the platform's `useInboxPoller` only delivers queued `SendMessage` between tool calls; long blocking tool calls leave inbound messages stuck until the next idle boundary. See [Communication Charter Part I — Delivery Model](../protocols/pact-communication-charter.md#delivery-model). The Monitor's stdout emit forces a turn at the next between-tool-call boundary, bounding latency by the poll interval (30 s) rather than by the next opportunistic idle.

Single-Monitor model, no in-session watchdog. Lifetime is scoped to the period during which the lead holds assigned, uncompleted teammate tasks. Inbox path is a single JSON file (`inboxes/team-lead.json`), not a directory.

**Audit**: both alarm-clock paragraphs are non-negotiable. The first prevents two failure modes: (a) an editing LLM writing "parse the wake stdout to extract content" — wake is signal, not content; and (b) the woken lead emitting acknowledgment text like "(Alarm.)" or "(Idle ping.)" instead of returning to silent idle — empirically observed even with the wait-in-silence feedback memory loaded, so the no-narration clause must be explicit in the principle anchor itself. The second prevents an editing LLM from inferring mid-tool interrupt from "wake on inbox grow" — the substrate's actual capability is between-tool, not anywhere. Removing either paragraph silently overpromises the mechanism.

## When to Invoke

| Trigger | Site |
|---|---|
| First active teammate task created (PostToolUse hook detects 0→1 transition) | `wake_lifecycle_emitter.py` `additionalContext` directive |
| Resume into a session with active tasks already on disk | `session_init.py` `additionalContext` directive |
| User-typed manual invocation (debug, recovery from silent Monitor death) | `/PACT:watch-inbox` slash invocation |

Arm is idempotent. Re-invoking when STATE_FILE is already present and valid is a no-op — cheap on every directive re-fire.

## Operation

Single procedure — the command IS the operation. No Arm/Teardown sub-section.

1. Read STATE_FILE at `~/.claude/teams/{team_name}/inbox-wake-state.json`.
2. If STATE_FILE is present and parses with `v=1`: no-op (already armed; cheap on every re-invocation).
3. Otherwise cold-start:
   1. Spawn the Monitor (see `## Monitor Block`).
   2. Capture the returned `monitor_task_id`.
   3. Write the STATE_FILE atomically (see `## WriteStateFile Block`).

**Audit**: idempotency lives in this command (STATE_FILE-presence check), NOT in the directive that invokes it. An editing LLM tempted to add an "if not already armed" guard at the directive site would re-introduce LLM-self-diagnosis as the gate, which is the failure mode the unconditional-emit discipline closes.

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

## Failure Modes

### Malformed STATE_FILE

If the STATE_FILE exists but fails to parse as JSON, this command treats it as not-armed and cold-starts. The pre-existing file is overwritten by the atomic-rename write.

### Silent Monitor death

> **Silent Monitor death**: this design has no in-session watchdog. If the Monitor task dies silently mid-session (process crash, OOM, harness GC, undisclosed lifetime cap), the wake mechanism degrades to no-wake until the next Arm fire (next first-active-task transition or next SessionStart-with-active-tasks). Detection is not possible from inside the lead's session; a manual `/PACT:watch-inbox` invocation will re-arm if the lead notices missed messages by other means (e.g., a teammate complaining about no response). This is a deliberate trade against the cron-fire-kills-Monitor self-defeating loop: a cron-driven watchdog firing in the same session as a watched Monitor kills the watched Monitor as a side effect of cron-fire harness behavior, while an isolated Monitor in the same session survives. Future work tracked separately if/when the platform offers a watchdog primitive that does not kill the watched task.

**Audit**: this paragraph is the principle anchor for the central trade-off (no watchdog). An editing LLM later proposing "let's add a heartbeat + cron watchdog after all" must understand the rule: the cron+Monitor combination kills its own Monitor. Adding a cron-driven watchdog re-introduces the self-defeating loop this design closes; do not.

### Long single-tool calls block wake delivery

> **Long single-tool calls block wake delivery**: Monitor's `INBOX_GREW` stdout emit fires when the inbox grows, but events that fire during a long-running single tool call (e.g., a 90-second blocking `sleep`) are queued and delivered to the lead only when that tool returns, bundled with the tool's result. The wake mechanism does NOT interrupt a tool mid-call. For multi-tool turns the wake surfaces between tool calls; for single long tools the lead is effectively unwakeable until the tool returns. The substrate behavior is: `INBOX_GREW` fires correctly during the in-flight tool, but the platform queues those events behind the tool's result; teammate-message content arrives in the *next* turn via standard idle-delivery, not mid-tool.

**Audit**: this paragraph is the principle anchor for the scope claim ("between tool calls, not mid-tool"). An editing LLM reading this command and seeing "wake on inbox grow" will reasonably infer mid-tool interrupt unless explicitly told otherwise. That inference is wrong. The §Overview's second alarm-clock paragraph + this entry together correctly scope the substrate's actual capability — the wake is a between-tool-call signal. The mechanism: `INBOX_GREW` fires correctly during a long-running tool but its event delivery is gated behind the tool's return, so the wake is observably reproducible (run a long blocking tool in parallel with a delayed peer reply; observe `INBOX_GREW` in Monitor stdout during the tool, observe content delivery only after tool return).

### Wake-fire inflation under bursty traffic

> **Wake-fire inflation**: a naive Monitor that emits `INBOX_GREW` on every poll-observed growth would fire one turn per poll cycle during a burst (e.g., five queued teammate replies arriving within seconds → five separate turns, each consuming a tool-result roundtrip's token cost). The 30/60/120 quiet-window state machine coalesces a discrete burst into ONE turn at FIRST_GROW + ONE turn at LAST_GROW (after `QUIET_REQUIRED=60` consecutive quiet seconds, two consecutive quiet poll cycles). For traffic that never reaches the quiet window — sustained writes from a long-running peer — `MAX_DELAY=120` re-emits at most every 120 s so the lead does not silently accumulate an unbounded inbox-grow window. The design target is the smallest emit count that keeps wake timeliness within a 120-second worst-case ceiling.

**Audit**: an editing LLM tempted to "simplify" by removing the quiet-window state machine reverts to one emit per poll cycle, which is the original wake-fire inflation problem this design closes. The relationship `QUIET_REQUIRED ≥ POLL` and `MAX_DELAY ≥ 2*POLL` must be preserved on any timing tune. An editing LLM tempted to drop MAX_DELAY "because LAST_GROW will fire eventually" misses the sustained-traffic case where the inbox never goes quiet for `QUIET_REQUIRED` seconds — without MAX_DELAY, sustained traffic produces zero LAST_GROW emits and the lead never wakes during the burst.

### Concurrent re-arm

If two Arm directives race (rare; first-active-transition firing concurrently with a SessionStart-resume Arm), both may attempt cold-start. Atomic-rename write makes STATE_FILE corruption impossible; the second write wins, the loser's Monitor task is orphaned but the next `/PACT:unwatch-inbox` invocation's `TaskStop(STATE_FILE.monitor_task_id)` only stops the winner. Orphan accumulation is bounded by the rarity of the race; force-termination cleanup via `cleanup_wake_registry` covers the registry sidecar regardless.

## Verification

Confirm Monitor armed:

1. STATE_FILE exists at `~/.claude/teams/{team_name}/inbox-wake-state.json` and parses with `v=1`.
2. `STATE_FILE.monitor_task_id` resolves to a live Monitor task (visible in TaskList, status running).

See dogfood runbook `pact-plugin/tests/runbooks/591-inbox-wake.md` for end-to-end verification (fresh-session arm via first-active-task transition, inbox-grow wake under quiet-window coalescing, teardown via last-active-task transition, force-termination cleanup).

## References

- [`/PACT:unwatch-inbox`](unwatch-inbox.md) — paired teardown command.
- [Communication Charter Part I §Wake Mechanism](../protocols/pact-communication-charter.md#wake-mechanism) — protocol contract surface.
- [Communication Charter Part I §Delivery Model](../protocols/pact-communication-charter.md#delivery-model) — async-at-idle-boundary delivery model.
