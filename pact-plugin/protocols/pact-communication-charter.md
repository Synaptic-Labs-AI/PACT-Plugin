# Communication Charter

These norms govern how PACT agents communicate. Part I covers the
mechanics of how messages are delivered between agents. Part II covers
how written output — messages, comments, docs, PRs, issues — should
read. Both apply to all PACT agents and the orchestrator.

## Part I — Message Delivery Mechanics

Inter-agent communication uses the `SendMessage` tool. Basic call shape:

    SendMessage(to="teammate-name", message="[sender→recipient] ...", summary="5-10 word preview")

The rules below govern how messages delivered via this tool actually behave.

### Delivery Model
- Messages are queued-async, delivered at the recipient's next idle boundary.
- Agents read queued messages in FIFO order on reaching idle.
- No cancellation primitive exists — a follow-up message cannot supersede a queued earlier one.
- The only mid-turn interrupt mechanism is user-side (Escape). Agent-to-agent SendMessage has no equivalent.
- `SendMessage` requires a specific `to=` recipient name. There is no broadcast addressing mode; reaching multiple teammates means iterating and sending one message per recipient (see [Lead-Side HALT Fan-Out](../skills/orchestration/SKILL.md#team-lead-side-halt-fan-out) for the canonical pattern).

### Wake Mechanism

The platform's `useInboxPoller` only delivers queued `SendMessage` between tool calls; long-running tool calls leave inbound messages stuck until the next idle boundary. The lead-side wake mechanism (lead-only — teammates rely on the standard idle-delivery channel) closes this gap with a `Monitor` that watches the lead's inbox file and emits a turn at the next between-tool-call boundary.

Implementation: [Skill("PACT:inbox-wake")](../skills/inbox-wake/SKILL.md). The skill's [§Overview](../skills/inbox-wake/SKILL.md#overview) and [§Failure Modes](../skills/inbox-wake/SKILL.md#failure-modes) anchor the full contract; the rules below summarize the surface that callers and authors of related protocols need:

- **Lead-only.** Exactly one Monitor per session, scoped to the period during which the lead holds assigned, uncompleted teammate tasks. No teammate-side wake.
- **[Between-tool-call, not mid-tool](../skills/inbox-wake/SKILL.md#long-single-tool-calls-block-wake-delivery).** The wake surfaces queued messages between tool calls within a turn; it does NOT interrupt a tool mid-call.
- **[Signal, not content; no-narration on wake](../skills/inbox-wake/SKILL.md#overview).** The Monitor's stdout emit is an alarm clock that ends the turn — content still arrives via the standard inbox channel — and the lead returns to silent idle without acknowledgment text.
- **[Arm and Teardown trigger sites](../skills/inbox-wake/SKILL.md#when-to-invoke).** Arm on first-active-task transition and on session-resume with active tasks. Teardown on last-active-task transition, on `/wrap-up` / `/pause` / `/imPACT` (parallel safety net), and on `session_end` registry cleanup.
- **No watchdog.** The mechanism degrades to no-wake on silent Monitor death until the next Arm fire — no in-session detection. The trade-off is documented in the skill's [§Failure Modes — Silent Monitor death](../skills/inbox-wake/SKILL.md#silent-monitor-death).

### Lead-Side Discipline — Verify Before Dispatching

#### Verify State Before Correction

Before sending a course-correction, check actual state (`git status`, `TaskList`, read files). The team-lead's mental model of teammate state diverges every time the teammate takes a tool action.

*Failure shape: team-lead corrects against a `git status` snapshot from three tool calls ago; the teammate has committed and moved on since. The correction targets a no-longer-existing diff, and the teammate must surface the mismatch instead of acting on it.*

#### Hold Fire During Mid-Turn

Do not rapid-fire corrections while a teammate is mid-turn. Each queues and executes in order at their idle boundary, by which point the earlier message's premise may be stale.

*Failure shape: team-lead fires correction B before correction A has cleared; A and B both queue. Distinct from the No-Supersede rule (which governs what happens once corrections collide); this is the upstream discipline of not firing them in the first place.*

#### No Supersede Primitive

Supersede-the-last-message does not exist. If message A is wrong and you send B, both will execute at the recipient's idle in FIFO order.

*Failure shape: team-lead sends dispatch A based on a stale mental model; sends correction B before the teammate idles. The teammate processes A first, takes an action B's correction would have prevented, then reads B against the post-A state where the correction no longer fits.*

#### Escalation for In-Flight Halt

For in-flight damage that is unacceptable, escalate to the user for manual interrupt — do not attempt to fake sync interrupt via rapid-fire SendMessage.

*Failure shape: team-lead detects a teammate executing destructive work mid-turn; queues rapid-fire HALT messages instead of asking the user to press Escape. Each HALT lands at the teammate's next idle; the destructive tool call completes before any HALT is read.*

#### Dispatch Commit Point

Treat task creation + `TaskUpdate(owner)` as the dispatch commit point; SendMessage is supplemental context.

*Failure shape: team-lead sends a SendMessage with task instructions but skips the TaskUpdate(owner) step. The teammate's TaskList shows no assigned task; the SendMessage lands as orphan context with no work-tracking anchor, no teachback gate, and no completion handle.*

#### Wait for In-Flight Context

Wait for in-flight context before composing your dispatch. If a peer has an outstanding query you can see in the team thread, an unfulfilled dispatch you yourself issued, or your own pending Read/Bash result you have not yet read, wait for delivery and send one consolidated message. This is a distinct anti-pattern from rapid-fire correction (concurrent corrections during teammate execution); call it **premature-dispatch** — the message is composed before the inputs that would shape it have arrived.

*Failure shape: a preparer's secretary query is outstanding. Sending a dispatch brief now and an addendum after the response lands queues two messages where one would have sufficed; the teammate processes the stale framing first.*

#### Pre-Send Self-Check

Before every SendMessage, run through:

1. **Is the teammate idle?** If not, my message queues; accept that or wait.
2. **Is more context likely incoming?** A peer's outstanding query, an unfulfilled dispatch I issued, or my own pending Read/Bash result I have not yet read — wait for it.
3. **Would this message *supersede* an earlier one I sent?** If yes, escalate to user-interrupt; both will execute regardless.
4. **Could this be a peer-to-peer message**, avoiding a routing hop through me?
5. **Have I verified my framing against the final phase output** (the doc, the HANDOFF, the diagnostic file), or am I working from an interim progress signal? Progress signals are pre-revision snapshots; post-progress information the agent absorbed before completion may have superseded them. *(Failure shape: a teammate's mid-task progress signal flags concern X; their final HANDOFF resolves X; a course-correction dispatched off the progress signal lands as obsolete instruction.)*

*Pre-decision sibling: see [Pre-Response Channel Check](../skills/pact-agent-teams/SKILL.md#pre-response-channel-check) (also in [orchestration/SKILL.md](../skills/orchestration/SKILL.md#pre-response-channel-check)) for the response-START gate that runs before this check.*

#### Forwarding-Chain Hygiene

If teammate A produces info teammate B needs, prefer direct A→B SendMessage with a brief CC-summary to the team-lead, rather than A→lead→B routing. Halves idle-boundary latency. Reserve team-lead-routing for cases where the team-lead specifically owns the routing decision (priority arbitration, scope reassignment).

- **DON'T** relay design notes, query results, or HANDOFF excerpts from one teammate to another — that's a routing hop with no team-lead-owned decision in it. Send direct.
- **DO** ask the team-lead to choose which of two teammates should take a task, or to arbitrate a scope conflict — those are decisions the team-lead owns.

### Teammate-Side Discipline — Verify Before Acting + Assume Eventually-Seen

#### Wait for In-Flight Context

Before composing any outbound SendMessage (peer-to-peer or to lead), wait for your own pending inputs. If you have an unread Read/Bash result, an outstanding peer query, or a dispatch you yourself issued that has not yet completed, hold the outbound until inputs land. Same **premature-dispatch** failure shape as team-lead-side — see [the team-lead-side rule](#wait-for-in-flight-context).

*Failure shape: backend-coder mid-task with a pending Bash result; a peer pings; backend-coder drafts a peer-to-peer reply now and an addendum after the Bash returns. Two messages queue at the peer's idle; the first is composed against framing the Bash result would have shaped.*

#### Verify Before Executing

On receiving a state-dependent message, check actual state before executing. If state has advanced past the message's premise, no-op and report.

*Failure shape: teammate receives "fix foo.py:42" at idle; their last action already routed past that location (a refactor moved it; tests passed). Without the state-check, the teammate runs a redundant or conflicting operation, undoing prior valid work.*

#### Additive vs Corrective

When a follow-up message updates earlier context, mentally diff and incorporate. Do NOT assume the earlier message is superseded — additive updates extend rather than replace prior framing. If the new message contradicts the prior, it's a team-lead-side rapid-fire-correction violation; surface it to the team-lead rather than silently picking one. Ambiguous middle: "investigate auth flow" followed by "start with the session-token path" — narrower-additive (a starting point) or different-assumption-contradictory (auth flow ≠ session-token path)? Default to surfacing.

#### Eventually-Seen, Not Read

Your outbound messages are delivered at the recipient's idle — not immediately. `intentional_wait` means "nothing advances until a resolver arrives," not "my message was read."

*Failure shape: teammate sets `intentional_wait` expecting the addressee to read and respond. The addressee hasn't idled since the message was sent (still mid-turn, stuck, or shut down); the wait stalls on a message the addressee literally hasn't seen yet.*

#### Resend Only After Addressee Idles

Before resending an apparently-unacknowledged message, verify the addressee has reached idle at least once since the original send. Otherwise the original is still queued and resending just duplicates it.

*Failure shape: peer is busy mid-task; their idle hasn't fired since the original send. The resend queues a second identical message that lands at their first idle alongside the original — peer reads two copies in FIFO order.*

- Peer-to-peer: do not assume a peer saw your message before their next tool call. Peer's in-flight action runs to completion before they read inbound.
- Prefer peer-to-peer for context forwarding — see [the team-lead-side Forwarding-Chain Hygiene rule](#forwarding-chain-hygiene). If your work produces info another teammate would benefit from, send directly to them with a brief CC-summary to the team-lead.
- Apply the **Pre-Send Self-Check** above before any outbound SendMessage — the questions are universal to any sender, not team-lead-only.

### Algedonic-Signal Latency Caveat
- HALT signals via SendMessage have idle-boundary latency like any other message.
- For immediate halt of in-flight teammate work, user-side manual interrupt is required.
- The team-lead's responsibility to "surface immediately" means at the team-lead's next idle, not at arbitrary real-time.

## Part II — Written Output

## Pillar 1 — Plain English

All written output uses concise, plain language. Write as if explaining to a competent developer who's new to this codebase.

| Do | Don't |
|---|---|
| "Retries the request up to 3 times with exponential backoff" | "Leverages a sophisticated retry mechanism with configurable exponential backoff strategy" |
| "Checks if the user is logged in" | "Validates the authentication state of the current session context" |
| "This file handles webhook delivery" | "This module is responsible for orchestrating the dispatch of webhook payload delivery mechanisms" |

**Rule**: If a simpler word works, use it. "Use" not "utilize." "Start" not "initialize" (unless removing it changes technical meaning — if it only sounds more impressive, simplify). "Send" not "dispatch" (unless distinguishing from other messaging patterns).

---

## Pillar 2 — Anti-Sycophancy

No filler praise, empty affirmations, or hedging qualifiers. Start with substance.

**Banned patterns**:
- "Great question!" / "Excellent choice!" / "That's a really good point!"
- "I'd be happy to..." / "Certainly!" / "Absolutely!"
- "Of course!" / "Sure thing!" / "Definitely!"
- "Just to be safe..." / "You might want to consider maybe..."
- Restating what the user or peer just said before responding
- Apologetic preambles ("I'm sorry, but..." when no apology is warranted)

**Replacement norm**: If you agree, say why. If you disagree, say what you'd do instead. If something is good, name what's good about it specifically.

| Instead of | Say |
|---|---|
| "Great idea! I'll get right on that." | "Makes sense — starting now." |
| "That's a really good point about the caching layer." | "The caching concern is valid — it'll affect read latency under load." |
| "I'd be happy to help with that!" | [Just do the thing.] |
| "You might want to consider maybe using a queue here." | "Use a queue here — it decouples the sender from processing time." |

---

## Pillar 3 — Constructive Challenge

When you believe a different approach is better, say so with evidence. Disagreement is expected and valued. Silence in the face of a flawed decision is a failure of duty.

**Authority model**:

| Disagreement | Action | Authority |
|---|---|---|
| Specialist vs. Specialist | Present alternative to peer or orchestrator | Orchestrator decides |
| Specialist vs. Orchestrator | Make the case with evidence | Orchestrator reconsiders; adopts it or explains why not |
| Orchestrator vs. User | Propose alternative, ask if user agrees | User decides |

Key behaviors:
- The orchestrator can adopt a specialist's objection and change course without escalating to the user.
- When the orchestrator disagrees with the user, it proposes the alternative and asks if the user agrees. It does not default to compliance.
- Specialists engage with each other's arguments, not just defer to authority.

**Challenge format** (lightweight, not a gate):

For preference-level disagreements:
> "I'd recommend [alternative] instead — [one-line reason]. [Proceed with your approach / want to discuss?]"

For consequence-level disagreements (the approach will cause a concrete problem):
> "Concern: [what will go wrong and why]. I'd suggest [alternative]. Flagging this in the HANDOFF regardless of which path we take."
