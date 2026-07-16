<!--
  spec/pact-protocol.md — the PACT protocol specification (normative core).
  Single-spine document: terminology, substrate interfaces, conformance rules,
  and the keyed requirement levels L1-L4. Consumed by conformance annexes,
  prospective binding documents, and the validation scripts under scripts/.
-->

# PACT Protocol Specification

## 0. Status

**Specification version**: 0.1.0
**Status**: Draft

**Fork point**: this specification was extracted from the PACT plugin for
Claude Code, version 4.6.8, commit `b4041ccf`. From this point the
specification is versioned independently of any realization. The fork point
is restated in `spec/README.md` and in the header of every conformance
annex, so that extraction-time synchronization and independent evolution
are both explicit.

### 0.1 Document conventions

- **Requirement blocks.** Every keyed requirement appears as a block that
  opens with its key in bold (for example `**L1-TS-01**`), an em-dash, and
  a short title, optionally followed by an *Applicability* line naming a
  predicate (§3.4), then one or more normative sentences. Within the level
  sections (§4–§7), every sentence containing an uppercase BCP 14 keyword
  sits inside a requirement block; the keyword-to-key traceability scan
  relies on this.
- **Normative vs informative.** The level sections (§4–§7) and the schemas
  referenced from Appendix A are normative. Sections 1–3 define the
  vocabulary and conformance framework in descriptive prose. Section 8
  (Hazard Model) and the appendix commentary are informative.
- **Tables.** State-transition tables inside requirement blocks are
  normative. Interface operation tables in §2 are definitional. Any
  diagrams, where present, are informative renderings of the tables.

## 1. Terminology and Actors

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT",
"SHOULD", "SHOULD NOT", "RECOMMENDED", "NOT RECOMMENDED", "MAY", and
"OPTIONAL" in this document are to be interpreted as described in BCP 14
[RFC 2119] [RFC 8174] when, and only when, they appear in all capitals, as
shown here.

### 1.1 Core terms

- **Substrate** — the execution platform on which a coordination system
  runs: its storage, messaging, scheduling, and interception facilities.
- **Binding** — a mapping of this protocol's abstract interfaces and
  requirements onto a concrete substrate. A *prospective* binding proposes
  such a mapping with per-requirement confidence tiers (§3.6); an
  *as-built* binding is audited in a conformance annex (§3.3).
- **Realization** — a concrete coordination system implementing this
  protocol on a substrate. Realizations are the conformance targets of the
  keyed requirements.
- **Actor** — a participant in the protocol. The actor classes are defined
  in §1.2.
- **Work record** — a durable TaskStore record representing a unit of
  dispatched work, with an owner, a status lifecycle, metadata, and
  dependency edges.
- **Gate record** — a work record whose deliverable is a verification
  payload (a teachback, §1.3) and whose terminal transition gates a paired
  work record.
- **Signal record** — a work record representing an emitted signal
  (blocker, ALERT, or HALT); it persists the signal and carries its
  resolution state.
- **Dispatch** — the act by which the Lead creates the records for, and
  assigns a mission to, a Specialist.
- **Mission** — the task description a dispatched Specialist executes.
- **Durable write** — a write to a substrate store (TaskStore, Journal,
  MemoryStore) that survives the writing actor's turn and termination.
- **Advertising signal** — a message whose content asserts the existence
  or content of a durable write.
- **Wake signal** — an advertising signal directed at a waiting actor for
  the purpose of causing it to re-read durable state. "Wake signal" is a
  protocol term of art defined here over MessageChannel and store
  observables; it carries no scheduler semantics.
- **Pull-only waiter** — a waiting actor that can learn of state changes
  only by reading shared state; nothing pushes a resumption to it. Whether
  waiters are pull-only is a substrate property declared by the
  `pull-only-waiters` predicate (§3.4).
- **Protocol-boundary message** — a message that initiates or resolves a
  protocol-defined wait, or that carries a completion claim, a teachback
  submission, a rejection, or a blocker report.
- **Teachback** — a Specialist's pre-work restatement of its dispatched
  mission, recorded on the gate record for Lead verification.
- **HANDOFF** — a Specialist's structured completion claim, recorded in
  the work record's metadata.
- **Rejection record** — a durable record of a Lead's rejection of a
  teachback or a HANDOFF, carrying reason, corrections, timestamp, and a
  revision counter.
- **Declared wait** — a wait made observable in the TaskStore per L1-WT-01.
- **Stall** — the condition in which an actor has ceased progressing
  without a terminal message or declared wait; diagnosed per L1-ST-01.

### 1.2 Actors

