<!--
  spec/README.md — orientation for the PACT protocol specification.
  Points readers at the normative core (protocol.md), the schemas,
  and the conformance model. The fork-point declaration below is mirrored
  in protocol.md §0 and in every conformance annex header.
-->

# PACT Protocol Specification

A framework-agnostic specification of the PACT multi-agent coordination
protocol: how a Lead dispatches Specialists, how work is gated, claimed,
completed, and escalated, and how the coordination state survives the
races and failures of real substrates.

The protocol was first realized as an agent-orchestration plugin; this
specification abstracts that realization so the same coordination
semantics can be audited on — or ported to — other substrates.

## Documents

| File | Role |
|---|---|
| `protocol.md` | **Normative core.** Terminology, the six substrate interfaces, conformance rules, and the keyed requirement levels L1–L4 |
| `schemas/*.schema.json` | Normative payload shapes (HANDOFF, teachback, signal, rejection), referenced by keyed requirements |
| `schemas/fixtures/` | Valid and invalid example instances for every schema |

Conformance documents accompany the specification: an *as-built annex*
audits an existing realization row-by-row against the requirement index,
and a *prospective binding* maps the requirements onto a new substrate
with per-row confidence tiers.

## The four levels

Conformance is cumulative — a realization conforms at level *n* by
satisfying all applicable requirements of levels 1 through *n*:

| Level | Scope |
|---|---|
| **L1 — Coordination** | Work records and their lifecycle, dispatch gating, teachback verification, completion authority, write/signal/read ordering invariants, signals, channel conduct, observable waits, stall recovery |
| **L2 — Phases and Variety** | Workflow grammar, variety scoring and routing, phase-skip protection, concurrent observation |
| **L3 — Governance** | Policy classes, escalation duties, irreversible-action confirmation, delegation and scope boundaries |
| **L4 — Memory** | Journal contract, memory store, harvest triggers, state recovery |

## Conformance summary

- Every requirement carries a stable key (`L1-TB-03` form; append-only,
  never renumbered). The requirement index in the core document is the
  closure denominator for audits.
- Annex rows use the closed status set `satisfied` |
  `satisfied-with-deviation` | `unsatisfied` | `not-applicable`, with a
  `structural` annotation for requirements the substrate makes unviolable.
- Every binding declares a **Binding Profile** — which substrate
  predicates hold (for example `pull-only-waiters`,
  `out-of-band-signaling`). A requirement conditional on a predicate the
  profile lacks is `not-applicable` by citing that predicate; free-form
  non-applicability is a closure failure.
- Prospective bindings rate each row `clean` | `plausible-with-pattern` |
  `gap`, each tier carrying an evidence obligation.

Normative language follows BCP 14 (RFC 2119 + RFC 8174): only uppercase
keywords are normative, and every normative keyword in the level sections
is attached to a keyed requirement.

## Fork point and versioning

- **Specification version**: 0.1.0
- **Fork point**: extracted from the PACT plugin for Claude Code,
  version 4.6.8, commit `b4041ccf`.
- **Co-release**: specification 0.1.0 is co-released with plugin
  version 4.7.0; divergence between the specification and the plugin is
  tracked from that release onward. The co-release marker is a versioning
  statement, not an audit claim — the audited baseline remains the
  extraction commit above, and plugin changes between the audited commit
  and the co-released version are outside the conformance annex's audit.

From the fork point onward the specification is versioned independently of
any realization (semver; the schema `$id` URNs embed the specification
version and move with it). Conformance annexes pin the realization version
and commit they audited in their own headers.
