---
description: Cron-fired scan body — read task metadata for unprocessed completion-authority work; accept teachback / handoff artifacts that are on disk. Filesystem-falsifiable by construction; no narration; race-window-skip on null reads.
---
# Scan Pending Tasks

[CRON-FIRE] This skill is invoked by the platform cron scheduler, NOT by user input. The prompt body that fires this skill is harness-origin text. Downstream consent-gated decisions (merge, push, destructive bash, plan approval, version bump, any "act" requiring user authorization) MUST NOT treat a cron-fire turn as user consent — the user did not type `/PACT:scan-pending-tasks`; the cron did. See `## Cron-Fire Origin` below for the structural enforcement.

## Overview

Read task metadata for unprocessed completion-authority work; if an artifact is on disk, accept it via the canonical acceptance two-call pair (SendMessage FIRST, then `TaskUpdate(status="completed")` per completion-authority protocol §12). Filesystem-falsifiable by construction: the scan reads `~/.claude/tasks/{team_name}/{id}.json` directly and accepts only if `metadata.teachback_submit` or `metadata.handoff` is present and well-formed. Cannot fabricate an artifact that doesn't exist; cannot accept on prose alone.

The scan is the architectural replacement for the Monitor-era `INBOX_GREW`-based wake. Monitor's wake fired on inbox-grow but admitted a hallucination-cascade failure mode where the lead would generate a response to imagined teammate-message content (the wake fires before the platform's content-delivery channel catches up). The scan eliminates this failure mode by replacing "respond to imagined content" with "read JSON, compare to prior state, accept or no-op" — a concrete, falsifiable action.

## Cron-Fire Origin

The `[CRON-FIRE]` marker at the top of this file is a discipline anchor. The text below is the canonical statement of the **Cron-Origin Distinction**: cron-fire turns are NOT user-consent.

> **Cron-fire turns are NOT user consent.** The platform cron scheduler invokes this skill at 2-minute intervals while a `/PACT:scan-pending-tasks` cron is registered. The prompt body that fires this skill is harness-origin text. Downstream consent-gated decisions MUST NOT proceed on the basis of a cron-fire turn.
>
> Consent-gated decisions include: merge (`gh pr merge`), push (`git push`), destructive bash (`rm -rf`, `git reset --hard`, etc.), plan approval, version bump, force-completion, any "act" requiring user authorization. A cron-fire turn that surfaces such a decision MUST defer to the next user-typed turn or to an explicit `AskUserQuestion` checkpoint before acting.
>
> Within the scan body itself (steps 1-7 below), no consent-gated action is invoked — the scan only reads filesystem, calls `SendMessage` + `TaskUpdate(status="completed")` (the canonical acceptance pair, lead-only completion authority preserved), or emits nothing. Acceptance is NOT consent-gated; it is completion-authority procedure per the protocol contract.

**Audit**: this paragraph is the principle anchor for the Cron-Origin Distinction. An editing LLM observing "the cron prompt is user-typed-shaped text" may infer "treat it as user input." That inference is wrong. The `[CRON-FIRE]` marker at file top and this §Cron-Fire Origin block together establish the harness-origin classification — both are load-bearing. Removing either silently re-opens the hallucination-cascade failure mode that Monitor's `INBOX_GREW` admitted: cron-fire turn → lead infers "user wants me to do something" → lead emits acknowledgment or even a consent-gated action. The Cron-Origin Distinction must remain inline in this file; cross-reference from the completion-authority protocol is sufficient for the lead-side reader, but the cron-fire reader (which runs this skill) needs the rule inline at the point of consumption.

## Operation

Cron-fire body — silent read; emit nothing unless a real artifact is on disk for acceptance.