- **Lead** — coordinates work; never implements; holds sole completion
  authority over Specialist-owned work records (subject to the declared
  exemptions of L1-CA-02).
- **Specialist** — executes one discipline under a mission; reports via
  HANDOFF; claims and works records it owns.
- **Secretary** — the memory actor: harvests HANDOFFs and maintains the
  MemoryStore. The Secretary is an optional actor below L4 conformance.
  Where present, its gate and completion carve-outs are declared-set
  exemptions (L1-DP-03, L1-CA-02), never ad hoc.
- **Principal** — the human authority: terminus of ALERT and HALT signals
  and confirmer of irreversible actions.

## 2. Substrate Interfaces

The protocol is phrased over six abstract substrate interfaces. A binding
maps each interface to concrete substrate mechanisms and declares which
optional capabilities and predicates (§3.4) hold. The operation lists
below are definitional: they state what the protocol assumes of each
interface. Where a substrate lacks a guarantee, the binding's profile and
the conformance vocabulary (§3) express the gap honestly instead of
forcing vacuous claims.

### 2.1 TaskStore — durable shared work records

| Operation | Guarantee |
|---|---|
| `create(record)` | Durable; visible to authorized actors |
| `read(record)` | Full-fidelity read including metadata, available to all authorized actors |
| `write-metadata(record, kv)` | Durable opaque key-value payloads; survive the writer's turn and the writer's termination |
| `status-transition(record, s1→s2)` | Atomic; single terminal state (`completed`); outcome semantics live in metadata annotations, not extra states |
| `dependency-edges(record)` | Declarable; resolution queryable. Not guaranteed: push notification on resolution (see `pull-only-waiters`, §3.4) |
| `query(filter)` | Enumerate records by owner and status |

### 2.2 MessageChannel — directed transfer between existing actors

| Operation | Guarantee |
|---|---|
| `send(actor, payload)` | Eventually delivered (eventually-seen, not read) |
| — ordering | No guaranteed ordering between channel delivery and TaskStore or Journal writes. This non-guarantee is load-bearing: it is the hazard the L1-CA invariants exist for |
| `broadcast`, `preempt`, `read-receipt` | Optional capabilities, binding-declared (see predicates, §3.4) |

### 2.3 Journal — append-only durable event log

| Operation | Guarantee |
|---|---|
| `append(typed event)` | Append-only; typed with per-type required fields; survives context loss, TaskStore garbage collection, and team teardown |
| `read-range(since, filter)` | Time-filtered, parseable batches |
| — recovery rank | Journal > TaskStore > MemoryStore by survival scope |

### 2.4 EnforcementPoint — mechanical interception of actor actions

| Operation | Guarantee |
|---|---|
| `intercept(action-class)` | Pre-action hook on declared action classes |
| `deny(action, feedback)` | The action does not execute; the actor observes structured feedback |
| `observe(action events)` | Post-action visibility |
| — composition | Multiple enforcement layers compose in a declared, deterministic order |

### 2.5 MemoryStore — cross-session queryable knowledge (L4 only)

| Operation | Guarantee |
|---|---|
| `persist(typed record)` | Durable across sessions |
| `query(semantic + filters)` | Retrieval with relevance ranking |
| `update-additive(record)` | List-merge additive semantics; ambiguous targets refused |

### 2.6 ActorLifecycle — actor creation, termination, and liveness

Lifecycle events are interface-observable and are referenced by the L1
dispatch, stall, and shutdown requirements; none of the other five
interfaces owns them (a channel to an actor that does not yet exist is
incoherent, and creating an actor is not the interception of an action).

| Operation | Guarantee | Core or optional |
|---|---|---|
| `spawn(actor-class, mission)` | Creates an actor bound to a mission; the spawn event is observable | core |
| `terminate(actor)` | Ends the actor; termination is observable (distinct from silent death) | core |
| `liveness-observe(actor)` | Some evidence channel distinguishes "working" from "gone" | core |
| `resume(actor, context)` | Re-animate with preserved context | optional (binding-declared) |

## 3. Conformance

### 3.1 Conformance targets and levels

The keyed requirements are organized in four cumulative levels:

- **L1 — Coordination** (§4): records, gates, completion authority,
  signals, channels, waits, stalls.
- **L2 — Phases and Variety** (§5): workflow grammar, variety scoring and
  routing, phase-skip protection, concurrent observation.
- **L3 — Governance** (§6): policy classes, escalation duties,
  irreversible-action confirmation, delegation and scope boundaries.
- **L4 — Memory** (§7): journal contract, memory store, harvest triggers,
  state recovery.

