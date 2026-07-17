<!--
  spec/binding-langchain.md — prospective binding of the PACT protocol onto
  the LangChain stack (LangGraph runtime, LangChain agent middleware, Deep
  Agents harness). One row per active requirement key of spec/pact-protocol.md
  with a confidence tier and evidence citation per row. Consumed by readers
  assessing protocol portability and by scripts/verify-spec-closure.sh
  (dual-binding closure against the requirement index).
-->

# Prospective Binding — PACT Protocol on the LangChain Stack

## Binding target and version pins

- **Specification**: PACT protocol specification, version **0.1.0**.
- **Substrate**: the LangChain Python stack —
  - `langgraph` **1.2.9** (graph runtime, checkpointers, Store),
  - `langchain` **1.3.14** (agent loop, middleware),
  - `deepagents` **0.6.12** (PyPI release) + repository HEAD SHA
    `d46a2cb033b8195f440f68de744e75874b6f8e6f` (dual pin: the release version
    is primary; the SHA disambiguates which repository state was read).
- **Evidence retrieval date**: **2026-07-16** for every documentation and
  source citation in this document (see Sources).
- **Kind**: *prospective* binding (specification §1.1) — this document
  proposes a mapping with per-row confidence tiers (§3.6); no realization is
  audited here. Where a row needs a concrete design choice to be assessable,
  the choice is stated in the row or in the substrate model below and is a
  proposal, not shipped behavior.

## Substrate model

How the six abstract interfaces (specification §2) land on this stack. Rows
below refer to these constructs by name.

- **TaskStore** — typed state channels of a coordination graph
  (`StateGraph` over TypedDict schemas), persisted by a checkpointer at
  every super-step; this binding pins `durability="sync"` so a committed
  super-step is durable before the next step starts. Work, gate, and signal
  records are keyed entries in a `tasks` channel merged by a **validating
  task reducer** (a per-key reducer that admits or refuses an update before
  merge). Records that must outlive a thread live in the cross-thread Store
  (`BaseStore`) under team-scoped namespaces.
- **MessageChannel** — shared state channels with additive reducers;
  targeted dispatch via `Send(node, payload)`; combined update-plus-routing
  via `Command(update=..., goto=...)`; child-to-parent transfer via
  `Command(graph=Command.PARENT, ...)`. Channel effects commit at
  super-step boundaries: "message delivered" is observable as "a checkpoint
  containing the write exists".
- **Principal Channel** (specification §1.1) — `interrupt(payload)` pauses
  the thread and surfaces the payload to the external invoker;
  `Command(resume=value)` delivers the Principal's reply. Resume events can
  be originated only by the external invoker, never by a node.
- **Journal** — an application journal in Store namespaces (one namespace
  per team) written by an append-only writer helper, with per-thread
  checkpoint history (`get_state_history`, ULID checkpoint ids, parent
  lineage) as the replay substrate.
- **EnforcementPoint** — the agent middleware stack: `before_*`/`after_*`
  hooks, `wrap_model_call`/`wrap_tool_call` and their async variants,
  `HumanInTheLoopMiddleware` for Principal-decision gates; plus graph
  topology (conditional edges), subgraph schema isolation, and Deep Agents
  filesystem permission rules. Composition order is deterministic and
  documented: `before_*` in registration order, `wrap_*` nested with the
  first middleware outermost, `after_*` in reverse order. Within the agent
  loop every Specialist action is a tool call, so tool-boundary middleware
  intercepts the complete Specialist action surface by construction.
  Caveat carried from the substrate survey: any gate realized with
  `interrupt()` re-runs its node from the beginning on resume, so all
  pre-interrupt side effects inside gate nodes must be idempotent (or sit
  behind `@task`-wrapped operations, which are independently checkpointed
  and skipped on resume).
- **MemoryStore** — `BaseStore` with a semantic index
  (`index={"embed": ..., "dims": ..., "fields": [...]}`), hierarchical
  namespace tuples, and `search(namespace_prefix, query=, filter=, ...)`.
- **ActorLifecycle** — Specialist spawn as isolated-context subagent
  invocation (the Deep Agents `task` delegation shape: fresh context per
  invocation, autonomous run, single final report) or as subgraph
  invocation; node start/finish/error events observable via
  `stream_mode="tasks"` (requires a checkpointer); resume via invoking a
  thread's checkpoint config.

## Binding Profile

Applicability predicates (specification §3.4) exhibited by the LangGraph
substrate, each derived from the evidence base rather than assumed:

| Predicate | Holds | Substrate derivation |
|---|---|---|
| `pull-only-waiters` | FALSE | A waiting actor on this substrate is an interrupted thread or an unscheduled node; resumption is pushed to it — `Command(resume=value)` re-animates the interrupted thread, and the runtime schedules nodes when channel updates commit. No actor learns of state changes by re-reading shared state while suspended (interrupts; graph-api) |
| `out-of-band-signaling` | FALSE | The only actor-to-actor channels are state channels, and channel effects commit at super-step boundaries in the same checkpoint as any state they advertise — delivery is ordered with durable-state writes by construction under the pinned `durability="sync"`. Streaming and tracing are external observation planes, not actor-to-actor signal channels (checkpointers; streaming) |
| `broadcast-channel` | TRUE | A single write to a shared state channel is visible to every node whose schema includes that channel at its next execution; additive reducers give one-write-to-all-readers semantics (graph-api) |
| `preemptive-interrupt` | FALSE | No documented mechanism lets one actor interrupt another mid-action: `interrupt()` is invoked by the running node itself, and nodes run to completion within their super-step (interrupts; graph-api) |
| `native-write-restriction` | FALSE | No access-control API exists on state channels — "a node can write to any state channel in the graph state"; write restriction is achievable only through structural patterns (subgraph schema isolation, validating reducers), which the affected rows name explicitly (graph-api, verbatim) |

