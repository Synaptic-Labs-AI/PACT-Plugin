<!--
  spec/annex-claude-code.md — as-built conformance annex for the Claude Code
  PACT plugin. One row per active requirement key of spec/pact-protocol.md,
  auditing the plugin realization row-by-row. Consumed by readers assessing
  the plugin's conformance and by scripts/verify-spec-closure.sh (dual-binding
  closure against the requirement index).
-->

# Conformance Annex — PACT Plugin for Claude Code

## Audited realization

- **Realization**: PACT plugin for Claude Code, version **4.6.8**, commit `b4041ccf`.
- **Fork point**: this is the realization the specification was extracted
  from (specification version 0.1.0, fork point restated in
  `spec/README.md` and `spec/pact-protocol.md` §0). The audited version and
  the fork point coincide: this annex records extraction-time
  synchronization, and later plugin versions require re-audit.
- **Citations**: paths in the Realizing-mechanism column are relative to
  the plugin root (`pact-plugin/`). Code files are cited as realizing
  mechanisms wherever the mechanism is code-enforced; protocol and skill
  documents are cited where the mechanism is instruction-enforced.

## Binding Profile

Applicability predicates (specification §3.4) exhibited by the Claude Code
substrate, declared here so that every `not-applicable` citation resolves
within this document:

| Predicate | Holds | Substrate evidence |
|---|---|---|
| `pull-only-waiters` | TRUE | Dependency resolution is computed at query time; nothing pushes a resumption to an idle waiter (`protocols/pact-completion-authority.md`) |
| `out-of-band-signaling` | TRUE | Agent messaging delivers independently of task-file and journal writes; delivery order is not guaranteed against durable-state visibility |
| `broadcast-channel` | FALSE | One send reaches one recipient; HALT fan-out is per-recipient iteration (`protocols/algedonic.md`) |
| `preemptive-interrupt` | FALSE | Only the Principal can interrupt an agent mid-action; no agent-initiated preemption exists |
| `native-write-restriction` | FALSE | The substrate cannot restrict which actor writes a task record or field; write authority is realized by instruction and post-hoc detection |

Both predicate-conditional Level 1 requirements (L1-CA-04, L1-CA-06 —
conditional on `pull-only-waiters`) apply to this profile. No requirement
in the audited set is `not-applicable` on this substrate: the hazards the
protocol guards against are all real here. Symmetrically, no row carries
the `structural` annotation — this substrate makes none of the audited
violations unrepresentable, which is why the realization is
compensating-ritual-heavy; the deviations recorded below are where those
rituals fall short of, or exceed, the keyed requirement.

## Level 1 — Coordination (§4)