A realization conforms at level *n* when every active keyed requirement at
levels 1 through *n* that applies under its declared Binding Profile
(§3.4) is satisfied. Levels are sections of one document because
requirements reference across levels: the L1 teachback group consumes the
L2 variety band floor, the L3 escalation duties consume the L1 signal
vocabulary, and the L4 harvest and recovery groups consume L1 and L2
events. Deprecated keys (§3.2) do not count toward conformance.

### 3.2 Requirement identifiers

Every requirement carries a stable key matching the frozen regular
expression:

```
^L[1-4]-[A-Z]+-[0-9]{2}$
```

The level prefix is `L1`..`L4`; the mechanism group code is one or more
uppercase letters; the per-group sequence is two digits, zero-padded,
starting at `01`. The regex is frozen: group-code additions are appends to
the registry below, never regex changes. Sequence overflow past `99` in
one group indicates the group is too coarse — the group is split (new
code, appended) rather than the digit field widened.

Mechanism group codes (append-only registry; existing codes never change
meaning):

| Level | Code | Mechanism |
|---|---|---|
| L1 | TS | TaskStore lifecycle and metadata |
| L1 | DP | Dispatch shape (gate plus work pair, exemption surface) |
| L1 | TB | Teachback lifecycle |
| L1 | CA | Completion authority, wake and read semantics |
| L1 | HO | HANDOFF obligation |
| L1 | SG | Signal taxonomy (blocker, ALERT, HALT) |
| L1 | MC | MessageChannel conduct rules |
| L1 | WT | Intentional wait |
| L1 | ST | Stall detection and recovery |
| L2 | PG | Phase grammar (productions, nesting cap) |
| L2 | VS | Variety scoring and routing bands |
| L2 | VA | Per-dispatch stamping and acknowledgment loop |
| L2 | PS | Phase-skip protection |
| L2 | AU | Auditor (concurrent observer) |
| L3 | PC | Policy-class declaration and escalation duties |
| L3 | IR | Irreversible-action confirmation |
| L3 | DL | Delegation boundary |
| L3 | QG | Quality gates (no known-broken merges) |
| L3 | SC | Scope contracts and concurrent write boundaries |
| L3 | OB | Observer-verdict integrity |
| L4 | JR | Journal contract |
| L4 | MS | MemoryStore contract |
| L4 | HV | Harvest triggers |
| L4 | SR | State recovery |

**Stability policy.**

1. Keys are append-only: assigned once, in authoring order within a group;
   new requirements take the next free number in their group.
2. Keys are never renumbered and never reused. A withdrawn requirement
   keeps its key forever.
3. A withdrawn key's row remains in the requirement index (Appendix B)
   with status `deprecated`, a one-line reason, and, if replaced, a
   successor-key pointer. Deprecated keys are excluded from the
   dual-binding-closure denominator; closure tooling reads the index's
   status column rather than counting key occurrences.
4. New group codes append to the registry with level and mechanism.

### 3.3 Requirement statuses (conformance annexes)

An as-built conformance annex records one row per active key, with a
status from the closed set:

`satisfied` | `satisfied-with-deviation` | `unsatisfied` | `not-applicable`

- `unsatisfied` rows carry a tracked-issue link.
- `not-applicable` is legal only by citing a declared applicability
  predicate (§3.4) that the audited binding's profile does not exhibit.
  Free-form non-applicability is a closure failure.
- `satisfied-with-deviation` names the deviation and the compensating
  mechanism. Realization-specific compensating rituals appear here as
  evidence.

**The `structural` annotation.** `satisfied (structural)` is an annotation
on `satisfied`, not a fifth status: it records that the substrate makes
violation unrepresentable (for example, an append-only history satisfied
by the storage layer itself). Closure tooling treats it as `satisfied`.
The annotation exists to make "workarounds become primitives" portability
arguments legible in the tables without inflating the status vocabulary.

### 3.4 Binding Profiles and applicability predicates

Every binding and annex document opens with a **Binding Profile**: a
declaration of which substrate properties hold, drawn from the predicate
registry below (append-only, under the same discipline as §3.2):

| Predicate | Meaning |
|---|---|
| `pull-only-waiters` | A waiting actor cannot be pushed a resumption; it observes state by reading |
| `out-of-band-signaling` | A signal channel exists whose delivery is not ordered with durable-state writes |
| `broadcast-channel` | One send reaches all active actors |
| `preemptive-interrupt` | An actor can be interrupted mid-action by another actor |
| `native-write-restriction` | The substrate can restrict which actor writes a given record or field |

Requirements can be conditional on a predicate; a requirement block's
*Applicability* line names the predicate it is conditional on. A
requirement conditional on a predicate the profile lacks is
`not-applicable`, citing that predicate. This is the device that keeps
dual-binding closure honest instead of forcing vacuous claims.

