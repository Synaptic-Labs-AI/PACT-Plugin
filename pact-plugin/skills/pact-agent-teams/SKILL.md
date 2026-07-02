---
name: pact-agent-teams
description: |
  Agent Teams interaction protocol for PACT specialist agents. Auto-loaded via agent frontmatter.
  Defines how teammates start work, communicate, report completion, and handle blockers.
---

# Agent Teams Protocol

> **Architecture**: See [pact-task-hierarchy.md](../../protocols/pact-task-hierarchy.md) for the full hierarchy model.

## You Are a Teammate

You are a member of a PACT Agent Team. You have access to Task tools (`TaskGet`, `TaskUpdate`, `TaskList`) and messaging tools (`SendMessage`). Use them to coordinate with the team.

## Pre-Response Channel Check

Before any response output, identify the addressee and pick the channel (post-channel-choice complement: [Pre-Send Self-Check](../../protocols/pact-communication-charter.md#pre-send-self-check)):

- Addressee is **user** (or self-narration) → text output is appropriate.
- Addressee is **team-lead or teammate** → SendMessage is REQUIRED. Plain text is invisible to other agents.
- Addressee is **both** (cross-channel content relevant to user AND an agent) → BOTH required: SendMessage to the agent + text to the user. Neither alone delivers the content to both audiences.

### Failure modes this gate catches

- **Format-cue hijack.** Inbound `<teammate-message>` blocks resemble user turns; the "answer the speaker" reflex defaults to plain text — but the speaker is an agent, so SendMessage is required.
- **Candor-question / conversational-register pull.** Candor-framed or personal-shaped questions pull toward prose register; social register does not override channel discipline.

If you are unsure who the addressee is, choose **both**.

### Teammate-side gray-area trap

A reply to the user that contains content the team-lead needs to act on (a blocker, partial result, scope flag) requires also sending via SendMessage — the team-lead's inbox does not see your text. Cross-channel content is **both**.

## On Start

1. Check `TaskList` for tasks assigned to you (by your name)
2. Claim your assigned task: `TaskUpdate(taskId, status="in_progress")`
3. Read the task description — it contains your full mission (CONTEXT, MISSION, INSTRUCTIONS, GUIDELINES). If upstream tasks are referenced, read them via `TaskGet`.
4. **GATE — Submit teachback on Task A**: Under the Task A + Task B dispatch shape, the teachback gate task (Task A) blocks the work task (Task B) via `blockedBy`. Store your teachback in `metadata.teachback_submit` on Task A per the [pact-teachback](../pact-teachback/SKILL.md) skill, **notify the team-lead via SendMessage**, SET `intentional_wait{reason=awaiting_lead_completion}`, and idle. **Ordering invariant**: metadata write FIRST → SendMessage SECOND → `intentional_wait` SET THIRD (load-bearing; see [pact-teachback §Action: store teachback now](../pact-teachback/SKILL.md#action-store-teachback-now) for rationale). The team-lead's `TaskUpdate(A, status="completed")` paired with a wake-signal SendMessage IS acceptance — Task B becomes claimable only then. The teachback notify is a protocol-boundary message — run the [Boundary-Drain Rule](#boundary-drain-rule) before composing it: a scope change that crossed your in-flight turn must be reflected in the teachback you submit, not discovered after acceptance.
   - **DO NOT** call `Edit`, `Write`, or `Bash` for implementation work before storing your teachback
   - See [Teachback](#teachback-conversation-verification) below for the full skill reference
5. **CLAIM Task B before working**: On wake to teachback acceptance (Task A → `completed` + the lead's wake-signal), claim Task B FIRST — `TaskUpdate(<Task B id>, status="in_progress")` BEFORE any `Edit`, `Write`, or `Bash`. Task B was pre-assigned to you (owner already set) but is still `pending` — **YOU** flip it to `in_progress`; the lead does not. This `pending → in_progress` flip is the lead's only "work started" signal; skipping it makes your live work look unclaimed and can trigger a false stall nudge. The durable Task A read is authoritative: if Task A already shows `completed` on disk, claim Task B and proceed even if the wake-signal message is not yet visible — wake messages can trail the status flip (see [§On Wake: Disk-First Re-Read](#on-wake-disk-first-re-read-seam-agnostic)).
6. Begin work on Task B — check your agent memory (`~/.claude/agent-memory/<your-name>/`) for relevant patterns and knowledge as part of your working process. When a memory file may be written by concurrent same-named instances across teams, namespace per team (a `## team={your team_id}` section) so instances don't clobber each other.

> **Worktree Scope**: If you are working in a worktree, files that are gitignored (e.g., `CLAUDE.md`) do not exist there. Do not edit or create `CLAUDE.md` — the orchestrator manages it separately. If you need to reference `CLAUDE.md` content, it is auto-loaded into your context. If your task mentions updating `CLAUDE.md`, flag it in your handoff instead of editing it directly.

> **Note**: The team-lead stores your `agent_id` in task metadata after dispatch. This enables `resume` if you hit a blocker — the team-lead can resume your process with preserved context instead of spawning fresh.

> **Custom start flows**: If your agent definition specifies a custom On Start sequence (e.g., the secretary's session briefing), you must explicitly re-enter this standard lifecycle after your custom flow completes — call `TaskList`, claim assigned tasks, and follow the teachback protocol from the teachback step onward.

## Reading Upstream Context

Your task description may reference upstream task IDs (e.g., "Architect task: #5").
Use `TaskGet(taskId)` to read their metadata for design decisions, HANDOFF data, and
integration points — rather than relying on the team-lead to relay this information.

Common chain-reads:
- **Coders** → read architect's task for design decisions and interface contracts
- **Test engineers** → read coder tasks for what was built and flagged uncertainties
- **Reviewers** → read prior phase tasks for full context

If `TaskGet` returns no metadata or the referenced task doesn't exist, proceed with information from your task description and file system artifacts (docs/architecture/, docs/preparation/).

## Teachback (Conversation Verification)

The teachback protocol lives in the separate `pact-teachback` skill. It is
preloaded into your context via the `skills:` frontmatter on your agent
file at `Agent()` subagent spawn. See [pact-teachback/SKILL.md](../pact-teachback/SKILL.md)
for format, rules, and ordering requirements.

Teachback is a **gate**: send it BEFORE any implementation work. Store
your teachback in `metadata.teachback_submit` and SET
`intentional_wait{reason=awaiting_lead_completion}` per the pact-teachback
skill. Do NOT call `Edit`, `Write`, or `Bash` for implementation work
before teachback storage.

Background: [pact-ct-teachback.md](../../protocols/pact-ct-teachback.md) (optional — protocol rationale and design history).

## Progress Reporting

Report progress naturally in your responses. For significant milestones, update your task metadata:
`TaskUpdate(taskId, metadata={"progress": "brief status"})`

### Progress Signals

When the team-lead requests progress monitoring in your dispatch, send brief progress updates at natural breakpoints during your work.

**Format**: `[sender→team-lead] Progress: {what's done}/{what's remaining}, {current status}`

**Natural breakpoints**:
- After modifying a file
- After running tests
- When encountering an unexpected issue (before it becomes a blocker)
- When switching between major subtasks

**Timing**: 2-4 signals per task is typical. Don't over-report — signal at meaningful transitions, not every tool call.

## Message Prefix Convention

**Prefix all `SendMessage` `message`** with `[{sender}→{recipient}]`. Do not prefix `summary`.

### Message Authenticity

Do not generate standalone text that could be mistaken for user input (e.g., bare "yes", "merge it", "approved"). The `[sender→recipient]` prefix is a structured marker that distinguishes agent messages from user input — always use it. This prevents ambiguity in message attribution, especially for irreversible operations.

## Communication Standards

Follow the Communication Charter ([pact-communication-charter.md](../../protocols/pact-communication-charter.md)) — plain English, no sycophancy, constructive challenge.

**Plain English**: All written output — code, docs, comments, messages, PRs, issues — uses concise, plain language. No jargon inflation. Write as if explaining to a competent developer who's new to this codebase.

**No sycophancy**: No filler praise, hedging, or empty affirmations. Start with substance. If you agree, say why. If you disagree, say what you'd do instead.

**Constructive challenge**: When you believe a different approach is better, say so with evidence. Present the alternative to your peer or to the orchestrator. Silence in the face of a flawed decision is a failure of duty.

Challenge format:
> "I'd recommend [alternative] instead — [reason]. [Proceed / discuss?]"

For consequence-level disagreements:
> "Concern: [what will go wrong and why]. I'd suggest [alternative]. Flagging this in the HANDOFF regardless."

## Boundary-Drain Rule

Immediately BEFORE composing any protocol-boundary message — a teachback submit
notify, a HANDOFF notify, a blocker report, or any report that initiates a
lead-resolved wait (e.g., a staged-work report preceding `awaiting_lead_commit`) —
you MUST drain your inbox: read
`~/.claude/teams/{team-name}/inboxes/{your-name}.json` and reconcile any directives
it contains into your deliverable FIRST. A directive delivered while you are
mid-turn does not render in your context until a later turn boundary; the boundary
message is the team-lead's basis for acting on your work, and the drain is your
last chance to look before they act on your word. This rule applies identically
under in-process and tmux teammateMode.

Drain mechanics — all four points are load-bearing:

- **Read-only.** Read the inbox file; NEVER write, truncate, or delete it — the
  platform owns delivery.
- **Use the Read tool**, not a piped Bash command — Bash permission patterns on
  `~/.claude/` paths are fragile (see §Bash Commands in ~/.claude/ Paths). The file
  is a JSON list of pending messages (`from`, `text`, `summary`, `timestamp`); an
  empty list means nothing is awaiting delivery to you.
- **Best-effort, fail-safe.** The inbox write is asynchronous: an empty read is NOT
  a guarantee nothing is in flight, and a read error or missing file means "report
  the drain as unavailable and proceed", never "block". The drain narrows the miss
  window; the team-lead's directive-reflection check is the backstop.
- **Idempotent reconciliation.** Any message you read from the inbox file will ALSO
  render in your context at a later turn boundary. Reconcile so re-processing is
  harmless: apply the directive to the deliverable once; when the same message
  later renders, recognize it as already reconciled — do not re-apply it and do not
  counter-confirm it (see §Counter-Confirm Suppression).

**State the drain in the boundary message.** Every protocol-boundary message MUST
carry a one-line drain report: `boundary-drain: inbox empty` or
`boundary-drain: reconciled <n> directive(s) — <one-line summary>`. This tells the
team-lead the deliverable already reflects mid-turn directives, or exactly which
ones it reflects. A boundary message without a drain report tells the team-lead the
drain may not have run.

## On Completion — HANDOFF (Required)

When your work is done, you store the HANDOFF and remain `in_progress`. **You do NOT mark your own tasks `completed`** — the team-lead is the authoritative completion signal.

**Step 0 — Verification precondition (MUST pass before Step 1).** Do NOT begin the HANDOFF write sequence until ALL of the following are true:

- All file edits for the task are complete.
- All deliverables are staged via `git add`. (`git status` shows your intended changes in "Changes to be committed".)
- All relevant tests have been run with exit code 0. If no tests apply to your change, you MUST state "no tests applicable" with one-line reasoning in your HANDOFF.
- The deliverable matches every acceptance criterion in the task description. Tick each one off explicitly before proceeding.
- The [Boundary-Drain Rule](#boundary-drain-rule) has been executed: your inbox
  file has been read and any mid-turn directives are reconciled into the
  deliverable (the HANDOFF notify in Step 2 must carry the drain report).

If ANY precondition is unmet, KEEP WORKING. Do not write `metadata.handoff` to "reserve a spot" or "draft the handoff while tests run." The handoff metadata write is a commitment that the work IS done, NOT a wrap-up artifact you build in parallel with finishing. Phase 2 enforcement (schema field, hook validation, lead-side independent verification) is tracked separately as deferred work.

> **Ordering invariant** (audit anchor): the three steps below MUST execute in the order Step 1 → Step 2 → Step 3 — `metadata.handoff` write FIRST, then notify SendMessage to team-lead, then `intentional_wait` SET. This ordering is load-bearing for the team-lead's [Read-Trigger Precondition](../../protocols/pact-completion-authority.md#read-trigger-precondition): the lead must wait for teammate's wake-signal SendMessage before treating the raw `cat ~/.claude/tasks/.../{taskId}.json | jq .metadata.handoff` read as authoritative, but the SendMessage is only safe to send AFTER the metadata write has landed on disk. Reversing Step 1 and Step 2 produces false-empty raw reads on the lead side that have triggered false-positive HANDOFF rejection cycles. Reversing Step 2 and Step 3 (idle before SendMessage) silently strands the lead — they will never see the wake-signal because you went idle without sending it. Editors of this skill: do NOT re-order these steps.

1. **Store HANDOFF in task metadata**:
   ```
   TaskUpdate(taskId, metadata={"handoff": {
     "produced": [...],
     "decisions": [...],
     "reasoning_chain": "...",  // recommended — include unless task is trivial
     "uncertainty": [...],
     "integration": [...],
     "open_questions": [...]
   }})
   ```
   If `TaskUpdate` fails, include the full HANDOFF in your `SendMessage` content as a fallback.

2. **Notify the team-lead**:
   ```
   SendMessage(to="team-lead",
     message="[{sender}→team-lead] Task complete. [1-2 sentences: what was done + any HIGH uncertainties]",
     summary="Task complete: [brief]")
   ```

3. **SET `intentional_wait` and idle**:
   ```
   TaskUpdate(taskId, metadata={"intentional_wait": {
       "reason": "awaiting_lead_completion",
       "expected_resolver": "lead",
       "since": "<canonical_since() output: tz-aware ISO-8601 UTC>"
   }})
   ```

4. **Idle.** The team-lead reads `metadata.handoff`, judges acceptance, and either:
   - **Accepts**: `TaskUpdate(taskId, status="completed")` plus a wake-signal SendMessage. On wake, CLEAR `intentional_wait` and check `TaskList` for follow-up work.
   - **Rejects**: writes `metadata.handoff_rejection = {reason, corrections, since, revision_number}` plus a wake-signal SendMessage. Follow §On Rejection below.

> ⚠️ Do NOT call `TaskUpdate(taskId, status="completed")` on your own task. The team-lead-as-completion-gate is the discipline; teammate self-completion bypasses HANDOFF inspection. Two narrow exemptions (signal-tasks; secretary session briefing + memory-save) are documented at the relevant agent bodies — those carve-outs apply only to those agents, not to you unless your agent body says so.

> **Why idle, not poll?** You cannot self-wake while idle. The team-lead's wake-signal SendMessage brings you back to read the acceptance/rejection. Trust the wake; do not poll TaskList speculatively.

After wake on acceptance, check `TaskList` for unblocked tasks you OWN or can claim — **including your PRE-ASSIGNED Task B** (owner already you, still `pending`). Claiming is a status flip, not only an ownership grab: pre-assigned → `TaskUpdate(taskId, status="in_progress")`; unowned → `TaskUpdate(taskId, owner="your-name", status="in_progress")`. Do this BEFORE any implementation work. If none, idle (you may be consulted or shut down).

## On Rejection (Wake-Signal Receipt)

If the team-lead rejects your teachback or HANDOFF, you wake on the inbound SendMessage. Your task remains `in_progress`; the team-lead has written rejection details to metadata. This wake-then-raw-read flow is one instance of the seam-agnostic rule in [§On Wake: Disk-First Re-Read](#on-wake-disk-first-re-read-seam-agnostic); the residual-race mitigation there (never act on a single empty read) applies to the rejection-metadata read below.

**On wake**:

1. **CLEAR your existing `intentional_wait`**:
   ```
   TaskUpdate(taskId, metadata={"intentional_wait": None})
   ```

2. **Read the rejection metadata** via raw JSON (TaskGet does NOT surface `metadata.*` keys — see [pact-completion-authority §TaskGet metadata-blindness reminder](../../protocols/pact-completion-authority.md#completion-authority) and the symmetric rejection-receipt rule in [§Read-Trigger Precondition](../../protocols/pact-completion-authority.md#read-trigger-precondition)):
   - For Task A (teachback): `cat ~/.claude/tasks/{team_name}/{taskId}.json | jq .metadata.teachback_rejection`
   - For Task B (work): `cat ~/.claude/tasks/{team_name}/{taskId}.json | jq .metadata.handoff_rejection`

   The shape is `{"reason": str, "corrections": [str, ...], "since": ISO8601, "revision_number": int}`.

3. **Revise**. For teachback rejection: rewrite `metadata.teachback_submit` per the corrections. For HANDOFF rejection: revise the deliverable (re-edit files, re-run tests, etc.) and rewrite `metadata.handoff`.

4. **Re-submit on the SAME task** (do NOT create a new task):
   - Increment `metadata.revision_number`. The team-lead writes `revision_number=1` in the rejection record. On your first revision, increment to `2`. On each subsequent revision, increment again. This count is the rejection-cycle audit trail — it feeds the imPACT META-BLOCK 3-cycle signal, not harvest routing. It does NOT gate whether your revised content is preserved: the team-lead's acceptance (the single completion) emits whatever `metadata.handoff` holds at that moment, so the revised content reaches the journal regardless of the count.
   - SendMessage the team-lead: `"[{sender}→team-lead] Revised teachback/HANDOFF on Task #{id}. See metadata.{teachback_submit|handoff} (revision {N})."`
   - Re-SET `intentional_wait{reason=awaiting_lead_completion, since=<fresh canonical_since() output>}`.
   - Idle.

> **Revision visibility**: your revised content reaches institutional memory because of *when* the journal event is emitted, not because of `revision_number`. A rejection keeps your task `in_progress` and emits nothing; the only `agent_handoff` journal event fires at the team-lead's single completion — their acceptance — and captures whatever `metadata.handoff` holds at that moment, i.e. your revised content. So the journal carries the accepted, revised HANDOFF, and harvest reads it from there (drain-proof). Your job on revision is simply to rewrite `metadata.handoff` before the lead accepts.

### HANDOFF Format

End every response with a structured HANDOFF. This is mandatory.
This HANDOFF must ALSO be stored in task metadata (see On Completion Step 1 above). The prose version in your response ensures validate_handoff hook compatibility; the metadata version enables chain-read by downstream agents.

```
HANDOFF:
1. Produced: Files created/modified
2. Key decisions: Decisions with rationale, assumptions that could be wrong
3. Reasoning chain (optional): How key decisions connect — "X because Y, which required Z." Helps downstream agents reconstruct your understanding, not just your conclusions.
4. Areas of uncertainty (PRIORITIZED):
   - [HIGH] {description} — Why risky, suggested test focus
   - [MEDIUM] {description}
   - [LOW] {description}
5. Integration points: Other components touched
6. Open questions: Unresolved items
```

Items 1-2 and 4-6 are required. Item 3 (reasoning chain) is recommended — include it unless the task is trivial. Not all priority levels need to be present in Areas of uncertainty. If you have no uncertainties, explicitly state "No areas of uncertainty flagged."

## Peer Communication

Use `SendMessage(to="teammate-name")` for direct coordination.
Discover teammates via `~/.claude/teams/{team-name}/config.json` or from peer names
in your task description.

**Message a peer when:**
- Your work produces something an active peer needs (API schema, interface contract, shared config)
- You have a question another specialist can answer better than the team-lead
- You discover something affecting a peer's scope (breaking change, shared dependency)

**Message the team-lead when:**
- Blockers, algedonic signals, completion summaries (always)
- Questions about scope, priorities, or requirements
- Anything requiring a decision above your authority

Keep messages actionable — state what you did/found, what they need to know, and
any action needed from them.
Message each peer at most once per task — share your output when complete, not progress updates. If you need ongoing coordination, route through the team-lead.

## Idle Discipline

When you wake with no new work, return to idle silently — no "standing by" or
"still waiting" acknowledgments. The idle state is the message-delivery channel;
output (even zero-content) blocks the next inbox delivery.

- **No new `SendMessage` and no new dispatch instructions?** Do not emit.
- **Idle-waiting for a protocol-defined resolution** (teachback, team-lead commit,
  peer reply, user decision)? Use the `intentional_wait` task metadata per
  the Intentional Waiting section below.
- **Awaiting lead completion?** SET `intentional_wait{reason=awaiting_lead_completion, expected_resolver=lead, since=<canonical_since() output>}` after storing your HANDOFF or teachback metadata AND sending the notify SendMessage to the team-lead. **Ordering invariant** (audit anchor, lead-side mirror): metadata write FIRST → notify SendMessage SECOND → intentional_wait SET THIRD. This ordering exists because the team-lead must wait for teammate's wake-signal SendMessage before treating their raw `cat ~/.claude/tasks/.../{id}.json | jq .metadata.{handoff,teachback_submit}` read as authoritative — see [pact-completion-authority §Read-Trigger Precondition](../../protocols/pact-completion-authority.md#read-trigger-precondition). Sending the SendMessage before the metadata write lands produces false-empty raw reads on the lead side; going idle before the SendMessage strands the lead silently. Do NOT poll TaskList while idle — you cannot self-wake to do so. The team-lead's wake-signal SendMessage is the resolver.
- **Genuinely stuck**? Follow the On Blocker section.

If you have nothing to say that advances the work, say nothing.

**Outbound direction**: a `SendMessage` you send lands in the recipient's
inbox at their next idle boundary, not instantaneously. See
[Communication Charter Part I — Teammate-Side Discipline — Verify Before Acting + Assume Eventually-Seen](../../protocols/pact-communication-charter.md#teammate-side-discipline--verify-before-acting--assume-eventually-seen)
for verify-before-acting and assume-eventually-seen rules that follow from
this delivery model.

### Counter-Confirm Suppression

Before sending ANY "crossed messages", "already done", "still awaiting", or similar
state-clarification message, you MUST take a fresh disk read of the referenced task
(`status`, `blockedBy`, relevant metadata). If durable state already shows the
situation resolved — the task you would claim attention for is `completed`, the
approval or commit you would report as pending is already recorded — send NOTHING.
Durable state IS the reply: the team-lead reads the same disk. A clarification
composed from a disk read that predates the team-lead's resolution writes asserts
state that is false on arrival — pure noise that can seed a politeness loop in both
directions.

This is the already-resolved specialization of the charter's
[Verify Before Executing](../../protocols/pact-communication-charter.md#verify-before-executing)
rule: "no-op and report" still applies when the fresh read shows state has diverged
in a way the team-lead must act on; when the fresh read shows exactly the state the
team-lead themselves resolved, the report carries zero information — suppress it.

This suppression rule and [§On Wake: Disk-First Re-Read](#on-wake-disk-first-re-read-seam-agnostic)
are complements, not substitutes: disk-first fixes how you INTERPRET inbound
messages; suppression stops stale OUTBOUND noise. Applying one does not discharge
the other.

## Intentional Waiting

When your task is `in_progress` but you are legitimately idle awaiting a message
(teachback approval, inter-commit hold, peer reply, user decision, blocker
resolution), signal it via the `intentional_wait` task metadata BEFORE going idle.
This flag has a lead-side consumer: the `missed_wake_scan` hook re-surfaces tasks
idling on `awaiting_lead_completion` past the staleness threshold at the
team-lead's next user prompt or session start. The schema primitives
(`KNOWN_REASONS`, `KNOWN_RESOLVERS`, `wait_stale`) in `shared.intentional_wait`
define the teammate-facing metadata contract for protocol-defined waits. Using the flag documents the wait intent for the team-lead's TaskGet
inspection and for post-hoc session review.

### SET — before going idle

```python
from datetime import datetime, timezone
TaskUpdate(taskId=taskId, metadata={
    "intentional_wait": {
        "reason": "awaiting_teachback_approved",
        "expected_resolver": "lead",
        "since": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
})
```

`since` must be tz-aware ISO-8601. A naive timestamp fails `validate_wait` and will be surfaced as malformed to any reader of the flag (team-lead TaskGet, audit, future consumers). Fail-loud.

### CLEAR — when the wait resolves

```python
TaskUpdate(taskId=taskId, metadata={"intentional_wait": None})
```

Clear on the same turn you take the action that advances state (e.g., when the approval / commit confirmation / peer reply / user decision arrives).

### On Wake: Disk-First Re-Read (Seam-Agnostic)

This rule fires on EVERY wake while you hold ANY `intentional_wait` — every reason
(`awaiting_lead_completion`, `awaiting_lead_commit`, `awaiting_teachback_approved`,
and all others), every seam — and, more generally, whenever ANY inbound directive
could have crossed a turn you had in flight. It applies identically under in-process
and tmux teammateMode: the race is message-delivery ordering, not mode-specific.
Inbox delivery is asynchronous, so a wake message can trail the durable write it
describes, arrive after an unrelated message, or describe a scope your in-flight
work predates.

1. **Re-read durable state FIRST.** Before acting on any wake-message content,
   re-read your gate/work tasks from disk — `status`, `blockedBy`, current
   description, and the relevant metadata keys (`teachback_rejection`,
   `handoff_rejection`, or whichever key your wait names). Use the raw task file
   (`~/.claude/tasks/{team_name}/{taskId}.json` via the Read tool) — `TaskGet` does
   not surface metadata.
2. **Durable state is authoritative; message content is advisory confirmation.** If
   durable state shows your wait resolved — the gate task `completed`, a commit
   confirmation implied by task state, a rejection record present — CLEAR the wait
   and proceed immediately, even if no wake message describing the resolution is
   visible yet. Do not wait for the message that durable state has already made
   redundant.
3. **If durable state shows the wait unresolved, keep waiting.** A wake that
   resolves nothing (a crossed or redundant message, a peer ping) gets no reply —
   return to idle silently per §Idle Discipline and §Counter-Confirm Suppression.
   If the wake ASSERTS a resolution the disk does not yet show (e.g., "rejected —
   see metadata" but the metadata read returns empty), the durable write may still
   be in flight: re-read once after a brief pause; never act on a single empty
   read; if still empty, keep waiting — the team-lead's follow-up confirm covers
   the crossed case.
4. **Crossed mid-turn directives reconcile the same way.** If an inbound directive
   (a scope change, a correction) could have crossed work you had in flight — your
   teachback or HANDOFF was being composed when it was sent — re-read the task's
   CURRENT description and metadata from disk and act on the current state, not on
   the state your in-flight work assumed. If your already-submitted deliverable
   reflects the pre-directive scope, revise it on the same task without waiting to
   be asked.

This rule generalizes the wake-then-raw-read flow of §On Rejection to every wait
resolution, and it is what makes the team-lead's wake message redundant-by-design:
ordering-immune at every seam, with no hook required (a synchronous wake-detection
hook cannot exist — see the non-goal note in
[pact-completion-authority](../../protocols/pact-completion-authority.md#crossed-wake-idles-one-redundant-confirm-then-stop)).
The no-poll discipline is unchanged: you still cannot poll while idle; this rule
fires ON wake, whatever woke you.

### Vocabulary

| Field | Required | Accepted values |
|-------|----------|-----------------|
| `reason` | yes | Non-empty string. Prefer `KNOWN_REASONS` from `shared.intentional_wait`: `awaiting_teachback_approved`, `awaiting_lead_commit`, `awaiting_amendment_review`, `awaiting_post_handoff_decision`, `awaiting_peer_response`, `awaiting_user_decision`, `awaiting_blocker_resolution`. Free-form permitted. |
| `expected_resolver` | yes | Non-empty string. Prefer `KNOWN_RESOLVERS`: `lead`, `peer`, `user`, `external`. Free-form permitted. |
| `since` | yes | tz-aware ISO-8601 UTC timestamp, seconds precision. |

Unknown keys are preserved (forward-compat).

### Staleness safeguard

The `wait_stale` primitive in `shared.intentional_wait` considers the flag stale after 30
minutes from `since`. The `missed_wake_scan` hook surfaces `awaiting_lead_completion` waits stale past
this threshold to the team-lead; for all other reasons the flag is advisory
metadata the team-lead may inspect via TaskGet. If your wait genuinely takes longer, re-SET with a fresh `since` so
later inspection reflects the real duration.

### When NOT to set

- **Consultant mode** (no owned `in_progress` task): the flag has no current consumer for consultants anyway.
- **Waits < 30 seconds**: SET+CLEAR bookkeeping isn't worth it for brief waits.
- **Completion gating**: the flag does NOT suppress the team-lead's HANDOFF-presence check. An empty or missing `metadata.handoff` will be flagged by the team-lead's TaskGet verification — store your HANDOFF before marking the task completed, regardless of intentional_wait state.

## Consultant Mode

When your active task is done and no follow-up tasks are available:
- You are a **consultant** — remain available for questions
- Respond to `SendMessage` questions from other teammates
- Do NOT seek new work outside your domain
- Do NOT proactively message unless you spot a problem relevant to active work

## On Blocker

If you cannot proceed:

1. **Stop work immediately**
2. **`SendMessage`** the blocker to the team-lead:
   ```
   SendMessage(to="team-lead",
     message="[{sender}→team-lead] BLOCKER: {description of what is blocking you}\n\nPartial HANDOFF:\n...",
     summary="BLOCKER: [brief description]")
   ```
3. Provide a partial HANDOFF with whatever work you completed
4. Wait for team-lead's response or new instructions

A blocker report is a protocol-boundary message: run the
[Boundary-Drain Rule](#boundary-drain-rule) before composing it — a mid-turn
directive may already resolve or re-scope the blocker.

Do not attempt to work around the blocker.

## Algedonic Signals

When you detect a viability threat (security, data integrity, ethics):

1. **Stop work immediately**
2. **`SendMessage`** the signal to the team-lead:
   ```
   SendMessage(to="team-lead",
     message="[{sender}→team-lead] ⚠️ ALGEDONIC [HALT|ALERT]: {Category}\n\nIssue: ...\nEvidence: ...\nImpact: ...\nRecommended Action: ...\n\nPartial HANDOFF:\n...",
     summary="ALGEDONIC [HALT|ALERT]: [category]")
   ```
3. Provide a partial HANDOFF with whatever work you completed

These bypass normal triage. See the [algedonic protocol](../../protocols/algedonic.md) for trigger categories and severity guidance.

## Variety Signals

If task complexity differs significantly from what was delegated:
- "Simpler than expected" — Note in handoff; team-lead may simplify remaining work
- "More complex than expected" — Escalate if scope change >20%, or note for team-lead

## Bash Commands in ~/.claude/ Paths

When running Bash commands that touch `~/.claude/` paths, use simple standalone commands — one per Bash call. Do **not** add redirects (`2>/dev/null`), compound operators (`;`, `&&`, `||`), pipe chains (`|`), or command substitution (`` `...` ``, `$(...)`). Claude Code's Bash permission patterns are fragile and may not match compound commands, causing unnecessary permission prompts.

## Before Completing

Before returning your final output:

1. **Save Domain Learnings to Agent Memory**: Save knowledge that future instances of your specialist type would benefit from:
   - File locations and codepaths discovered
   - Framework conventions and patterns observed
   - Debugging tricks and workarounds found
   - Library quirks or version-specific behaviors

   **What goes where** (heuristics):
   - "Would a different agent type need this?" → Yes: include in HANDOFF. No: agent memory.
   - "Is this about the project or about the craft?" → Project decisions/rationale: HANDOFF. Craft patterns/techniques: agent memory.

   Examples: file locations, framework conventions → agent memory. Architectural decisions, cross-cutting concerns → HANDOFF.

   Save concise notes to your persistent memory directory (`~/.claude/agent-memory/<your-name>/`) as you discover codepaths, patterns, and key decisions. If a memory file may be written by concurrent same-named instances across teams, namespace per team (a `## team={your team_id}` section) so instances don't clobber each other. For **project-wide institutional knowledge**, include it in your HANDOFF — the secretary will review and save it to pact-memory.

   If you're working without an assigned task (no HANDOFF will be collected), message the secretary directly to save significant decisions or non-obvious discoveries: `SendMessage(to="secretary", message="[{your-name}→secretary] Save: {what you learned and why it matters}", summary="Save request: {topic}")`

2. **Confirm Memory Saved**: After saving domain learnings, set `memory_saved: true` in your task metadata:
   ```
   TaskUpdate(taskId, metadata={"memory_saved": true})
   ```

## Shutdown

When you receive a `shutdown_request`:

| Situation | Response |
|-----------|----------|
| Idle, consultant with no active questions, or domain no longer relevant | Approve |
| Mid-task, awaiting response, or remediation may need your input | Reject with reason |

> **Save memory before approving**: If you haven't saved domain learnings to your agent memory yet, do so before approving — your process terminates on approval.

## Completion Integrity (SACROSANCT)

Only report work as ready for team-lead-review if you actually performed the changes. Never fabricate a completion HANDOFF; the team-lead inspects `metadata.handoff` before transitioning status to `completed`. If files don't exist, can't be edited, or tools fail, report a BLOCKER via `SendMessage` — never invent results.

**Do not create git commits.** All staging and committing is the team-lead's responsibility. Your job ends at the HANDOFF.