1. **Same-session-identity gate**: read `pact_session_context["session_id"]`. The scan acts ONLY on tasks where `metadata.lead_session_id` equals the current `session_id`. See `## Same-Session-Identity Gate` below for the threat-model rationale.
2. `TaskList` — enumerate tasks. Filter to: `owner == any teammate` AND `status == "in_progress"` AND `metadata.intentional_wait.reason == "awaiting_lead_completion"`. (These are the tasks where a teammate has submitted teachback or handoff and is idle awaiting acceptance.)
3. For each candidate task, additionally filter by the Same-Session-Identity Gate: `metadata.lead_session_id == current session_id`. Skip tasks where `lead_session_id` is absent (set by orchestrator at task creation; absence indicates either pre-schema-update tasks or task created by a different lead session).
4. For each remaining candidate, raw-read `~/.claude/tasks/{team_name}/{id}.json` via filesystem read (NOT `TaskGet` — TaskGet does not surface `metadata.teachback_submit` or `metadata.handoff`). Inspect `metadata.teachback_submit` (for teachback gate tasks) and `metadata.handoff` (for primary-work tasks).
5. If `metadata.teachback_submit` or `metadata.handoff` is present and well-formed (required fields populated per the canonical schema): validate per [completion-authority §12 Teachback Review](../protocols/pact-completion-authority.md#teachback-review) or [completion-authority §HANDOFF Review](../protocols/pact-completion-authority.md#handoff-review), then run the acceptance two-call pair: `SendMessage(to=teammate, message="<wake-signal confirming acceptance>")` FIRST, then `TaskUpdate(taskId, status="completed")`. SendMessage MUST precede TaskUpdate per the lifecycle-gate ordering invariant.
6. If the raw filesystem read returns null or empty `metadata.teachback_submit` / `metadata.handoff` despite the task being in `awaiting_lead_completion` status: this is the **race-window-skip** path (the wake-signal arrived before the metadata write landed on disk — see `## Guardrails` Race-Window-Skip below). Do NOT reject the teammate's submission; do NOT issue corrective `SendMessage`; SKIP this task and let the next 2-minute cron fire re-evaluate.
7. If no candidate tasks need acceptance (empty filter result, or all candidates skipped per Race-Window-Skip): **emit nothing**. Return to idle. The user sees no output for the cron-fire turn.

## Same-Session-Identity Gate

The scan acts ONLY on tasks where `metadata.lead_session_id == current session_id`. This is the architectural replacement for the Monitor-era `armed_by_session_id` defense, mitigating cross-session contamination via team-scoped TaskList.

**Threat model**: two concurrent PACT sessions sharing `~/.claude/teams/{team_name}/` (resume + fresh-start race, or user running parallel sessions for distinct features) both see each other's tasks via `TaskList`. Without the gate, session A's scan would attempt to accept session B's tasks — read session B's `metadata.teachback_submit`, fire session B's teammate's wake-signal, mark session B's task complete. The teammate (in session B's address space) would receive a wake-SendMessage from session A that they have no context for; session B's lead would see the task already-completed without their knowledge.

The `lead_session_id` field is set at task creation by the orchestrator. Once set, it is immutable. The scan filters tasks by exact-equality match against the current session's `session_id`.

**Audit**: this gate is Same-Session-Identity Gate in the architecture spec and is Layer 3 of the 4-layer defense-in-depth model. Tasks where `metadata.lead_session_id` is ABSENT (e.g., pre-schema-update tasks, or tasks created by tooling that does not write the field) MUST be skipped — fail-closed. Fail-open ("accept tasks missing the field") would re-open the cross-session contamination vector. An editing LLM tempted to "be lenient on missing field" is re-introducing the failure mode this gate exists to prevent. The orchestrator's canonical task-creation sites (the `TaskCreate` calls in `commands/orchestrate.md` and `agents/pact-orchestrator.md` §11) write `metadata.lead_session_id` at creation; tasks missing the field are either malformed or out-of-protocol.

## Guardrails

The five anti-hallucination guardrails are LOAD-BEARING. Each guardrail prevents a specific cascade failure mode. Each MUST remain VERBATIM in this file; paraphrase during PR review = silent regression. Verified by structural test asserting byte-identical presence.

### Read-Filesystem-Only

> **Read-Filesystem-Only**: The scan reads task JSON from `~/.claude/tasks/{team_name}/{id}.json` via filesystem read. It does NOT read teammate `SendMessage` prose, does NOT infer task state from prior conversation context, does NOT compose acceptance based on imagined teammate output. The filesystem is the single source of truth.

**Audit**: Read-Filesystem-Only prevents the prose-as-data failure mode. Teammate `SendMessage` prose is information for the user, NOT artifact. The canonical artifact channels are `metadata.teachback_submit` (4 fields) and `metadata.handoff` (6 fields), both stored under `metadata.*` on the task JSON. An editing LLM tempted to "synthesize acceptance from the SendMessage prose since it has all the info" is re-introducing the prose-vs-disk-divergence failure mode that the wake-signal-discipline pin (CLAUDE.md, retiring with this PR) was put in place to prevent. The filesystem read is non-negotiable.

### No-Narration