### 3.5 Not-applicable versus structurally satisfied

The two mechanisms of §3.3 and §3.4 answer different questions and are
not interchangeable:

- A requirement whose **hazard cannot arise** on the audited substrate —
  because the requirement is conditional on a predicate the profile lacks
  — is recorded `not-applicable`, citing the predicate.
- `satisfied (structural)` is reserved for requirements that **apply and
  cannot be violated**: the hazard is real on this substrate, and the
  substrate's construction rules the violation out.

Recording a cannot-arise requirement as structurally satisfied overstates
the binding; recording an applies-but-unviolable requirement as
not-applicable understates it. Closure reviews check this distinction.

### 3.6 Confidence tiers (prospective bindings)

A prospective binding document records one row per active key with a
confidence tier from the closed set:

`clean` | `plausible-with-pattern` | `gap`

- `clean` rows cite documentation or shipped source verbatim.
- `plausible-with-pattern` rows name the documented structural pattern
  they rely on; the suffix carries an evidence obligation the bare word
  would lose.
- `gap` rows name what was searched and not found.

### 3.7 Admission criterion (informative)

A requirement enters the normative core at `MUST` strength only when its
violation causes a coordination or interoperability failure observable at
a substrate interface — a wrong state transition, a missing event, an
unvalidated payload, an unauthorized completion. Judgment guidance is
demoted to `SHOULD` strength or to informative text. Level 3 applies this
criterion most aggressively and is deliberately requirement-sparse.

## 4. Level 1 — Coordination

### 4.1 TaskStore lifecycle and metadata (TS)

The work-record state machine and the metadata surface every other L1
mechanism writes to.

**L1-TS-01** — Single terminal lifecycle

A work record's status lifecycle MUST be exactly the transitions of the
following table, with `completed` the sole terminal state; a realization
MUST NOT introduce additional lifecycle states (including failure states).

| From | To | Meaning |
|---|---|---|
| `pending` | `in_progress` | Claim (see L1-TS-03) |
| `pending` | `completed` | Skip (recorded per L1-TS-02) |
| `in_progress` | `completed` | Completion (authority per L1-CA-01) |

**L1-TS-02** — Outcome annotations, not outcome states

Every non-happy-path termination (stall, failure, blockage, forced
termination, skip) MUST be recorded as a transition to the terminal state
accompanied by a metadata annotation naming the outcome class and a
reason. Outcome semantics MUST be carried in metadata annotations rather
than in additional lifecycle states.

**L1-TS-03** — Claim discipline

The claim transition (`pending` → `in_progress`) on a work record MUST be
effected only by the record's owner, and only when the record's dependency
set is empty.

**L1-TS-04** — Metadata durability and readability

The TaskStore MUST provide durable per-record key-value metadata: a
metadata write MUST survive the writing actor's turn and the writing
actor's termination, and every authorized actor MUST be able to read a
record in full fidelity, including its metadata.

### 4.2 Dispatch shape (DP)

The gate-plus-work pair that structures every Specialist dispatch.

**L1-DP-01** — Gate-plus-work dispatch

Unless the dispatched actor's class is in the declared gate-exempt set
(L1-DP-03), every Specialist dispatch MUST create a gate record and a work
record, with a dependency edge from the work record to the gate record.

**L1-DP-02** — Dispatch-time visibility and pre-assignment

Both records MUST exist at dispatch time and be visible to the dispatched
Specialist. The work record MUST be pre-assigned (owner set at dispatch)
and MUST remain unclaimed until the gate record reaches the terminal
state.

**L1-DP-03** — Declared gate exemptions

Gate-exempt actor classes MUST be declared as an enumerable set, resolved
by dispatch-time actor class; a gate exemption MUST NOT be granted ad hoc.

### 4.3 Teachback lifecycle (TB)

Pre-work verification: the Specialist restates its mission; the Lead
accepts or rejects before implementation begins.

**L1-TB-01** — Teachback payload schema

A teachback submission MUST be recorded in the gate record's metadata and
MUST validate against the teachback schema
(`urn:pact:spec:0.1.0:teachback.schema.json`, Appendix A).

**L1-TB-02** — Acceptance precedes claim

A Specialist's work-record claim MUST be preceded by a gate-acceptance
event on the paired gate record, effected by the Lead.

**L1-TB-03** — No implementation before acceptance

A dispatched Specialist MUST NOT perform implementation actions before its
teachback is durably recorded and accepted; reads for understanding are
permitted. Where the substrate provides an EnforcementPoint, this
requirement SHOULD be enforced as a pre-action gate on the implementation
action classes.