| Key | Requirement (summary) | Realizing mechanism | Status | Annotation | Deviation / predicate / issue |
|---|---|---|---|---|---|
| L1-TS-01 | Lifecycle is exactly pending, in_progress, completed; no extra states | `protocols/pact-task-hierarchy.md` state table (three statuses, no failure states) | satisfied | | |
| L1-TS-02 | Non-happy-path ends are terminal transitions plus metadata annotations | `protocols/pact-agent-stall.md` termination markers (`stalled`, `failed`, `blocked`, `terminated`, `skipped`, each with `reason`) | satisfied | | |
| L1-TS-03 | Claim is owner-only and requires an empty dependency set | `protocols/pact-task-hierarchy.md` status-by-actor table; dependency emptiness computed at query time | satisfied | | |
| L1-TS-04 | Metadata is durable and readable in full fidelity by authorized actors | Platform task files persist metadata across turns and terminations; raw task-file reads are the documented read path (`protocols/pact-completion-authority.md`) | satisfied-with-deviation | | The platform task-read tool is metadata-blind; full-fidelity reads are realized by the documented raw-task-file read ritual, not by the native read operation |
| L1-DP-01 | Dispatch creates a gate record plus a work record with a dependency edge | `commands/orchestrate.md` dispatch flow; `skills/pact-agent-teams` (gate task and work task, work blocked by gate) | satisfied | | |
| L1-DP-02 | Both records exist at dispatch, visible; work pre-assigned, unclaimed until gate terminal | Same dispatch flow: both tasks created at dispatch time, work task owner set at creation and left pending until the gate completes | satisfied | | |
| L1-DP-03 | Gate exemptions are a declared, enumerable actor-class set | `hooks/shared/intentional_wait.py` `TEACHBACK_EXEMPT_AGENT_TYPES`, resolved by dispatch-time agent type | satisfied | | |
| L1-TB-01 | Teachback recorded in gate metadata and valid against the teachback schema | `hooks/shared/teachback_schema.py` (field, enum, and sub-key SSOT matching the schema); `hooks/task_lifecycle_gate.py` write-time shape advisories; lead-side completion-time schema gate | satisfied | | |
| L1-TB-02 | Work claim is preceded by Lead gate acceptance | Dependency edge from work to gate; the Lead's gate completion is the acceptance event (`protocols/pact-completion-authority.md`) | satisfied | | |
| L1-TB-03 | No implementation before teachback is recorded and accepted | `skills/pact-teachback` ordering rule (no implementation tool use before storage and acceptance; understanding reads permitted) | satisfied | | The SHOULD-strength pre-action enforcement is not realized: ordering is instruction-enforced, with write-time advisories on the teachback payload only |
| L1-TB-04 | Rejection keeps the gate open; revision on the same record, counter incremented | `protocols/pact-completion-authority.md` rejection flow; `skills/pact-agent-teams` on-rejection procedure (same task, `revision_number` incremented) | satisfied | | |
| L1-TB-05 | Declared rejection-cycle threshold escalates as ALERT | Declared threshold 3; three or more cycles escalate to imPACT META-BLOCK, an ALERT-class triage (`protocols/pact-completion-authority.md`, `protocols/pact-variety.md`) | satisfied | | |
| L1-TB-06 | Declared variety floor at or above which method reconstruction is required | `hooks/shared/variety_scorer.py` `PLAN_MODE_MIN` (11); band table in `skills/pact-teachback` | satisfied | | |
| L1-CA-01 | Terminal transition on Specialist work is Lead-only | `protocols/pact-completion-authority.md` (lead-only flip); `hooks/task_lifecycle_gate.py` self-completion advisory with `completion_disputed` writeback | satisfied-with-deviation | | No pre-action write restriction exists on this substrate (`native-write-restriction` FALSE); a violating self-completion is detected post-hoc and durably disputed, not prevented |
| L1-CA-02 | Self-completion exemptions are enumerable, never ad hoc | `hooks/shared/intentional_wait.py` `is_self_complete_exempt`: signal-task metadata predicate, exempt agent-type set, and the Lead force-termination marker | satisfied | | |
| L1-CA-03 | Durable write precedes any signal advertising it | Specialist-side ordering invariant: metadata write first, notify second, wait flag third (`skills/pact-agent-teams`, `skills/pact-teachback`) | satisfied | | |
| L1-CA-04 | Wake signal precedes or accompanies state changes visible to pull-only waiters | Lead-side resolution ordering: wake message paired with, and sent no later than, the status flip it announces (`protocols/pact-completion-authority.md`) | satisfied | | Applies: profile exhibits `pull-only-waiters` |
| L1-CA-05 | Durable state authoritative; never act on a single empty read | Disk-first re-read on wake; single-empty-read prohibition (`protocols/pact-completion-authority.md` read-trigger precondition; `skills/pact-agent-teams`) | satisfied | | |
| L1-CA-06 | Acceptance and rejection are paired write-plus-wake acts | Paired durable write and wake message on both resolution paths; neither alone completes the resolution (`protocols/pact-completion-authority.md`) | satisfied | | Applies: profile exhibits `pull-only-waiters` |
| L1-CA-07 | Rejection records validate against the rejection schema | Rejection shape (`reason`, `corrections`, `since`, `revision_number`) defined in `protocols/pact-completion-authority.md` and `skills/pact-agent-teams` | satisfied-with-deviation | | Prose-only shape: no code validator enforces the rejection schema as-built; the documented shape matches the schema and Lead-side review is the compensating mechanism |
| L1-HO-01 | Completion claim is a schema-valid HANDOFF with prioritized uncertainty | `skills/pact-agent-teams` HANDOFF format; `hooks/task_lifecycle_gate.py` required-field completeness check | satisfied-with-deviation | | Realization is stricter than the schema: the gate's required-field list includes `reasoning_chain`, which the schema leaves optional; the check is advisory severity, so absence flags but does not reject |
| L1-HO-02 | HANDOFF recorded only when the claimed work is complete and verified | `skills/pact-agent-teams` verification precondition (edits complete, deliverables persisted, tests run or declared inapplicable before the HANDOFF write) | satisfied | | |
| L1-HO-03 | No fabricated completions; tool failure routes to a blocker signal | `skills/pact-agent-teams` completion-integrity rule (SACROSANCT) with the blocker route on tool or environment failure | satisfied | | |
| L1-SG-01 | Three-class signal taxonomy with schema-valid payloads | `protocols/algedonic.md` taxonomy (blocker, ALERT, HALT) and payload templates; `hooks/shared/constants.py` `SYSTEM_TASK_PREFIXES` subject conventions | satisfied-with-deviation | | Payload shapes are prose templates only: no code validator enforces the signal schema as-built; the schema is normatively stronger than the realization |
| L1-SG-02 | Permissionless emission; ALERT and HALT surface to the Principal unsuppressed | `protocols/algedonic.md` (any agent emits without permission; the coordinator is required to surface immediately, and cannot triage, delay, or suppress); nested-cycle bypass per `protocols/pact-s1-autonomy.md` | satisfied | | |
| L1-SG-03 | ALERT blocks phase scope, HALT blocks feature scope; signals persist to resolution | `protocols/algedonic.md` task-system integration: ALERT blocks the phase task, HALT blocks the feature task via dependency edges; signal tasks persist and unblock on Principal resolution | satisfied | | |
| L1-SG-04 | HALT resumes only on Principal acknowledgment; overrides logged, non-carrying | `protocols/algedonic.md` resolution options: explicit acknowledgment required; override documented with justification; a materialized overridden risk re-signals | satisfied | | |
| L1-MC-01 | Sent is not received; receipt observable only through subsequent actions | `protocols/pact-communication-charter.md` eventually-seen-not-read rule | satisfied | | |
| L1-MC-02 | Boundary messages reflect all directives delivered before composition | Boundary-drain rule with a declared drain report on every protocol-boundary message; Lead-side directive-reflection check as the declared best-effort backstop (`skills/pact-agent-teams`) | satisfied | | |
| L1-MC-03 | Verify durable state before executing state-dependent directives | Verify-before-executing rule (`protocols/pact-communication-charter.md`); disk-first re-read on wake (`skills/pact-agent-teams`) | satisfied | | |
| L1-MC-04 | No content mistakable for Principal input; sender-attributed messages | Sender-recipient message prefix convention and the message-authenticity rule (`skills/pact-agent-teams`, `protocols/pact-communication-charter.md`) | satisfied | | |
| L1-MC-05 | Suppress assertions already fully represented in durable state | Counter-confirm suppression rule (`skills/pact-agent-teams`, `protocols/pact-communication-charter.md`) | satisfied | | |
| L1-WT-01 | Waits observable in the TaskStore: reason, resolver, tz-aware timestamp | `hooks/shared/intentional_wait.py` metadata contract (`reason`, `expected_resolver`, `since`); set-before-idle discipline in `skills/pact-agent-teams` | satisfied | | |
| L1-WT-02 | Timezone-naive wait timestamps fail loud | `hooks/shared/intentional_wait.py` `validate_wait` rejects naive timestamps as malformed | satisfied | | |
| L1-WT-03 | Stale declared waits re-surface to the expected resolver | `hooks/shared/intentional_wait.py` `wait_stale` (declared 30-minute threshold); `hooks/missed_wake_scan.py` re-surfacing | satisfied-with-deviation | | Re-surfacing is implemented only for the lead-resolved wait class (`awaiting_lead_completion`); other declared waits are inspectable advisory metadata with no re-surfacing path to their resolvers |
| L1-ST-01 | Stall diagnosis from observables; declared waits and delivery artifacts excluded | `protocols/pact-agent-stall.md` indicator list over substrate observables, excluding live declared waits and wake-postdating idles | satisfied | | |
| L1-ST-02 | Recovery terminates with a stall annotation; bounded retries, then ALERT | `protocols/pact-agent-stall.md`: force-complete with stall annotation and reason; one retry, then an ALERT-class (META-BLOCK) escalation | satisfied | | |

