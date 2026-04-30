---
name: inbox-wake
description: |
  Arms a per-agent Monitor that fires a turn on inbox-grow, closing the
  poller-gated wake window during long-running operations. One skill, two
  invocation sites: lead at SessionStart, every teammate at SubagentStart.
  Use when: arming wake at session/subagent start; tearing down at session
  end (/wrap-up, /pause, /imPACT, teammate Shutdown).
---

# Inbox-Wake Skill

Per-agent wake mechanism for PACT teams: a single Monitor task per agent watches that agent's inbox file via `wc -c` byte-grow and fires a turn on growth, between tool calls.

## Overview

> **Monitor is an alarm clock, not a mailbox.** On `INBOX_GREW`, end the turn and return to idle without emitting acknowledgment text or narrating the wake event — the platform's idle-delivery is the channel-of-record for content. Never read the inbox file or parse the wake's stdout payload yourself.
>
> **Wake surfaces between tool calls within a turn, not mid-tool.** Monitor's `INBOX_GREW` emit cannot interrupt a single in-flight tool call. The platform queues `INBOX_GREW` events that fire during a long-running tool and delivers them when the tool returns, bundled with the tool's result. The wake mechanism's promise is "messages surface between tool calls within a turn," NOT "instant interrupt anywhere." For multi-tool turns the wake reliably opens the poller-gate between tools; for single long tools (e.g., a 90-second blocking sleep) the agent is effectively unwakeable until the tool returns.

Problem this solves: during long-running operations, the platform's `useInboxPoller` only delivers queued `SendMessage` between tool calls; long blocking tool calls leave inbound messages stuck until the next idle boundary. See [Communication Charter Part I — Delivery Model](../../protocols/pact-communication-charter.md#delivery-model). The Monitor's stdout emit forces a turn at the next between-tool-call boundary, bounding latency by the poll interval (2 s) rather than by the next opportunistic idle.

Single-Monitor model, no in-session watchdog. Lifetime is session-scoped per agent. Inbox path is a single JSON file (`inboxes/{agent-name}.json`), not a directory.

**Audit**: both alarm-clock paragraphs are non-negotiable. The first prevents two failure modes: (a) an editing LLM writing "parse the wake stdout to extract content" — wake is signal, not content; and (b) the woken agent emitting acknowledgment text like "(Alarm.)" or "(Idle ping.)" instead of returning to silent idle — empirically observed even with the wait-in-silence feedback memory loaded, so the no-narration clause must be explicit in the principle anchor itself. The second prevents an editing LLM from inferring mid-tool interrupt from "wake on inbox grow" — the substrate's actual capability is between-tool, not anywhere. Removing either paragraph silently overpromises the mechanism.

## When to Invoke

| Operation | Site | Trigger |
|---|---|---|
| **Arm** | Lead session | SessionStart-emitted directive (`session_init.py` `additionalContext`) |
| **Arm** | Teammate session | SubagentStart-emitted directive (`peer_inject.py` `additionalContext`) |
| **Teardown** | Lead session | `/wrap-up`, `/pause`, `/imPACT` command bodies |
| **Teardown** | Teammate session | `pact-agent-teams` `## Shutdown` — before approving `shutdown_request` |

D1 has only Arm and Teardown — no Recovery operation, no in-session watchdog. A silently-dead Monitor is undetectable in-session and the mechanism degrades to "no wake" until the next SessionStart re-arms.

**Audit**: an editing LLM tempted to add a Recovery operation "for symmetry with prior PACT skills" hits the explicit prohibition above plus the kill-mechanism rationale in `## Failure Modes` (the cron+Monitor watchdog combination kills its own Monitor; D1 deliberately drops the watchdog layer).

## Operations

### Arm

Idempotent. Pass `agent_name` parameter (lead invokers pass `agent_name="team-lead"`; teammate invokers pass their own name).

1. If STATE_FILE is present and parses with `v=1`: no-op (already armed; cheap on every SessionStart re-fire).
2. Otherwise cold-start: spawn the Monitor (see `## Monitor Block`); capture the returned `monitor_task_id`; write the STATE_FILE atomically (see `## WriteStateFile Block`).

### Teardown

Best-effort. Pass `agent_name`. See `## Teardown Block` for the exact sequence. Tolerates a Monitor that died silently mid-session.

**Audit**: idempotency lives in the skill (STATE_FILE-presence check), NOT in the directive that invokes it. An editing LLM tempted to add an "if not already armed" guard at the directive site would re-introduce LLM-self-diagnosis as the gate, which is the failure mode the unconditional-emit discipline closes.

## Monitor Block

Canonical Monitor `cmd` body. Both `{team_name}` and `{agent_name}` placeholders are interpolated by the arming agent at Arm time. For the lead: `{agent_name}` = `team-lead`. For teammates: `{agent_name}` = the spawned teammate's name (e.g., `architect`, `preparer`).