**L1-TB-04** — Rejection keeps the gate open

A teachback rejection MUST keep the gate record in its non-terminal
claimed state and MUST append a rejection record (L1-CA-07); the revised
teachback MUST be re-submitted on the same gate record with the rejection
record's revision counter incremented.

**L1-TB-05** — Rejection-cycle escalation

A binding MUST declare a rejection-cycle threshold; when consecutive
rejection cycles on one gate record reach that threshold, the condition
MUST be escalated as an ALERT-class signal (§4.6).

**L1-TB-06** — Method-reconstruction floor

A realization that implements variety scoring (L2) MUST declare a variety
band floor at or above which a teachback submission MUST include the
method-reconstruction payload (`reasoning_reconstruction`, Appendix A);
below the declared floor the payload is optional.

### 4.4 Completion authority, wake and read semantics (CA)

Who may finish work, and the write/signal/read invariants that make
resolution survivable on substrates where message delivery is not ordered
with durable writes.

**L1-CA-01** — Lead-only completion

The terminal status transition on a Specialist-owned work record MUST be
effected only by the Lead, except under an exemption declared per
L1-CA-02.

**L1-CA-02** — Enumerable self-completion exemptions

Every self-completion exemption MUST be enumerable — a declared metadata
predicate (for example, records that are themselves signals) or a declared
actor class — and an exemption MUST NOT be granted ad hoc.

**L1-CA-03** — Durable write precedes advertisement

A durable write MUST precede any signal that advertises that write's
content.

**L1-CA-04** — Wake precedes visible state change
*Applicability: `pull-only-waiters`*

A wake signal MUST precede or accompany any state change visible to a
pull-only waiter.

**L1-CA-05** — Durable state is authoritative

An actor MUST treat durable state as authoritative over message content,
and MUST NOT act on a single empty read taken after a write-advertising
signal.

**L1-CA-06** — Paired resolution acts
*Applicability: `pull-only-waiters`*

Acceptance and rejection of a Specialist deliverable MUST each be
performed as a paired act — a durable state write and a wake signal to the
affected waiter; neither act alone MUST be treated as completing the
resolution.

**L1-CA-07** — Rejection record schema

Every rejection record — whether it rejects a teachback or a HANDOFF —
MUST validate against the rejection schema
(`urn:pact:spec:0.1.0:rejection.schema.json`, Appendix A).

### 4.5 HANDOFF obligation (HO)

The structured completion claim that carries a Specialist's work product
to the Lead and to downstream consumers.

**L1-HO-01** — HANDOFF schema and content

A Specialist's completion claim MUST be recorded as a structured HANDOFF
in the work record's metadata, validating against the handoff schema
(`urn:pact:spec:0.1.0:handoff.schema.json`, Appendix A). Uncertainty
entries MUST carry a priority from the closed set HIGH, MEDIUM, LOW, and
an empty uncertainty set MUST be declared explicitly rather than omitted.
The `reasoning_chain` field SHOULD be included for non-trivial work.

**L1-HO-02** — HANDOFF is a completion commitment

An actor MUST NOT record a HANDOFF before the work it claims is complete:
the claimed deliverables MUST be durably persisted at recording time, and
verification MUST have been run — or been explicitly declared inapplicable
within the HANDOFF — before the HANDOFF is recorded.

**L1-HO-03** — No fabricated completions

An actor MUST NOT record a HANDOFF that misrepresents its produced
deliverables. When tool or environment failure prevents completion, the
actor MUST emit a blocker signal (§4.6) instead of a completion claim.

### 4.6 Signal taxonomy (SG)

Escalation channels that bypass normal coordination when viability is
threatened.

**L1-SG-01** — Taxonomy and payload schema

A realization MUST provide a signal taxonomy with at least the three
severity classes `blocker` (coordinator-triaged), `ALERT`, and `HALT`
(both Principal-directed), and every signal payload MUST validate against
the signal schema (`urn:pact:spec:0.1.0:signal.schema.json`, Appendix A).

**L1-SG-02** — Permissionless emission and non-suppression

Any actor MUST be able to emit a signal without prior permission — the
trigger condition itself authorizes emission. ALERT and HALT signals MUST
be surfaced to the Principal without delay or suppression, bypassing every
intermediate coordinator, including the coordinators of nested cycles.

**L1-SG-03** — Scope amplification

An ALERT MUST block the current phase-scope work and a HALT MUST block the
enclosing feature-scope work. Signal records MUST persist in the TaskStore
and MUST unblock only on Principal resolution.

**L1-SG-04** — HALT resolution