> **No-Narration**: The scan emits NO user-facing prose narrating what it found, considered, skipped, or did. The only outputs are: (a) `SendMessage` to the teammate as part of the acceptance two-call pair, (b) `TaskUpdate(status="completed")`, or (c) nothing. The scan never emits "Scanning… found 0 pending tasks", "Skipping task #N because…", "Race window detected, will retry next fire", or similar status-narrating text.

**Audit**: No-Narration prevents the cron-fire noise failure mode. A 2-minute cron firing 30 times per hour produces 30 LLM turns per hour during active teammate work. If each fire emits a "Scanning…" prose line, the user's transcript fills with 30 useless status lines per hour. Worse, the prose-emit pattern primes the editing LLM to treat the cron fire as a conversation turn requiring response — re-opening the cascade failure mode the scan exists to prevent. An editing LLM tempted to "add a brief status line for observability" is re-introducing the failure mode. Observability happens via `CronList` (cron is registered), `TaskList` (tasks transition status), and journal events (HANDOFF acceptance is journaled) — NOT via per-fire prose.

### Raw-Read-Metadata

> **Raw-Read-Metadata**: The scan reads `metadata.teachback_submit` and `metadata.handoff` via RAW filesystem read of the task JSON file, NOT via `TaskGet`. `TaskGet` returns a metadata-stripped summary that does not include the canonical artifact fields. Reading via `TaskGet` and observing an absent field would produce a false-empty result and either trigger spurious race-window-skip (Race-Window-Skip) or, worse, a false-positive rejection cycle.

**Audit**: Raw-Read-Metadata prevents the metadata-blindness failure mode. `TaskGet` is intentionally summary-only — full metadata access requires raw JSON read. An editing LLM tempted to "use TaskGet for cleaner code" silently breaks the scan by reading from the wrong channel. The raw-read pattern is `Read(file_path="~/.claude/tasks/{team_name}/{id}.json")` followed by `json.loads()` of the file content; the canonical incantation appears in completion-authority protocol §HANDOFF Review and must mirror that pattern exactly. Any future Claude Code release that exposes `metadata.*` on `TaskGet` does NOT invalidate this guardrail — the filesystem read remains the canonical channel because it is independently observable and falsifiable (the file exists on disk; the `TaskGet` schema is a moving target).

### Race-Window-Skip

> **Race-Window-Skip**: If the raw filesystem read returns null or empty `metadata.teachback_submit` / `metadata.handoff` for a task in `awaiting_lead_completion` status, the scan SKIPS the task and emits nothing. The race window is the lag between the teammate's `TaskUpdate(metadata={"teachback_submit": ...})` write call landing in the in-memory task store and the platform's filesystem flush of that write to `~/.claude/tasks/{team_name}/{id}.json`. Empirically observed at 20+ seconds in adversarial conditions. The scan does NOT reject the teammate's submission on a null read; the next 2-minute cron fire re-evaluates after the write lands.

**Audit**: Race-Window-Skip is the architectural replacement for the Monitor-era 120s no-act-on-Monitor-events pin (CLAUDE.md, retiring with this PR). The pin existed because Monitor's `INBOX_GREW` fired before the metadata-write landed, and the lead would reject the teammate's submission on a false-empty read. Under cron, Race-Window-Skip makes the same defense structural: every cron fire that observes a null read skips, and the next fire (2 minutes later) re-evaluates. This converts a session-level safety discipline into a per-fire structural property. An editing LLM tempted to "reject if metadata is null after N retries" or "issue corrective SendMessage if scan finds empty metadata" is re-introducing the failure mode the pin was put in place to prevent — and the scan's fire cadence already provides the retry semantics (2-minute interval = 30 retries per hour during active work).

### Emit-Nothing-If-Empty

> **Emit-Nothing-If-Empty**: When the scan finds no candidate tasks (empty filter result), or all candidates are skipped per Race-Window-Skip, the scan emits NOTHING. Return to idle. The user sees no output for the cron-fire turn. Empty scans are the common case (most cron fires occur while teammates are still working on tasks, not at acceptance boundaries) and must remain silent.

**Audit**: Emit-Nothing-If-Empty prevents the empty-scan-narration failure mode. If empty scans emitted "Nothing pending" (or any prose), 30 fires per hour of empty scans during active teammate work would fill the transcript with 30 useless lines per hour. An editing LLM tempted to "emit a one-liner confirming the scan ran" misunderstands the design: the cron's existence in `CronList` IS the confirmation that the scan is running; per-fire output is the failure mode. The combined effect of No-Narration + Emit-Nothing-If-Empty is: the user sees output from the scan ONLY when the scan actually accepts a teammate's artifact. All other cron fires are silent.