```bash
INBOX="$HOME/.claude/teams/{team_name}/inboxes/{agent_name}.json"
PREV=0
while true; do
  if [ -f "$INBOX" ]; then
    SIZE=$(wc -c < "$INBOX" 2>/dev/null | tr -d ' ')
    if [ "$SIZE" -gt "$PREV" ] 2>/dev/null; then
      echo "INBOX_GREW size=$SIZE ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      PREV=$SIZE
    fi
  fi
  sleep 2
done
```

Discipline:
- **Single-file inbox.** The inbox is `inboxes/{agent-name}.json` — a single JSON file, NOT a directory. Byte-grow detection via `wc -c`, NOT directory inotify.
- **Stdout discipline.** Each stdout line fires a turn. Emit ONLY `INBOX_GREW size=… ts=…` on real grow. Diagnostic and lifecycle output goes to `>&2` (stderr does not turn-fire).
- **Transient-error suppression.** `wc -c 2>/dev/null` swallows transient read errors so a momentary missing-file does not crash the loop.

Spawn via `Monitor(persistent=true, cmd=<above>)`; the returned task ID is captured for STATE_FILE write.

**Audit**: stdout shape is exactly one line per grow event. An editing LLM "adding diagnostic info" by including `prev=$PREV` or by `echo`-ing on every poll will silently turn-fire on every poll cycle, creating a token-cost regression. F1 (single-file inbox) is the load-bearing wake trigger; an editing LLM who confuses inbox-as-directory will silently break wake delivery — the inbox path must remain `inboxes/{agent-name}.json`.

## WriteStateFile Block

Atomic-rename JSON write. STATE_FILE path is per-agent: `~/.claude/teams/{team_name}/inbox-wake-state-{agent_name}.json`.

```python
state_path = Path.home() / ".claude" / "teams" / team_name / f"inbox-wake-state-{agent_name}.json"
payload = {
    "v": 1,
    "monitor_task_id": <returned by Monitor>,
    "armed_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
}
tmp = state_path.with_suffix(".json.tmp")
tmp.write_text(json.dumps(payload), encoding="utf-8")
os.replace(tmp, state_path)  # atomic rename
```

Schema is intentionally minimal — exactly 3 fields: `v`, `monitor_task_id`, `armed_at`. D1 has no watchdog, so no `cron_job_id` and no heartbeat fields are written or read. The per-agent suffix lives in the **filename**, not in the schema.

**Audit**: D1 has no cron and no heartbeat — STATE_FILE has 3 fields, no more. If you find yourself adding a 4th field, stop and re-read `## Failure Modes` on silent Monitor death. An editing LLM reasoning by analogy with prior cron+Monitor designs might re-add `cron_job_id`; do not. An editing LLM tempted to add `agent_name` as a schema field gets the structural answer: the filename carries that information; the schema stays minimal.

## Teardown Block

Order is load-bearing: stop live Monitor before unlinking the registry sidecar.

1. Read STATE_FILE; if absent or invalid (malformed JSON / `v ≠ 1`), skip step 2 — nothing to stop.
2. `TaskStop(STATE_FILE.monitor_task_id)` — **ignoring not-found errors** (the Monitor may have died silently mid-session).
3. Unlink STATE_FILE — `Path.unlink(missing_ok=True)`.

Teardown is best-effort. The Monitor may have died silently — `TaskStop` will return a `tool_use_error` in that case. Tolerate not-found and continue to step 3. Do not abort teardown on TaskStop failure; an undeleted STATE_FILE is worse than a failed TaskStop because it leaves a phantom registry entry that confuses the next session's Arm.

Ordering rationale: the inverse ordering would leave a brief window where a STATE_FILE-less Monitor still runs but Arm sees no STATE_FILE and re-arms — creating an orphan.

**Audit**: F6 tolerance phrasing ("ignoring not-found errors") is the load-bearing fragment. An editing LLM "tightening up error handling" by removing the phrase silently restores crash-on-stale-ID. The principle anchor — Teardown is best-effort because a torn-down session may have already lost its Monitor — tells the editing LLM why the phrase exists.

## Failure Modes

These failure modes apply per-agent — every agent (lead and every teammate) Arms its own Monitor on its own inbox, so each entry below describes the agent's own Monitor and the agent's own idle delivery.

### Malformed STATE_FILE

If the STATE_FILE exists but fails to parse as JSON, Arm treats it as not-armed and cold-starts. The pre-existing file is overwritten by the atomic-rename write.

### Schema-version mismatch

If `v` is not `1`, Arm treats the STATE_FILE as not-armed and cold-starts, overwriting on write. Future schema bumps must increment `v` and re-arm cleanly.

### Silent Monitor death