Resumption after a HALT MUST require explicit Principal acknowledgment. A
Principal override MUST be recorded with risk-specific justification and
MUST NOT carry forward: if an overridden risk later materializes, it MUST
be re-signaled.

### 4.7 MessageChannel conduct (MC)

Rules of engagement for a channel whose delivery is eventual and unordered
with respect to durable writes.

**L1-MC-01** — Eventual visibility, not receipt

A sender MUST NOT treat a sent message as received; receipt is observable
only through the recipient's subsequent durable writes or protocol
actions.

**L1-MC-02** — Boundary reflection

A protocol-boundary message MUST reflect all directives delivered to its
sender before the message's composition, subject to a declared best-effort
reconciliation check.

**L1-MC-03** — Verify state before executing

An actor MUST verify current durable state before executing a
state-dependent directive received over the MessageChannel.

**L1-MC-04** — Message authenticity

An actor MUST NOT emit message content that could be mistaken for
Principal input; agent-originated messages MUST carry attributable sender
marking.

**L1-MC-05** — Suppress information-free assertions

An actor SHOULD suppress state-assertion messages whose content is already
fully represented in durable state visible to the recipient.

### 4.8 Intentional wait (WT)

Making legitimate waiting observable, so that waiting is distinguishable
from death.

**L1-WT-01** — Waits are observable

An actor that cannot proceed until an external event MUST make that
dependency observable in the TaskStore before suspending, recording at
minimum a non-empty reason, a non-empty expected resolver, and a
timezone-aware timestamp.

**L1-WT-02** — Malformed waits fail loud

A declared wait whose timestamp lacks timezone information MUST be
rejected or surfaced as malformed; it MUST NOT be silently accepted.

**L1-WT-03** — Staleness detection

A realization MUST provide a staleness-detection path that re-surfaces
declared waits older than a declared threshold to the wait's expected
resolver.

### 4.9 Stall detection and recovery (ST)

Diagnosing and recovering from actors that are gone rather than waiting.

**L1-ST-01** — Evidence-based stall diagnosis

Stall diagnosis MUST be based on substrate observables — a non-terminal
work record without channel or write activity, or actor termination
without a terminal message — and MUST exclude declared waits (L1-WT-01)
and delivery-ordering artifacts from counting as stall evidence.

**L1-ST-02** — Bounded recovery

Stall recovery MUST terminate the stalled work record via the terminal
state with a stall annotation and reason (L1-TS-02), and retries MUST be
bounded: the mission is re-dispatched at most a declared number of times,
after which the condition MUST be escalated as an ALERT-class signal
(§4.6).

## 5. Level 2 — Phases and Variety

Level 2 governs how work is shaped before it is dispatched: the grammar of
workflow productions, the variety assessment that routes work into them,
the per-dispatch stamping and acknowledgment loop, protection against
silent phase skips, and concurrent observation of implementation work.

### 5.1 Phase grammar (PG)

The protocol's workflow productions: the **base cycle** (the four phases
Prepare, Architect, Code, Test, in order); **comPACT** (phase-less
concurrent dispatch for independent, self-contained tasks); **plan-mode**
(planning-only — analysis, consultation, synthesis, presentation; no
implementation); **rePACT** (a nested cycle within one phase of a parent
cycle); and **imPACT** (triage after a blocker or repeated failure).
Realizations can extend this catalog; the requirements below apply to
whatever catalog is declared.

**L2-PG-01** — Declared productions with observable phases

A realization MUST declare its workflow productions and the phase sequence
each production executes, and every phase of an executing production MUST
be represented as a work record in the TaskStore.

**L2-PG-02** — Bounded nesting with parent reporting

A nested cycle MUST NOT exceed a nesting depth of one, and a nested cycle
MUST report its results to its parent cycle via the HANDOFF mechanism
(§4.5). Signals emitted within a nested cycle remain governed by L1-SG-02
and bypass both the nested and the parent coordinator.

### 5.2 Variety scoring and routing (VS)

**L2-VS-01** — Four-dimensional variety score

A dispatch-variety assessment MUST score exactly four dimensions —
novelty, scope, uncertainty, and risk — each as an integer from 1 to 4,
and MUST record the total as their sum (range 4 to 16).

**L2-VS-02** — Routing bands

Workflow routing MUST follow the variety total per this table:

| Variety total | Routed production |
|---|---|
| 4–6 | comPACT (phase-less concurrent dispatch) |
| 7–10 | base cycle |
| 11–14 | plan-mode, then the base cycle |
| 15–16 | research spike, then reassessment |

The method-reconstruction floor declared under L1-TB-06 SHOULD equal the
lower bound of the plan-mode band (11).

### 5.3 Per-dispatch stamping and acknowledgment (VA)