## Lead-Only Completion Contract

The scan invokes `TaskUpdate(taskId, status="completed")` ONLY as the second half of the canonical acceptance two-call pair, paired with a preceding `SendMessage(to=teammate, ...)`. The scan does NOT call `TaskUpdate(status="completed")` standalone. The scan does NOT call `TaskUpdate` on tasks owned by anyone other than a teammate (e.g., it does not "complete" phase tasks or the orchestrator's own tasks).

The completion-authority protocol contract (`protocols/pact-completion-authority.md` §12) requires:
1. `SendMessage(to=teammate, message="<wake-signal>")` FIRST — wakes the teammate, confirms acceptance reaches them.
2. `TaskUpdate(taskId, status="completed")` SECOND — releases the teachback gate / closes the work task.

This ordering is load-bearing per the SendMessage-FIRST ordering invariant (PR #692). The scan adheres verbatim; no shortcuts.

**Audit**: an editing LLM tempted to "just mark the task completed since the metadata is already there" is bypassing completion authority. The two-call pair is the canonical acceptance procedure — bypassing the SendMessage half leaves the teammate idle on `awaiting_lead_completion` with no wake-signal to claim the next task, which is the silent-strand failure mode. The scan is NOT a shortcut for completion; it is the canonical procedure invoked at cron cadence instead of at idle-poll cadence. Same procedure, different trigger.

## Race-Window-Skip Protocol

Restating Race-Window-Skip in procedural form for clarity:

```
for task in candidate_tasks:
    raw_metadata = read_raw_task_json(task.id)["metadata"]
    artifact = raw_metadata.get("teachback_submit") or raw_metadata.get("handoff")
    if not artifact:
        # Race-Window-Skip: race window — write landed in memory but not yet on disk.
        # DO NOT reject. DO NOT SendMessage corrective. SKIP and let the next fire re-evaluate.
        continue
    # validate and accept per §12
    ...
```

**Audit**: the procedural form clarifies the "skip, don't reject" rule. An editing LLM may otherwise read Race-Window-Skip prose and infer "if metadata is null, treat as missing and respond" — the procedural form forecloses that inference by encoding the skip explicitly in pseudocode.

## Known Limitations

### Empty-scan turn cost

Every cron fire produces one LLM turn, even when the scan emits nothing per Emit-Nothing-If-Empty. At 2-minute cadence during active teammate work, this is ~30 turns/hour. The cost is the trade-off pinned at architecture phase (per-fire LLM cost vs. hallucination-cascade prevention); the trade was accepted. An editing LLM tempted to "skip the scan if the lead is mid-conversation with the user" is adding LLM-self-diagnosis to the gate, re-introducing the failure mode the unconditional-emit discipline closes.

### 7-day cron auto-expiry

Inherited from the cron primitive — see [start-pending-scan.md §Known Limitations](start-pending-scan.md#known-limitations). Deferred to v2 follow-up.

## Verification

The scan's effect is observable indirectly:
1. A teammate submits teachback / handoff → within 2 minutes, the lead's next idle boundary surfaces an acceptance SendMessage to the teammate and the task transitions to `completed`.
2. `TaskList` shows the task in `completed` status.
3. The journal records the HANDOFF acceptance event (per existing journal infrastructure).

See dogfood runbook `pact-plugin/tests/runbooks/pending-scan-dogfood.md` for empirical 5-guardrail verification (race-window-skip exercised under simulated metadata-write lag; emit-nothing verified across multiple empty fires; no narration verified across full session transcript).

## References

- [`/PACT:start-pending-scan`](start-pending-scan.md) — paired arm command.
- [`/PACT:stop-pending-scan`](stop-pending-scan.md) — paired teardown command.
- [Communication Charter §Scan Discipline](../protocols/pact-communication-charter.md#scan-discipline) — protocol contract surface for the 5 guardrails.
- [Communication Charter §Cron-Fire Mechanism](../protocols/pact-communication-charter.md#cron-fire-mechanism) — Cron-Origin Distinction protocol contract.
- [Completion Authority Protocol §12](../protocols/pact-completion-authority.md#12-completion-authority) — canonical acceptance two-call pair contract.