**Profile consequences.** Both predicate-conditional requirements (L1-CA-04,
L1-CA-06 — conditional on `pull-only-waiters`) are `not-applicable` on this
profile: their constrained entity, a pull-only waiter, is absent. With
`out-of-band-signaling` FALSE, the RC-1 violating interleaving is
unrepresentable here, and its unconditional keys (L1-CA-03, L1-CA-05) apply
— their constrained entities all exist on this profile — and are carried
`clean (structural)` per the §3.5 discriminator (specification §8,
structural notes; see Finding 1).

**Declared values** (declaration-class requirements discharge in this
declaration, per specification §3.4):

| Declaration | Proposed value |
|---|---|
| Workflow productions (L2-PG-01) | The §5.1 catalog: base cycle, phase-less concurrent dispatch, plan-mode, nested cycle, blocker triage |
| Gate-exempt actor classes (L1-DP-03) | {Secretary} — the optional memory actor of specification §1.2 |
| Self-completion exemptions (L1-CA-02) | Records carrying a signal-record metadata predicate; the Secretary actor class |
| Rejection-cycle threshold (L1-TB-05) | 3 consecutive rejection cycles on one gate record |
| Method-reconstruction floor (L1-TB-06) | Variety total 11 — the lower bound of the plan-mode routing band, as L2-VS-02 recommends |
| Stall retry bound (L1-ST-02) | 2 re-dispatches, then ALERT |
| Wait staleness threshold (L1-WT-03) | 30 minutes |
| Skip reason set (L2-PS-01) | {approved-plan, out-of-scope, superseded, duplicate} |
| Plan-completeness signal set (L2-PS-02) | The §5.4 minimum set: unchecked research items, placeholder table cells, forward references, unchecked questions, placeholder text, open research questions, research-shaped implementation items |
| Concurrent-observation engagement (L2-AU-01) | Engaged for every implementation-bearing production — the base cycle's implementation phase, nested cycles, and phase-less concurrent dispatch when the dispatched work is implementation-bearing; plan-mode carries no implementation. Skip decisions require the durable justification of the AU-01 row |
| Implementation action classes (L1-TB-03) | File-mutation and execution tool classes of the Specialist tool set |
| File-class predicate (L3-DL-01) | Path-prefix rule: implementation artifacts are all paths outside the coordination namespace, routed and permissioned per backend |
| Policy classes (L3-PC-01) | Security, quality, ethics, delegation, Principal approval, integrity — each mapped to a signal class |
| Change-integration action class (L3-QG-01) | The declared integration tool(s); integration occurs only through them (see the QG-01 row) |
| Durability mode | `durability="sync"` on every coordination-graph invocation |

## Reading the rows