**L2-VA-01** — Fresh per-dispatch stamp

Every Specialist work record MUST be stamped at creation with a variety
assessment comprising the four dimension scores, one one-sentence
rationale per dimension, and the total; the assessment MUST be scored
afresh for that dispatch — neither inherited from nor capped by any
enclosing assessment.

**L2-VA-02** — Acknowledgment resolution

A teachback variety acknowledgment other than affirmative (a "no" or
"concern" value) MUST receive a durable resolution — a revised variety
stamp or a rejection record — before gate acceptance.

### 5.4 Phase-skip protection (PS)

**L2-PS-01** — Recorded skips only

A phase MUST NOT be skipped without a durable skip annotation on the
phase's work record (the skip transition of L1-TS-01) recording a reason
drawn from a declared reason set.

**L2-PS-02** — Plan-completeness check before skip

A skip justified by an approved plan MUST be preceded by a
text-observable incompleteness check of the corresponding plan section,
applying a declared signal set covering at least: unchecked research
items, placeholder table cells, forward references, unchecked questions,
placeholder text, open research questions, and research-shaped
implementation items.

### 5.5 Concurrent observation (AU)

An observer reads implementation output as it is produced and compares it
against the design contracts, emitting graded verdicts. The integrity of
an authored verdict against later overwrites is a governance concern keyed
in the L3 observer-verdict group.

**L2-AU-01** — Declared observation coverage

For every implementation-bearing production, the realization MUST declare
whether concurrent observation is engaged, and a decision to skip
observation where the realization's declared engagement conditions hold
MUST be justified in a durable record.

**L2-AU-02** — Verdict absence semantics

The absence of an observer verdict MUST be interpreted as "not yet
written", never as "no findings"; a consumer MUST NOT infer a clean
observation from a missing verdict.

## 6. Level 3 — Governance

> Reserved. This level's keyed requirements (groups PC, IR, DL, QG, SC,
> OB) are drafted in a subsequent stage of this document's authoring.

## 7. Level 4 — Memory

> Reserved. This level's keyed requirements (groups JR, MS, HV, SR) are
> drafted in a subsequent stage of this document's authoring.

## 8. Hazard Model (INFORMATIVE)

This section is informative. It names the race classes the L1 invariants
exist to survive, so that auditors and binding authors can reason from
hazard to requirement. Each race class arises from substrate properties,
not from any particular implementation; per-realization compensating
rituals (specific write orderings, re-read disciplines, drain-and-mark
conventions, wait flags) are recorded as evidence in the conformance
annex rows of the keyed invariants listed here, not in this document.

| Race class | Hazard | Keyed invariant(s) | Structural note |
|---|---|---|---|
| RC-1 — trailing write | An advertising signal arrives before the durable write it advertises is visible to the reader | L1-CA-03, L1-CA-05 | Cannot arise on substrates without `out-of-band-signaling`: where message delivery and state commit are the same transaction, a signal cannot outrun its write |
| RC-2 — crossed wake | A wake signal and the state change it announces are observed in inverted order | L1-CA-04, L1-CA-05, L1-CA-06 | Same structural exemption as RC-1; cannot arise where waiters are not pull-only |
| RC-3 — mid-turn directive | A directive delivered while the recipient has a turn in flight is invisible to it until a later processing boundary, so a deliverable composed during that turn can silently predate the directive | L1-MC-02, L1-MC-03 | Turn-based substrates only; step-committed substrates read committed state at every step by construction |
| RC-4 — false stall | A legitimately waiting actor is indistinguishable from a dead one, triggering wrongful recovery | L1-WT-01, L1-WT-03, L1-ST-01 | Substrates on which suspension is itself observable satisfy the wait-observability requirement structurally |

Two ordering disciplines commonly realize L1-CA-03 and L1-CA-04 on
substrates that exhibit both hazards: a write-side ordering (persist the
deliverable, then send the advertising signal, then declare the wait) and
a resolution-side ordering (send the wake signal no later than the state
flip it announces). The two orderings point in opposite directions because
they answer different hazards — RC-1 and RC-2 respectively. Neither
concrete ordering is keyed; a conformance annex records each under the
invariant it realizes.

## Appendix A — Schemas (normative by reference)

The four payload schemas are normative for payload shape only; lifecycle
and ordering semantics live in the prose requirements that reference them.
Each schema is the subject of exactly one keyed requirement.