## Level 2 — Phases and Variety (§5)

| Key | Requirement (summary) | Realizing mechanism | Status | Annotation | Deviation / predicate / issue |
|---|---|---|---|---|---|
| L2-PG-01 | Productions declared; every executing phase is a work record | `protocols/pact-workflows.md` production catalog (base cycle, comPACT, plan-mode, rePACT, imPACT, scoped productions); per-phase tasks under the phase subject convention (`hooks/shared/constants.py`) | satisfied | | |
| L2-PG-02 | Nesting depth at most one; nested cycles report to the parent via HANDOFF | `protocols/pact-s1-autonomy.md` nesting limit (1 level maximum); `commands/rePACT.md` (maximum nesting 1 level; results reported to the parent via HANDOFF); nested-cycle signal bypass per `protocols/algedonic.md` | satisfied | | As-built pins the same literal depth-1 cap the requirement keys; nothing in the realization treats the cap as a binding-declared value |
| L2-VS-01 | Exactly four dimensions, integers 1 to 4, total 4 to 16 | `hooks/shared/variety_scorer.py` (novelty, scope, uncertainty, risk; per-dimension and total bounds enforced) | satisfied | | |
| L2-VS-02 | Routing follows the four-band variety table | `hooks/shared/variety_scorer.py` band constants and `route_workflow`; cut values match the normative table exactly | satisfied | | |
| L2-VA-01 | Fresh per-dispatch stamp: four scores, four rationales, total | `protocols/pact-variety.md` per-dispatch stamping rule (scored afresh, neither inherited nor capped); `hooks/task_lifecycle_gate.py` required-rationale completeness check | satisfied | | |
| L2-VA-02 | Non-affirmative acknowledgment gets a durable resolution before acceptance | `protocols/pact-variety.md`: a no or concern acknowledgment resolves by Lead re-stamp of the variety metadata or by teachback rejection, before gate acceptance | satisfied | | |
| L2-PS-01 | Skips only with a durable annotation and a declared reason | `protocols/pact-task-hierarchy.md` skip transition (`metadata.skipped` plus reason); declared reason set `plan_section_complete` and `structured_gate_passed` (`protocols/pact-completeness.md`) | satisfied | | |
| L2-PS-02 | Plan-justified skips preceded by a declared-signal incompleteness check | `protocols/pact-completeness.md` seven-signal set with mechanical detection guidance, consumed as a layer of the skip gate in `commands/orchestrate.md` | satisfied-with-deviation | | The check's execution leaves no durable record: only the claimed skip reason persists, so an unperformed check is indistinguishable from a performed one; the declared text-observable signal set and the layered skip flow are the compensating mechanism |
| L2-AU-01 | Observation engagement declared per production; skips visibly justified | `protocols/pact-audit.md` engagement conditions (variety at or above 7, three or more parallel coders, security-sensitive code, drift history) with a required written skip justification | satisfied | | |
| L2-AU-02 | Absent verdict means not-yet-written, never no-findings | `protocols/pact-audit.md` non-blocking rule: an absent verdict means not written yet, never no findings; no bounded read-after-write window exists | satisfied-with-deviation | | The interpretation discipline is instruction-only: no substrate observable distinguishes a consumer that inferred a clean observation from absence; the rule stated at the consumption site and the authored-verdict durability mirror are the compensating mechanism |