- **Columns**: Key; Requirement (summary — current specification text,
  abbreviated); Proposed binding; Confidence (`clean` /
  `plausible-with-pattern` / `gap`, specification §3.6); Annotation
  (`structural` where the substrate makes violation impossible by
  construction, per §3.5's discrimination rule; otherwise empty); Evidence
  (citation for `clean`, named pattern for `plausible-with-pattern`,
  what-was-searched for `gap`).
- **Citations** are page slugs resolved in Sources, all retrieved
  2026-07-16. `mw-source` cites shipped source at the pinned `langchain`
  1.3.14 release: `langchain/agents/middleware/types.py`.
- The two front-loaded survey verdicts recur across rows: **no native
  channel ACL** (write restriction is structural-pattern-only) and
  **`interrupt()` re-runs its node on resume** (gate nodes must keep
  pre-interrupt effects idempotent).

## Level 1 — Coordination (§4)

| Key | Requirement (summary) | Proposed binding | Confidence | Annotation | Evidence |
|---|---|---|---|---|---|
| L1-TS-01 | Lifecycle exactly pending, in_progress, completed; no extra states | Work records as keyed entries in the tasks channel with a status field; the validating task reducer admits only the three transitions of the lifecycle table and refuses any other status value or transition | plausible-with-pattern | | Pattern: owner-mediated validating reducer — reducers are per-key merge functions that can validate or discard updates (graph-api reducers; survey Verdict A pattern table) |
| L1-TS-02 | Non-happy-path ends are terminal transitions plus metadata annotations | Outcome class and reason carried as metadata fields on the task entry, written in the same reducer-merged update as the terminal transition; no extra lifecycle states exist in the record shape | clean | | Typed state channels persist arbitrary application fields per super-step (graph-api state schemas; checkpointers) |
| L1-TS-03 | Claim is owner-only with an empty dependency set | The validating task reducer refuses a pending-to-in_progress update unless the update's writer token matches the record owner and the record's dependency list is empty at merge time | plausible-with-pattern | | Pattern: owner-mediated reducer with writer token — merge-time policy, every write passes through it (graph-api reducers; survey Verdict A) |
| L1-TS-04 | Metadata durable across turn and termination; full-fidelity reads | Channel writes are checkpointed at every super-step under the pinned sync durability and outlive every node execution and actor teardown; `get_state` returns full channel values including metadata to any holder of the thread config | clean | structural | Checkpoint-per-super-step with sync durability persists before the next step; `get_state` returns a full StateSnapshot — a committed write that fails to survive is unrepresentable at this storage layer (checkpointers) |
| L1-DP-01 | Dispatch creates gate plus work records with a dependency edge | The Lead dispatch node writes both records into the tasks channel in one update — one super-step, one checkpoint — with the work entry's dependency list naming the gate entry | clean | | Single-node state update commits atomically in one checkpoint (checkpointers super-step semantics; graph-api) |
| L1-DP-02 | Both records exist and visible at dispatch; work pre-assigned, unclaimed until gate terminal | Same atomic dispatch write sets owner on the work record at creation; visibility via the shared tasks channel; the unclaimed-until-gate-terminal clause is enforced by the same claim guard as L1-TS-03 (dependency on the gate record is only empty once the gate completes) | plausible-with-pattern | | Pattern: atomic dual-record write (clean substrate part) plus the validating-reducer claim guard for the hold-until-terminal clause (graph-api reducers; checkpointers) |
| L1-DP-03 | Gate exemptions are a declared, enumerable actor-class set | Declared in this profile's Declared values table; the dispatch node consults the set by dispatch-time actor class. Declaration-class requirement — discharged in the conformance declaration per §3.4 | clean | | Nodes are arbitrary application code consulting a declared constant; the declaration itself is this document (graph-api nodes; §3.4 discharge rule) |
| L1-TB-01 | Teachback in gate metadata, valid against the teachback schema | Specialist writes the teachback payload to the gate entry's metadata; the writing node validates against the teachback JSON schema before submitting the update, refusing invalid payloads loudly | clean | | Nodes run arbitrary Python (schema validation in-node); typed channel metadata persists the payload (graph-api) |
| L1-TB-02 | Work claim preceded by Lead gate acceptance | The gate entry's terminal transition is Lead-only (same mechanism as L1-CA-01); the work record's dependency on the gate plus the L1-TS-03 claim guard make the claim impossible before that acceptance event | plausible-with-pattern | | Pattern: dependency-gated claim through the validating reducer, stacked on the L1-CA-01 write-restriction pattern (graph-api reducers; use-subgraphs) |
| L1-TB-03 | No implementation before teachback recorded and accepted | Implementation tool classes (declared above) are gated at the tool boundary: `awrap_tool_call` middleware reads the gate record from `request.state` and short-circuits with a deny ToolMessage while the paired gate is non-terminal. Reads for understanding are ungated. The SHOULD-strength pre-action enforcement is thereby realized mechanically | clean | | `awrap_tool_call` handler contract: "Can skip calling it to short-circuit"; request carries state access ("Access state via `request.state`") (mw-source, pinned 1.3.14; wrap-tool-call-ref) |
| L1-TB-04 | Rejection keeps gate open; revision on same record with incremented counter | Rejection appends a rejection record to the gate entry's metadata via an additive list reducer; the gate status stays in_progress; the revised teachback overwrites the submission field with the counter incremented — the validating reducer refuses a terminal transition carrying an unresolved rejection | plausible-with-pattern | | Pattern: additive rejection list plus validating-reducer terminal guard (graph-api reducers) |
| L1-TB-05 | Declared rejection-cycle threshold escalates as ALERT | Threshold declared above (3); the Lead node counts rejection records on the gate entry and, at threshold, writes an ALERT signal record and routes via a conditional edge to signal handling | clean | | Conditional edges and Command routing are documented control flow; the count is application code over channel state (graph-api conditional edges and Command) |
| L1-TB-06 | Declared variety floor for required method reconstruction | Floor declared above (11, the plan-mode band lower bound); the teachback validator of L1-TB-01 additionally requires the method-reconstruction payload when the dispatch stamp's total is at or above the floor. Declaration-class component discharged here per §3.4 | clean | | In-node validation over the dispatch record's variety stamp; declaration in this document (graph-api nodes; §3.4 discharge rule) |
| L1-CA-01 | Terminal transition on Specialist work is Lead-only | Specialists execute inside subgraphs whose state schemas exclude the tasks channel's status surface: they write HANDOFFs and proposals to channels their schema shares, and the terminal status write exists only at the Lead graph level. Non-shared parent channels are unreachable from subgraph code | plausible-with-pattern | structural | Pattern: subgraph schema isolation — the survey ranks it "Structural — strong": keys exclusive to the parent are not addressable from inside the subgraph, so the violating write is unrepresentable in Specialist code under this topology (use-subgraphs; survey Verdict A) |
| L1-CA-02 | Self-completion exemptions enumerable, never ad hoc | Declared above (signal-record metadata predicate; Secretary actor class); the validating reducer consults the declared set when admitting a terminal transition not originated by the Lead. Declaration-class requirement discharged in this declaration per §3.4 | clean | | Declared enumerable set in this document; reducer consultation is application code (graph-api reducers; §3.4 discharge rule) |
| L1-CA-03 | Durable write precedes any signal advertising it | No ordering discipline is needed: an advertising signal on this substrate is itself a channel write, and it commits in the same checkpoint transaction as the content it advertises. A signal observable before its write is unrepresentable — there is no out-of-band channel for it to travel on | clean | structural | Channel effects commit at super-step boundaries; "message delivered" is observable as "checkpoint containing the write exists" (checkpointers; survey MessageChannel timing note; cf. specification §8 RC-1 structural note) |
| L1-CA-04 | Wake precedes or accompanies state change visible to pull-only waiter | Not applicable — the profile lacks `pull-only-waiters`: resumption is pushed (`Command(resume=value)` re-animates the interrupted thread; the runtime schedules nodes on committed updates), so no pull-only waiter exists to protect | clean | | Predicate citation per §3.4; derivation in the Binding Profile (interrupts; graph-api) |
| L1-CA-05 | Durable state authoritative; no acting on a single empty read | Message content and durable state coincide here: the only messages an actor can observe are channel values in the committed checkpoint its execution reads. A read that observes an advertising signal observes the advertised write in the same snapshot, so the empty-read-after-signal hazard is unrepresentable | clean | structural | Nodes read committed channel state at execution start; signal and payload share the checkpoint (graph-api; checkpointers; cf. specification §8 RC-1 and RC-2 structural notes) |
| L1-CA-06 | Acceptance and rejection are paired write-plus-wake acts | Not applicable — the profile lacks `pull-only-waiters` (same derivation as L1-CA-04): resolution writes commit and the affected actor is resumed or scheduled by the runtime itself, so no separate wake act exists to pair | clean | | Predicate citation per §3.4; derivation in the Binding Profile (interrupts; graph-api) |
| L1-CA-07 | Rejection records validate against the rejection schema | The rejection-writing node validates the payload (reason, corrections, timestamp, revision counter) against the rejection JSON schema before the metadata append, refusing invalid records loudly | clean | | In-node JSON-schema validation; typed metadata persistence (graph-api) |
| L1-HO-01 | Completion claim is a schema-valid HANDOFF with prioritized uncertainty | HANDOFF payload written to the work entry's metadata and validated in-node against the handoff schema, including the closed uncertainty-priority enum and the explicit-empty-uncertainty rule | clean | | In-node JSON-schema validation; typed metadata persistence (graph-api) |
| L1-HO-02 | HANDOFF only when work complete and verification run or declared inapplicable | Topological ordering: the HANDOFF-writing node sits behind the verification node on a conditional edge that admits it only on verification success or an explicit inapplicability declaration; verification operations are `@task`-wrapped so resumes skip completed work instead of re-running it | plausible-with-pattern | | Pattern: verification-gated conditional edge plus `@task` independent checkpointing (graph-api conditional edges and tasks-in-nodes; survey Verdict B mitigation) |
| L1-HO-03 | No fabricated completions; tool failure routes to a blocker | Tool-boundary middleware intercepts tool errors (`awrap_tool_call` observes the failure) and returns a Command routing to blocker-signal emission instead of allowing a completion path; the conduct half binds to the Specialist mission | plausible-with-pattern | | Pattern: middleware error interception with Command routing — wrap hooks may retry, observe results, and return Command (wrap-tool-call-ref; mw-source) |
| L1-SG-01 | Three-class taxonomy; payloads validate against the signal schema | Signal records as typed entries in a signals channel with class field from {blocker, ALERT, HALT}; the emitting helper validates against the signal JSON schema before the write | clean | | Typed channel entries plus in-node validation (graph-api) |
| L1-SG-02 | Permissionless emission; ALERT and HALT reach the Principal unsuppressed | Emission is permissionless by the substrate's own no-ACL property: any node can write the signals channel. Principal surfacing: `Command(graph=Command.PARENT)` carries the signal past nested coordinators to the top graph, whose signal node raises `interrupt(signal)` on the Principal Channel; the additive signals reducer makes suppression-by-overwrite impossible | clean | | Any node can write any channel (graph-api, verbatim — the no-ACL verdict working in favor); child-to-parent Command and interrupt surfacing documented (graph-api Command.PARENT; interrupts) |
| L1-SG-03 | ALERT blocks phase scope, HALT feature scope; signals persist to Principal resolution | Signal records enter the tasks channel with dependency edges onto the phase (ALERT) or feature (HALT) record; the claim and routing guards refuse progress on blocked records; signal entries are checkpointed and cleared only by a Principal-resolution write from the Principal Channel | plausible-with-pattern | | Pattern: dependency-edge blocking through the validating reducer and conditional-edge routing guards (graph-api reducers and conditional edges) |
| L1-SG-04 | HALT resumes on Principal acknowledgment; overrides recorded on the signal record, non-carrying | HALT raises `interrupt(signal)`; resumption requires `Command(resume=acknowledgment)` which only the external invoker can originate; the resolution node writes acknowledgment or override-with-justification onto the signal record's metadata; re-signal logic re-emits on a materialized overridden risk. Pre-interrupt effects in the HALT node are kept idempotent per the survey's re-run verdict | clean | | interrupt pauses and Command(resume) delivers the externally-originated reply (interrupts); signal-record metadata write per L1-TS-04 mechanics (checkpointers) |
| L1-MC-01 | Sent is not received; receipt only via recipient's subsequent durable writes | The substrate offers no read-receipt primitive to misuse: a send is a channel write, delivery is the recipient's next scheduled read of committed state, and the only receipt observable is the recipient's own subsequent checkpointed writes | clean | structural | The survey's timing model — delivery is observable only as "checkpoint containing the write exists"; no receipt operation exists in the channel inventory (survey MessageChannel section; graph-api; checkpointers) |
| L1-MC-02 | Boundary messages reflect prior-delivered directives (declared best-effort check) | The declared reconciliation check is the node-start committed-state read: a node composing a protocol-boundary message reads the directives channel in the same snapshot, so everything committed before the node began is reflected. Same-super-step crossings from parallel nodes are merged durably by the reducers and reconciled at the next node execution | clean | | Nodes read committed channel state at execution start; concurrent sibling writes merge via reducers into the durable record (graph-api state and reducers; checkpointers) |
| L1-MC-03 | Verify durable state before executing a channel-received directive | Intrinsic to the read model: a directive arrives as durable state, and the node acting on it reads directive and current state in one consistent committed snapshot — there is no directive channel separate from durable state to act on unverified | clean | structural | Single-snapshot node reads of committed channel state (graph-api; checkpointers) |
| L1-MC-04 | No content mistakable for Principal input; sender-attributed messages | Principal input exists only as Principal Channel events (`Command(resume=...)`), which no node can originate — agent content is channel-separated from Principal input by construction. Agent messages carry a sender field in their typed payload, stamped by the emitting helper | clean | | Resume values are delivered by the external invoker only (interrupts); typed payload fields carry attribution (graph-api state schemas) |
| L1-MC-05 | Suppress assertions already fully represented in durable state (SHOULD) | The emitting node compares the would-be assertion against the same committed snapshot it reads anyway and skips emission when the recipient-visible state already carries the content; conduct half binds to actor missions | plausible-with-pattern | | Pattern: snapshot-diff conditional emission in node code over documented state reads (graph-api) |
| L1-WT-01 | Waits observable in TaskStore with reason, resolver, tz-aware timestamp | A waiting actor declares the wait as a typed wait record on its task entry (reason, expected resolver, timezone-aware timestamp) in the same update that suspends via `interrupt(payload)`; the suspension itself is additionally checkpoint-observable (`get_state` shows the pending interrupt and next nodes). Pre-interrupt effects in the wait-declaring node are kept idempotent per the survey's re-run verdict | clean | | interrupt surfaces its payload and the paused thread state is inspectable (interrupts; checkpointers; cf. specification §8 RC-4 note — suspension is itself observable here) |
| L1-WT-02 | Naive-timestamp waits rejected or surfaced as malformed | The wait-declaring helper validates timezone-awareness before writing and raises on a naive timestamp; the validating reducer refuses a wait record lacking timezone information — fail-loud on both paths | clean | | In-node and reducer validation are application code over documented primitives (graph-api reducers) |
| L1-WT-03 | Staleness path re-surfaces old waits to the expected resolver | A wait registry in a Store namespace mirrors open wait records; a sweep in the coordination graph's `before_agent` hook (running once per invocation) re-surfaces entries older than the declared threshold to the expected resolver's channel. No native timer exists; the sweep rides protocol activity | plausible-with-pattern | | Pattern: Store-namespace registry plus before_agent sweep (stores; middleware — before_agent runs once per invocation) |
| L1-ST-01 | Stall diagnosis from substrate observables; declared waits excluded | Diagnosis reads `stream_mode="tasks"` events (per-node start, finish, error — checkpointer-backed) and checkpoint recency per thread; a non-terminal record with no task events or checkpoint advance, and no wait record per L1-WT-01, diagnoses a stall. Interrupt records exclude declared waits from the evidence by construction of the check | plausible-with-pattern | | Pattern: tasks-event and checkpoint-recency probe with wait-record exclusion (streaming — tasks mode events including errors; checkpointers) |
| L1-ST-02 | Recovery terminates with stall annotation; bounded retries then ALERT | Recovery uses the administrative write path: `update_state` applies the terminal transition with stall annotation and reason onto the stalled record; a retry counter on the mission record bounds re-dispatch at the declared value (2), then the Lead emits an ALERT signal record | clean | | update_state is the documented external and administrative state-update operation; counters are record fields (graph-api update_state; checkpointers) |

## Level 2 — Phases and Variety (§5)

| Key | Requirement (summary) | Proposed binding | Confidence | Annotation | Evidence |
|---|---|---|---|---|---|
| L2-PG-01 | Declared productions; every phase a work record | Productions declared in this profile's Declared values table, each realized as a compiled graph (or subgraph) whose phase nodes write phase work records into the tasks channel on entry | clean | | StateGraph composition and per-node state writes (graph-api); declaration discharged here per §3.4 |
| L2-PG-02 | Nesting depth at most one; nested results report via HANDOFF | A nested cycle runs as a subgraph invoked from one phase node; a depth counter in the shared schema is checked at nested-cycle entry and refuses depth beyond one; results return via `Command(graph=Command.PARENT, update=handoff)` into the parent's channels. Signals bypass per L1-SG-02 | clean | | Subgraph invocation and child-to-parent Command are documented (use-subgraphs; graph-api Command.PARENT) |
| L2-VS-01 | Four dimensions scored 1-4; total recorded in the work record's variety stamp | The dispatch node computes the four integer dimensions and writes them with their sum into the variety stamp on the work record at creation (the L2-VA-01 stamp), validated in-node against the 1-4 and 4-16 ranges | clean | | Typed record fields with in-node range validation (graph-api state schemas) |
| L2-VS-02 | Routing bands per variety total | A conditional edge at the routing node maps the stamped total onto the declared production graphs per the band table; the method-reconstruction floor declared above equals the plan-mode lower bound as the key recommends | clean | | add_conditional_edges routing on state values (graph-api conditional edges) |
| L2-VA-01 | Fresh per-dispatch variety stamp with per-dimension rationales | The dispatch node scores each dispatch afresh — the stamp is computed in the dispatch code path with no read of any enclosing assessment — and writes four scores, four one-sentence rationales, and the total onto the work record at creation | clean | | Same record-creation write as L1-DP-01; freshness is a property of the dispatch code path (graph-api) |
| L2-VA-02 | Non-affirmative acknowledgment durably resolved before gate acceptance | The validating reducer refuses the gate entry's terminal transition while the teachback's variety acknowledgment is non-affirmative and no resolution record (revised stamp or rejection) exists on the record | plausible-with-pattern | | Pattern: validating-reducer terminal guard over acknowledgment and resolution fields (graph-api reducers) |
| L2-PS-01 | No skip without durable annotation with declared reason | The skip transition (pending to completed) is admitted by the validating reducer only when the update carries a skip annotation with a reason from the declared set above | plausible-with-pattern | | Pattern: validating-reducer transition guard with closed reason set (graph-api reducers) |
| L2-PS-02 | Plan-completeness check before plan-justified skip; outcome recorded in the skip annotation | The skip path for approved-plan reasons runs a text scan of the plan section (read through the virtual filesystem tools) applying the declared signal set, and writes the check outcome into the L2-PS-01 skip annotation in the same update | clean | | Plan text readable via Deep Agents filesystem tools (deepagents-overview — read_file, grep); scan is node code; annotation write per L1-TS-02 mechanics |
| L2-AU-01 | Observation engagement declared; skips justified in TaskStore or Journal | Engagement is declared per production in this document; when engaged, the observer runs as a parallel node (or an external consumer of `graph.stream`) reading implementation channels as super-steps commit; a skip decision writes its justification onto the production's work record and a journal event | clean | | Live event feed via stream modes including per-node updates and results (streaming); durable justification write per L1-TS-04 and L4-JR-01 mechanics |
| L2-AU-02 | Verdict on observer's own work record; absence means not-yet-written | The observer writes graded verdicts onto its own task entry (the observer holds its own work record per L2-PG-01); consumers read presence-or-absence of the verdict field and treat absence as not-yet-written — the field simply does not exist until authored, so absence is never a findings claim | clean | | Record-scoped metadata field presence semantics over full-fidelity reads (graph-api; checkpointers get_state) |

## Level 3 — Governance (§6)

| Key | Requirement (summary) | Proposed binding | Confidence | Annotation | Evidence |
|---|---|---|---|---|---|
| L3-PC-01 | Declared policy classes with per-class escalation behavior | Declared in this profile's Declared values table with each class mapped to its signal class and escalation route. Declaration-class requirement discharged in this declaration per §3.4 | clean | | Declaration is this document; routing rides L1-SG mechanics (§3.4 discharge rule) |
| L3-PC-02 | Duty to emit the matching signal on recognition | Conduct requirement bound into every actor mission (recognition thresholds are declared guidance); the emission path is guaranteed available by the substrate's no-ACL property — any node can always write the signals channel, so a recognizing actor is never blocked from emitting | plausible-with-pattern | | Pattern: mission-bound conduct rule with structurally-available emission path (graph-api — any node writes any channel; survey Verdict A) |
| L3-PC-03 | Security class enforced mechanically at an EnforcementPoint (SHOULD) | Security-policy denial at the tool boundary: `awrap_tool_call` middleware inspects security-classed actions (for example secret-bearing artifacts at the change-integration tool) and short-circuits with a deny ToolMessage without invoking the handler | clean | | "Can skip calling it to short-circuit" — the async wrap variant's handler contract (mw-source, pinned 1.3.14); deny-with-feedback shape also documented for the human-decision path (hitl — reject skips the tool and injects feedback) |
| L3-IR-01 | Irreversible integration actions preceded by a Principal Channel confirmation event | `HumanInTheLoopMiddleware(interrupt_on={integration tools})` pauses after the model proposes the call and before it executes; the decision arrives as a Principal Channel event (`Command(resume={"decisions": [...]})`, originatable only by the external invoker); free-form message text never reaches the decision path. The interrupt and resume decisions are checkpointed, satisfying the evidence SHOULD. Pre-interrupt effects are idempotent per the survey's re-run verdict | clean | structural | HITL middleware pauses before tool execution with decisions approve, edit, reject, respond; checkpointer required; resume delivered via Command (hitl; interrupts) — with the gate configured, executing the action without a Principal Channel event is unrepresentable |
| L3-DL-01 | Lead never implements; declared file-class boundary | The Lead agent's tool set excludes all implementation tool classes, and the Deep Agents pre-backend permissions layer denies the Lead write access outside the coordination namespace — an explicit first-match deny rule over the declared path-prefix predicate (the no-match default is allow, so the deny is stated, never implied). Permissions cover the built-in filesystem tools only; the tool-set exclusion closes every other write path. A denied write cannot execute | clean | structural | Permissions are a layer evaluated before the backend is called, enforced in middleware before tool execution: `FilesystemPermission(operations, paths, mode)` via the `permissions` parameter, first-match-wins evaluation (deepagents-permissions, live re-fetch; deepagents-backends) — under this configuration the violating write is unrepresentable |
| L3-QG-01 | Change-integration gate (EnforcementPoint interception) rejects known-failing verification | The change-integration action class is the declared integration tool set, and Specialist actions are tool calls by construction of the agent loop — so `awrap_tool_call` interception covers the class completely: the gate middleware reads the change's verification status from `request.state` and short-circuits a deny while it is known-failing or unrun-without-declaration. No new applicability predicate is needed: the §2.4 interception the key presumes is exactly this documented middleware surface | clean | | Tool-boundary interception with state access and short-circuit deny (mw-source — awrap_tool_call, "Access state via `request.state`", "Can skip calling it to short-circuit"; wrap-tool-call-ref); scope condition: integration occurs only through the declared tools, per the Declared values table |
| L3-SC-01 | Concurrent write boundaries recorded durably before covered work begins | Each concurrent Specialist's write boundary is its subgraph state schema; the dispatch node records the boundary declaration (schema channel list or declared sequencing) into the covered work records' metadata in the dispatch super-step — before any covered Specialist node runs. The schemas then also enforce what was declared | clean | | Boundary recording is the L1-DP-01 dispatch write (checkpointers atomic super-step); schema-scoped subgraph channels are documented (use-subgraphs) |
| L3-SC-02 | Scope contracts recorded durably before sub-scope work; fulfillment reported in HANDOFF | The sub-scope contract (identity, deliverables, interfaces, constraints including shared files) is a typed metadata payload written on the sub-scope's work record at dispatch, before the sub-scope subgraph is invoked; the completion HANDOFF schema carries a fulfillment section reporting against it | clean | | Dispatch-time metadata write per L1-DP-01 mechanics; HANDOFF payload per L1-HO-01 (graph-api; checkpointers) |
| L3-SC-03 | Sibling merge conflict escalates as ALERT | The consolidation channel's reducer detects conflicting sibling writes (two sub-scope updates addressing the same declared-exclusive key) and, instead of silently merging, records the conflict and triggers ALERT emission on the consolidation node's next execution | plausible-with-pattern | | Pattern: conflict-detecting reducer — reducers see every incoming update and can tag rather than overwrite (graph-api reducers; survey Verdict A reducer pattern) |
| L3-OB-01 | Overwrites preserve the authored verdict in the same record, visibly | The verdict field on the observer's work record is governed by an additive list reducer: an overwrite attempt appends a new entry rather than replacing the authored one, so the original verdict persists in the same substrate record and the appended entry is itself the visible overwrite marker | clean | structural | Additive merge is the documented reducer behavior for append-governed channels — silent replacement is unrepresentable under a list-append reducer (graph-api reducers, add_messages additive semantics) |

## Level 4 — Memory (§7)

| Key | Requirement (summary) | Proposed binding | Confidence | Annotation | Evidence |
|---|---|---|---|---|---|
| L4-JR-01 | Append-only journal survives context loss, GC, teardown; versioned typed entries | The journal is a per-team Store namespace written by an append-only writer helper (new key per event: monotonic timestamp plus sequence; no update or delete calls in the writer); Store backends are cross-thread and outlive graph state, checkpoint garbage collection, and team teardown. Each entry carries schema version, event type, and UTC timestamp. Append-only is a writer convention — the Store API itself permits overwrite, and checkpoint history admits synthetic states via update_state, so no tamper-evidence is claimed | plausible-with-pattern | | Pattern: append-only writer convention over durable cross-thread Store namespaces (stores — put, get, list_namespaces; survey Journal section — append-only rated approximate on this stack) |
| L4-JR-02 | Typed events per declared registry; time-filtered parseable reads | Event types and per-type required fields live in a declared registry constant consulted by the writer helper; reads use Store search over the journal namespace with filter on type and timestamp fields, returning parseable typed batches | clean | | Store search carries namespace-prefix, filter, limit, and offset parameters (stores) |
| L4-JR-03 | Acceptance emits a Journal event carrying the HANDOFF | The Lead acceptance node performs the terminal transition and writes the journal event carrying the accepted HANDOFF payload in the same node execution — one super-step, so completion and journaling commit together and completed work survives task-channel garbage collection in the journal | clean | | Node-scoped Store writes via runtime.store plus state update in one execution (stores — runtime access from any node; checkpointers) |
| L4-MS-01 | Typed, entity-tagged, semantically queryable, additive-merge memory | BaseStore with semantic index for typed records; entity tagging via namespace tuples and indexed fields; semantic query via store search with relevance-ranked results. Additive list-merge updates are realized by a read-modify-write helper that merges lists additively and refuses updates whose target key or list position is ambiguous — the Store's own put is last-write-wins, so the additive and refusal semantics live in the helper | plausible-with-pattern | | Pattern: additive-merge helper over documented get and put; semantic search and namespaces are documented capabilities (stores — index with embed and fields, search with query) |
| L4-HV-01 | Harvest on protocol events, not wall-clock timers alone | Harvest nodes are wired into the production graphs after phase-completion and consolidation nodes (graph edges are the triggers), and an orphan-recovery sweep runs in the coordination graph's `before_agent` hook at session start — all activity-driven, no wall-clock scheduler anywhere in the path | clean | | Graph-edge triggering and once-per-invocation before_agent hook (graph-api; middleware) |
| L4-HV-02 | Journal-preferred harvest reads with per-team dedup tracking | The harvest node reads accepted HANDOFFs from the journal namespace (L4-JR-03 events) in preference to the tasks channel, and records processed event keys in a per-team processed-tracking Store namespace consulted before each save | clean | | Namespace-scoped Store reads and writes with prefix search (stores) |
| L4-SR-01 | Workflow state reconstructible from the Journal alone | Workflow state on this substrate never lives in actor context: phase progress and paused state are channel values in checkpoints, accepted HANDOFFs are journal events, and `get_state_history` replays any thread from durable records. After total actor context loss, reconstruction reads the journal namespace plus checkpoint history — in-context memory is not a storage tier here at all | clean | structural | Checkpoint-per-super-step persistence, thread history, and replay are the documented recovery model (checkpointers; use-time-travel); the violating dependence on actor context is unrepresentable because context is never where state lives |

## Findings (informative)

Observations produced by writing this binding — the exam results, recorded
for the specification's maintainers:

1. **L1-CA-03 and L1-CA-05 stay unconditional — resolved by the §3.5
   discriminator.** This binding originally flagged a tension: the hazard
   model framed the RC-1 trailing-write hazard as one that could not occur
   without `out-of-band-signaling`, yet both keys are unconditional. §3.5's
   discriminating question — what the requirement constrains — resolves it:
   these keys constrain writes, signals, and reads, entities that all exist
   on this profile; only the violating interleaving is ruled out by the
   substrate's construction. Per §3.5 such keys apply and are recorded
   `satisfied (structural)` — the correct recording even where hazard prose
   observes that the violating interleaving cannot occur — and §8's RC-1
   note now carries the same framing ("Cannot be violated"). The
   `clean (structural)` rows above are therefore the sanctioned recording
   and no Applicability lines are needed: `not-applicable` stays reserved
   for keys whose constrained entity is absent from the profile, which on
   this substrate is the L1-CA-04 and L1-CA-06 case (no pull-only waiter
   exists for a wake-ordering requirement to govern).
2. **The substrate's documented gaps are not load-bearing.** The capability
   survey records three gap-tier substrate capabilities — cross-thread and
   cross-graph transactions, store TTL, and live duplex messaging within a
   step — and no keyed requirement needs any of them. Every row above lands
   `clean` or `plausible-with-pattern`; this binding contains zero gap-tier
   rows, not by generosity but because the protocol's invariants are phrased
   over interfaces this substrate has (the closest call is L4-JR-01, where
   append-only survives as a writer convention rather than a primitive).
3. **The no-ACL property cuts both ways.** "A node can write to any state
   channel" costs this substrate `native-write-restriction` and pushes every
   write-authority row (L1-TS-03, L1-CA-01, L1-TB-02) onto structural
   patterns — but the same property is what makes L1-SG-02's permissionless
   emission structurally available. One substrate fact, opposite signs on
   different rows.
4. **Every interrupt-based gate inherits the idempotency obligation.** The
   node re-run verdict (resume restarts the interrupted node from the top)
   applies to L1-SG-04, L1-WT-01, and L3-IR-01 alike: pre-interrupt side
   effects in gate nodes must be idempotent or `@task`-wrapped. A realization
   audit of this binding should test exactly that on each gate node.
5. **Declaration-class discharge works as specified.** Fourteen of the
   fifteen declared values in the Binding Profile discharge the
   declaration-class components of L1-DP-03, L1-CA-02, L1-TB-03, L1-TB-05,
   L1-TB-06, L1-ST-02, L1-WT-03, L2-PG-01, L2-PS-01, L2-PS-02, L2-AU-01,
   L3-DL-01, L3-PC-01, and L3-QG-01 (the fifteenth, the durability-mode
   pin, is a profile declaration with no key of its own) without any of
   them needing a runtime mechanism row — the §3.4 discharge sentence
   carried its weight here.

## Sources

All retrieved 2026-07-16 (the capability-survey retrieval date; pins above)
unless marked as a live re-fetch, retrieved 2026-07-17.

| Slug | Source |
|---|---|
| graph-api | docs.langchain.com/oss/python/langgraph/graph-api — state, schemas, reducers, Command, Send, tasks-in-nodes |
| interrupts | docs.langchain.com/oss/python/langgraph/interrupts — interrupt and resume semantics, node re-run on resume |
| use-subgraphs | docs.langchain.com/oss/python/langgraph/use-subgraphs — subgraph schema isolation and checkpointing |
| checkpointers | docs.langchain.com/oss/python/langgraph/checkpointers — super-step checkpoints, durability modes, pending writes, get_state |
| use-time-travel | docs.langchain.com/oss/python/langgraph/use-time-travel — replay and fork semantics |
| stores | docs.langchain.com/oss/python/langgraph/stores — Store API, semantic search, namespaces, backends |
| streaming | docs.langchain.com/oss/python/langgraph/streaming — stream modes including tasks and checkpoints |
| middleware | docs.langchain.com/oss/python/langchain/middleware and .../middleware/custom — hook inventory, ordering, signatures |
| hitl | docs.langchain.com/oss/python/langchain/human-in-the-loop — decision set including reject semantics, checkpointer requirement |
| wrap-tool-call-ref | reference.langchain.com/python/langchain/agents/middleware/types/wrap_tool_call — tool-wrap reference |
| deepagents-overview | docs.langchain.com/oss/python/deepagents/overview — harness, task delegation, filesystem tools |
| deepagents-backends | docs.langchain.com/oss/python/deepagents/backends — backends and composite path routing; permissions "are evaluated before the backend is called" (live re-fetch 2026-07-17) |
| deepagents-permissions | docs.langchain.com/oss/python/deepagents/permissions — pre-backend permission layer: FilesystemPermission(operations, paths, mode) on the permissions parameter, first-match-wins, no-match default allow, built-in filesystem tools only (live re-fetch 2026-07-17) |
| mw-source | langchain 1.3.14 (pinned release), `langchain/agents/middleware/types.py` — `awrap_tool_call` handler contract ("Can skip calling it to short-circuit"; "Access state via `request.state`"); the synchronous `wrap_tool_call` docstring does not carry the skip sentence, so programmatic denial cites the async variant specifically |