| Schema file | `$id` | Keyed by |
|---|---|---|
| `schemas/handoff.schema.json` | `urn:pact:spec:0.1.0:handoff.schema.json` | L1-HO-01 |
| `schemas/teachback.schema.json` | `urn:pact:spec:0.1.0:teachback.schema.json` | L1-TB-01 |
| `schemas/signal.schema.json` | `urn:pact:spec:0.1.0:signal.schema.json` | L1-SG-01 |
| `schemas/rejection.schema.json` | `urn:pact:spec:0.1.0:rejection.schema.json` | L1-CA-07 |

Conventions: JSON Schema draft 2020-12; `$id` in URN form embedding the
specification version (bumping the specification version bumps every `$id`
in the same change); unknown keys are permitted by default
(`additionalProperties: true`) except where a closed sub-shape is
code-enforced at the fork point; enums are closed where code-enforced.
Fixture instances live in `schemas/fixtures/`: at least one valid and one
invalid instance per schema; each invalid twin's `$comment` names the
violated constraint.

## Appendix B — Requirement Index

The closure denominator: one row per key ever assigned. Status is `active`
or `deprecated`; deprecated rows carry a reason and, if replaced, a
successor key. Closure tooling reads this table's status column.

| Key | Level | Group | Status | Title |
|---|---|---|---|---|
| L1-TS-01 | L1 | TS | active | Single terminal lifecycle |
| L1-TS-02 | L1 | TS | active | Outcome annotations, not outcome states |
| L1-TS-03 | L1 | TS | active | Claim discipline |
| L1-TS-04 | L1 | TS | active | Metadata durability and readability |
| L1-DP-01 | L1 | DP | active | Gate-plus-work dispatch |
| L1-DP-02 | L1 | DP | active | Dispatch-time visibility and pre-assignment |
| L1-DP-03 | L1 | DP | active | Declared gate exemptions |
| L1-TB-01 | L1 | TB | active | Teachback payload schema |
| L1-TB-02 | L1 | TB | active | Acceptance precedes claim |
| L1-TB-03 | L1 | TB | active | No implementation before acceptance |
| L1-TB-04 | L1 | TB | active | Rejection keeps the gate open |
| L1-TB-05 | L1 | TB | active | Rejection-cycle escalation |
| L1-TB-06 | L1 | TB | active | Method-reconstruction floor |
| L1-CA-01 | L1 | CA | active | Lead-only completion |
| L1-CA-02 | L1 | CA | active | Enumerable self-completion exemptions |
| L1-CA-03 | L1 | CA | active | Durable write precedes advertisement |
| L1-CA-04 | L1 | CA | active | Wake precedes visible state change |
| L1-CA-05 | L1 | CA | active | Durable state is authoritative |
| L1-CA-06 | L1 | CA | active | Paired resolution acts |
| L1-CA-07 | L1 | CA | active | Rejection record schema |
| L1-HO-01 | L1 | HO | active | HANDOFF schema and content |
| L1-HO-02 | L1 | HO | active | HANDOFF is a completion commitment |
| L1-HO-03 | L1 | HO | active | No fabricated completions |
| L1-SG-01 | L1 | SG | active | Taxonomy and payload schema |
| L1-SG-02 | L1 | SG | active | Permissionless emission and non-suppression |
| L1-SG-03 | L1 | SG | active | Scope amplification |
| L1-SG-04 | L1 | SG | active | HALT resolution |
| L1-MC-01 | L1 | MC | active | Eventual visibility, not receipt |
| L1-MC-02 | L1 | MC | active | Boundary reflection |
| L1-MC-03 | L1 | MC | active | Verify state before executing |
| L1-MC-04 | L1 | MC | active | Message authenticity |
| L1-MC-05 | L1 | MC | active | Suppress information-free assertions |
| L1-WT-01 | L1 | WT | active | Waits are observable |
| L1-WT-02 | L1 | WT | active | Malformed waits fail loud |
| L1-WT-03 | L1 | WT | active | Staleness detection |
| L1-ST-01 | L1 | ST | active | Evidence-based stall diagnosis |
| L1-ST-02 | L1 | ST | active | Bounded recovery |
| L2-PG-01 | L2 | PG | active | Declared productions with observable phases |
| L2-PG-02 | L2 | PG | active | Bounded nesting with parent reporting |
| L2-VS-01 | L2 | VS | active | Four-dimensional variety score |
| L2-VS-02 | L2 | VS | active | Routing bands |
| L2-VA-01 | L2 | VA | active | Fresh per-dispatch stamp |
| L2-VA-02 | L2 | VA | active | Acknowledgment resolution |
| L2-PS-01 | L2 | PS | active | Recorded skips only |
| L2-PS-02 | L2 | PS | active | Plan-completeness check before skip |
| L2-AU-01 | L2 | AU | active | Declared observation coverage |
| L2-AU-02 | L2 | AU | active | Verdict absence semantics |