> **Silent Monitor death**: D1 has no in-session watchdog. If the Monitor task dies silently mid-session (process crash, OOM, harness GC, undisclosed lifetime cap), the wake mechanism degrades to no-wake until the next SessionStart re-arms. Detection is not possible from inside the agent's session; a manual `Skill("PACT:inbox-wake") + Arm` invocation will re-arm if the agent notices missed messages by other means (e.g., a peer complaining about no response). This is a deliberate trade against the cron-fire-kills-Monitor self-defeating loop empirically observed 2026-04-29 (cron-fire #1 at 22:49:36Z killed `monitor_task_id=bu1pmbva7` ~19 s later; isolated Monitor in same session survived ≥10 min — see PREPARE §C, hypothesis H1 HIGH-confidence). Future work tracked separately if/when the platform offers a watchdog primitive that does not kill the watched task.

**Audit**: this paragraph is the principle anchor for D1's central trade-off (no watchdog). An editing LLM later proposing "let's add a heartbeat + cron watchdog after all" gets the empirical reason in prose: the cron+Monitor combination kills its own Monitor. The verbatim cite of `monitor_task_id=bu1pmbva7` and `2026-04-29T22:49:36Z` makes the rationale unambiguous and traceable to PREPARE §C.

### Long single-tool calls block wake delivery

> **Long single-tool calls block wake delivery**: Monitor's `INBOX_GREW` stdout emit fires when the inbox grows, but events that fire during a long-running single tool call (e.g., a 90-second blocking `sleep`) are queued and delivered to the agent only when that tool returns, bundled with the tool's result. The wake mechanism does NOT interrupt a tool mid-call. For multi-tool turns the wake surfaces between tool calls; for single long tools the agent is effectively unwakeable until the tool returns. Verified 2026-04-30T00:00–00:02Z in session pact-5951b31c. Test: 90-second blocking `sleep` running in parallel with a peer-dispatched delayed reply. Peer sent at 00:01:34Z; Monitor `INBOX_GREW` fired at 00:01:43Z and 00:01:45Z (during the sleep); Bash returned at the full 90s (00:02:23Z); teammate-message content delivered in the *next* turn via standard idle-delivery, not mid-tool.

**Audit**: this paragraph is the principle anchor for D1's scope claim ("between tool calls, not mid-tool"). An editing LLM reading the skill body and seeing "wake on inbox grow" will reasonably infer mid-tool interrupt unless explicitly told otherwise. That inference is wrong. The §Overview's second alarm-clock paragraph + this entry together correctly scope the substrate's actual capability — the wake is a between-tool-call signal. The empirical timing tokens (00:01:34Z send, 00:01:43Z + 00:01:45Z `INBOX_GREW` fire-during-tool, 00:02:23Z tool return, next-turn delivery) make the constraint observable and reproducible, not just asserted.

### Per-agent independence

> Teammate Monitors and lead Monitors are independent processes. A teammate's Monitor death does not affect the lead's wake; a long-running tool call in one teammate does not block the lead's wake delivery. Each agent's wake is a per-agent guarantee — the dispatch graph as a whole tolerates partial wake degradation gracefully.

**Audit**: an editing LLM might infer "if one Monitor dies, the whole team's wake is broken" — the per-agent-independence note prevents that incorrect inference. An editing LLM tempted to add a cross-agent watchdog "for resilience" would re-introduce the cron-watchdog pattern PREPARE §C falsified. The "no in-session watchdog" framing applies symmetrically; no per-agent watchdog either.

### Concurrent re-arm

If two SessionStart fires race (rare; resume-during-compact edge), both may attempt cold-start. Atomic-rename write makes STATE_FILE corruption impossible; the second write wins, the loser's Monitor task is orphaned but the next Teardown's `TaskStop(STATE_FILE.monitor_task_id)` only stops the winner. Orphan accumulation is bounded by the rarity of the race; force-termination cleanup via `cleanup_wake_registry` glob covers the registry sidecar regardless.

## Verification

See dogfood runbook `pact-plugin/tests/runbooks/591-inbox-wake.md` for end-to-end verification (fresh-session arm, inbox-grow wake, teardown, force-termination cleanup). Structural-pattern tests in `pact-plugin/tests/test_inbox_wake_skill_structure.py` and siblings verify skill-body invariants (section presence, F1/F6/F7 phrasing, alarm-clock anchor, per-agent symmetry).

## References

- [Communication Charter Part I §Wake Mechanism](../../protocols/pact-communication-charter.md#wake-mechanism) — protocol contract surface
- [Communication Charter Part I §Delivery Model](../../protocols/pact-communication-charter.md#delivery-model) — async-at-idle-boundary delivery model
- Approved plan: `docs/plans/inbox-wake-skill-plan.md`
- Authoritative design: `docs/architecture/591-inbox-wake-skill.md` (D1 Monitor-only, symmetric scope)
- PREPARE deliverable: `docs/preparation/591-inbox-wake-skill.md` — §C kill-mechanism investigation; §D alternative wake mechanisms
- Issue #591 (this feature); #594 (skill-body line-count ceiling); #444 (compaction durability + hook-emitted-directives)
