## Variety Management

Variety = complexity that must be matched with response capacity. Assess task variety before choosing a workflow.

### Task Variety Dimensions

| Dimension | 1 (Low) | 2 (Medium) | 3 (High) | 4 (Extreme) |
|-----------|---------|------------|----------|-------------|
| **Novelty** | Routine (done before) | Familiar (similar to past) | Novel (new territory) | Unprecedented |
| **Scope** | Single concern | Few concerns | Many concerns | Cross-cutting |
| **Uncertainty** | Clear requirements | Mostly clear | Ambiguous | Unknown |
| **Risk** | Low impact if wrong | Medium impact | High impact | Critical |

### Quick Variety Score

Score each dimension 1-4 and sum:

| Score | Variety Level | Recommended Workflow |
|-------|---------------|---------------------|
| **4-6** | Low | `/PACT:comPACT` |
| **7-10** | Medium | `/PACT:orchestrate` |
| **11-14** | High | `/PACT:plan-mode` → `/PACT:orchestrate` |
| **15-16** | Extreme | Research spike → Reassess |

**Calibration Examples**:

| Task | Novelty | Scope | Uncertainty | Risk | Score | Workflow |
|------|---------|-------|-------------|------|-------|----------|
| "Add pagination to existing list endpoint" | 1 | 1 | 1 | 2 | **5** | comPACT |
| "Add new CRUD endpoints following existing patterns" | 1 | 2 | 1 | 2 | **6** | comPACT |
| "Implement OAuth with new identity provider" | 3 | 3 | 3 | 3 | **12** | plan-mode → orchestrate |
| "Build real-time collaboration feature" | 4 | 4 | 3 | 3 | **14** | plan-mode → orchestrate |
| "Rewrite auth system with unfamiliar framework" | 4 | 4 | 4 | 4 | **16** | Research spike → Reassess |

> **Extreme (15-16) means**: Too much variety to absorb safely. The recommended action is a **research spike** (time-boxed exploration to reduce uncertainty) followed by reassessment. After the spike, the task should score lower—if it still scores 15+, decompose further or reconsider feasibility.

### Learning II: Pattern-Adjusted Scoring

Before finalizing the variety score, search pact-memory for recurring patterns in the task's domain. This implements Bateson's Learning II — learning to learn from past experience.

1. **Search**: Query pact-memory for `"{domain} orchestration_calibration OR review_calibration"` and `"{domain} blocker OR stall OR rePACT"`
2. **Assess**: If 5+ memories match a recurring pattern (e.g., "auth tasks consistently underestimated"), bump the relevant variety dimension by 1
3. **Note specialist patterns**: If past calibrations indicate specialist mismatch for this domain, note for specialist selection
4. **Document**: "Variety adjusted from {X} to {Y} due to recurring {pattern}"

**Skip when**: First session on a new project (no calibration data exists yet).

### Variety Strategies

**Attenuate** (reduce incoming variety):
- Apply existing patterns/templates from codebase
- Decompose into smaller, well-scoped sub-tasks
- Constrain to well-understood territory
- Use standards to reduce decision space

**Amplify** (increase response capacity):
- Invoke additional specialists
- Enable parallel execution (primary CODE phase strategy; use QDCL from [orchestrate.md](../commands/orchestrate.md))
- Invoke nested PACT (`/PACT:rePACT`) for complex sub-components
- Run PREPARE phase to build understanding
- Apply risk-tiered testing (CRITICAL/HIGH) for high-risk areas

### Variety Checkpoints

At phase transitions, briefly assess:
- "Has variety increased?" → Consider amplifying (more specialists, nested PACT)
- "Has variety decreased?" → Consider simplifying (skip phases, fewer agents)
- "Are we matched?" → Continue as planned

**Who performs checkpoints**: Orchestrator, at S4 mode transitions (between phases).

### Agent State Model

Derive agent state from progress signals (see agent-teams skill, Progress Signals section) and existing monitoring:

| State | Indicators | Orchestrator Action |
|-------|-----------|-------------------|
| **Converging** | Progress signals show forward movement (files modified, tests passing) | No intervention needed |
| **Exploring** | Progress signals show searching behavior (reading files, no modifications yet) | Normal for early task stages; intervene if persists past ~50% of expected duration |
| **Stuck** | No progress signals for extended period; stall detection triggers | Send context/guidance via `SendMessage`; escalate to imPACT if unresponsive |

**State transitions**:
- Exploring → Converging: Normal (agent found approach, started implementing)
- Converging → Exploring: Concerning (may indicate blocker or scope expansion)
- Any → Stuck: Intervention needed

**Dependency**: Requires progress signal data from agents. Request progress monitoring in dispatch prompts for tasks where mid-flight visibility matters (variety 7+, parallel execution, novel domains).

### Variety Calibration Record

> **Cybernetic basis**: Bateson's deutero-learning — the system learns to learn by comparing
> predicted difficulty against actual outcomes, creating a feedback loop for scoring accuracy.

At workflow completion (`/PACT:wrap-up` after orchestrate, or comPACT completion), the secretary gathers calibration metrics during HANDOFF processing, asks the team-lead for a brief difficulty assessment, and saves the calibration record to pact-memory. Records feed back into Learning II pattern matching.

**Schema**:

```
CalibrationRecord:
  task_id: str                    # Feature task ID
  domain: str                     # Top-level domain (e.g., "auth", "hooks", "frontend")
  initial_variety_score: int      # Score at orchestration start (4-16)
  actual_difficulty_score: int    # Post-hoc assessment (4-16, same scale)
  dimensions_that_drifted:        # Which dimensions were off
    - dimension: str              # "novelty" | "scope" | "uncertainty" | "risk"
      predicted: int              # 1-4
      actual: int                 # 1-4
  blocker_count: int              # imPACT cycles triggered
  phase_reruns: int               # Phases that had to be redone
  specialist_fit: str | null      # "good" | "undermatched" | "overmatched" | null
  timestamp: str                  # ISO 8601
```

**pact-memory mapping**: Saved via secretary with entities including `orchestration_calibration` AND `{domain}` (required for Learning II queries).

**Post-cycle comparison**: During HANDOFF processing, the secretary:
1. Reads feature task metadata for initial_variety_score
2. Scans `TaskList` for blocker count and phase rerun count
3. Asks the team-lead for a brief difficulty assessment (higher, lower, or about the same)
4. Computes the full CalibrationRecord and saves to pact-memory
5. If drift exceeds 2 in any dimension, notes as significant for future Learning II queries

#### Per-Dispatch Variety Stamping

The feature-level CalibrationRecord above coexists with per-dispatch variety stamping. Every primary work task (Task B in the Teachback-Gated Dispatch shape) receives `metadata.variety` at `TaskCreate`-time using the per-dimension rationale schema. The orchestrator scores THIS dispatch's complexity afresh — NOT inherited from feature variety, NOT capped by feature variety.

**Per-dispatch schema** (stamped at TaskCreate-time on each Task B):

```
{
  "variety": {
    "novelty":               1-4,
    "novelty_rationale":     "<1-sentence: the shape of this dispatch's novelty — never what you expect to find>",
    "scope":                 1-4,
    "scope_rationale":       "<1-sentence: the shape of this dispatch's scope — never what you expect to find>",
    "uncertainty":           1-4,
    "uncertainty_rationale": "<1-sentence: the shape of this dispatch's uncertainty — never what you expect to find>",
    "risk":                  1-4,
    "risk_rationale":        "<1-sentence: the shape of this dispatch's risk — never what you expect to find>",
    "total":                 4-16
  }
}
```

> **The teammate reads these rationales BEFORE it starts work.** Describe only the SHAPE of the work — its novelty, breadth, unknowns and exposure. Do not write the dispatch's hypothesis, its expected answer, or the reasoning that produced it. Where a dispatch's value depends on the teammate NOT knowing something, the rationales must not disclose it.

> The canonical total key is `total`. The lifecycle hook's band resolver additionally tolerates non-canonical `score` / top-level `variety_score`, or the sum of the four dimension scores, as fallbacks for stamps seen in the field — but orchestrators MUST stamp `total`. A resolvable `total` is taken at face value before the resolver reaches the dimension-sum fallback. So a `total` that disagrees with its four dimension scores is used as stated. No check reports that disagreement, at read time or at write time.

> **Inheritance fallback (resolver-side, not stamp-side).** If a Task B is dispatched WITHOUT a resolvable `metadata.variety` despite the requirement above, the band resolver inherits the band from the PARENT (Plan/feature/umbrella) task that Task B blocks — so `reasoning_reconstruction` stays resolvable rather than silently mis-resolving as `skipped` (consultation Task Bs are frequently 11-13). This is a read-time SAFETY NET for an omission, NOT a license to skip stamping: orchestrators still stamp each Task B afresh per the directive above. Inheritance fires only when the parent pointer is unambiguous (Task B blocks exactly one task) and that parent is itself stamped; otherwise the resolver fails open to `unresolvable`.

> **Enforcement split (dispatch-boundary vs advisory).** A MISSING stamp on a dispatched Task B is REFUSED at the terminal dispatch-wiring write (the `TaskUpdate` setting `owner`+`addBlockedBy`): `PACT_DISPATCH_VARIETY_MODE` defaults to `deny`, and `warn` or `shadow` opts a consumer DOWN. A stamp that IS present but malformed/untotaled stays a post-write advisory. The wiring-write gate reads the linked Task B's variety structurally — it never keys on actor identity.

> **Journal mirror (per-dispatch `variety_assessed` event).** Every dispatch site that stamps `metadata.variety` on a Task B also writes a `variety_assessed` journal event keyed on THAT task's id, carrying the four dimension scores and the total (`*_rationale` strings omitted), with the TOP-LEVEL `"scope": "dispatch"` discriminator present. The FEATURE-level assessment event carries NO `scope` field, and journal readers key on that difference: arc-start resolution and first-event feature derivation exclude dispatch-marked events, and the wrap-up Q5 join consumes them as an as-dispatched fallback source. A hand-written emission that omits the field degrades to feature-level (the legacy behavior), never silently corrupts dispatch semantics — but omit it and the dispatch site loses its journal mirror, so write it.

**Why per-dimension rationales (not a single rationale)**: A single rationale field tolerates cargo-cult ("matches feature complexity" satisfies it). Four distinct rationale fields, one per dimension, force the orchestrator to articulate four independent judgments — cargo-culting all four with one phrase is mechanically incoherent (cannot coherently explain why novelty AND scope AND uncertainty AND risk are simultaneously "the same as feature" without exposing the copy-paste).

**Write-time check on each rationale**: ask what the sentence is ABOUT. If it describes the thing being changed, reviewed, or built — its behaviour, its blast radius, the class its bug belongs to — it describes the topic, and it is not a rationale. A rationale describes the WORK: what THIS dispatch must do, must know, or must not get wrong.

- FAILS, on a code review: "a wrong path resolution in production writes the store somewhere unintended" — that is the reviewed change's risk, not the review's.
- PASSES: "the review's own risks are a probe that writes to the live store, and a finding that is missed."

Portability is not the fault. A rationale that names a coupled file pair or a permanent context cost transfers to other dispatches on the same artifacts because that structure is stable, and it is still a rationale. The cost of a topic-rationale is not the score, which is typically correct either way — it is where attention goes: a rationale about production blast radius argues for scrutiny of the code, while a rationale about the review argues also for discipline about the reviewer's own commands.

#### Q5 Dispatch Variety Calibration (Wrap-Up Aggregation)

The wrap-up retrospective's Q5 reports the CALIBRATION DELTA — the feature-level variety estimate against the distribution of per-dispatch variety the arc actually produced. It does NOT report a stamping-compliance ratio: stamping is refused at the dispatch-wiring write per the Enforcement split above, so compliance has no variance left to measure. Whether the estimate was well-calibrated is the question enforcement cannot answer, and it is the one this question keeps.

**Population — the `dispatch_site` journal stream, scoped to the current arc.** Its membership is the OWNER-WITNESSED dispatch sites: the emit fires on a `TaskUpdate` that names a pact-specialist `owner` on a non-teachback, non-exempt task, and on nothing else — one event means one dispatch by construction, never by inference. An owner write in a SPLIT wiring (owner set in one update, blockers at creation or in another) is a full witness; the composite owner+addBlockedBy write is NOT required. Review-panel dispatches are EXCLUDED, because the peer-review path dispatches reviewers without ever writing owner on the task — no owner write witnesses them, and covering them would mean inferring ownership from a display convention, which keys membership on something that moves. Wanting them counted is remedied by canonical owner wiring at dispatch — a practice-side change, never a widening of the emit's inference — and they stay visible through the `review_dispatch` stream. MEMBERSHIP DEPENDS ON THE TEACHBACK SUBJECT CONVENTION: a teachback Task-A gate receives the same owner-only split wiring a work task does, so the subject carve-out is the SOLE discriminator excluding those gates, and a gate whose subject loses the TEACHBACK marker is counted as a site. Because the platform reuses task_ids across arcs in a resumed session, the stream MUST be scoped to the current arc before use. Do NOT widen the population to another source — a scan of the task store answers "which tasks happen to carry a stamp", whose membership moves with any later decision about which sites get stamped, and the substitution is silent because the wrong population still yields a mean that renders.

**Terms.** One pass of the pure helper `extract_final_dispatch_coverage` over the `dispatch_site` stream, the `task_metadata_snapshot` stream, and the arc-scoped dispatch-marked `variety_assessed` events returns a dict of ten values. MEMBERSHIP comes from `dispatch_site` and the VALUE comes from the latest `task_metadata_snapshot` for the same task, so the distribution holds the FINAL total rather than the as-dispatched one:

- `variety_totals` — the FINAL resolved total of each member, taken from the latest `task_metadata_snapshot` for that task and falling back to the as-dispatched sources in order — the `dispatch_site` value, then the task's dispatch-marked `variety_assessed` event — when no snapshot resolves, through the shared `resolve_variety_total` accessor (so a stamp recovered through a non-canonical candidate still counts). This list IS the distribution the delta is computed over.
- `sites` — the number of `dispatch_site` events. **A REPORTED COUNT, NEVER A DENOMINATOR.** Passing it to `compute_variety_divergence` revives the retired ratio. As a count it is the only thing separating an arc that dispatched nothing from an arc whose dispatches carry no resolvable stamp; report those two as one and the answer is confidently wrong.
- `malformed` — members for which NEITHER stream resolves a total AND the `dispatch_site` event carries a PRESENT `variety`. A data-quality defect, NOT the same finding as an ABSENT stamp, and never merged with one: the remedies are opposite.
- `fallback_used` — the number of members for which the final value did NOT come from a snapshot. **IT COUNTS ARMS 2 AND 3 TOGETHER.** A member that resolves NO value at all is in that number. Do not read it as a count of members that carried an as-dispatched value, or it reads high — arm 2 has TWO as-dispatched sources in resolution order (the `dispatch_site` value, then the dispatch-marked `variety_assessed` event), and a member rescued by the second still counts here. Report the count whenever it is non-zero, and do not compute a rate across it.
- `total_unresolved` — the number of members that resolve NO total on any stream. It is the arm-3 half of `fallback_used`, and `fallback_used` keeps its meaning and its name as the union of arms 2 and 3.
- `superseded` — the number of members for which the final TOTAL differs from the as-dispatched one, counted only where the two totals each resolve. Report the count whenever it is non-zero.
- `late_stamped` — the number of members that took a snapshot value and carry NO `variety` key on the `dispatch_site` event. The dispatch was not stamped at dispatch time and a subsequent write supplied the value.
- `dispatch_malformed` — the number of members that took a snapshot value and carry a PRESENT but unresolvable `variety` on the `dispatch_site` event. A producer defect that the snapshot value hides if this number is not reported.
- `superseded_dimensions_only` — the number of members for which the two totals each resolve and agree, and the two dimension vectors are each complete and DIFFER. A revision that moved dimensions and left the total where it was.
- `dimensions_incomparable` — the number of members for which the two totals each resolve and agree, and a dimension vector is incomplete. The comparison could not run, so these members are reported rather than absorbed into the agreeing population.

**Two relation types are in one key set, and a reader cannot treat all ten alike.** The six arm-1 cells are a PARTITION, so a member reaches one cell only. One of the six carries no key: the members for which the two totals agree and the two dimension vectors agree. The fallback-path terms are a NESTING. `fallback_used` contains `total_unresolved`, and `total_unresolved` contains `malformed`. The difference between the middle term and the inner one is the honest un-stamped dispatch on the fallback path.

**The preconditions a final value rests on.** The final value of a member is correct only when four conditions hold at the write that produced its latest snapshot. First, `variety` is a targeted key of the per-write mirror. Second, `variety` is not in the snapshot exclusion set. Third, the write comes from the canonical journal frame, and a tmux teammate frame skips the per-write mirror. Fourth, the write is not a post-completion write. These four are properties of the emitter and not of the caller. So a caller can satisfy the two preconditions the helper states and read a stale value even so.

**What these counters cannot report.** The join cannot report a rationale-only correction, because the `dispatch_site` projection carries the four dimensions and their total and drops the `*_rationale` strings, so a corrected rationale has nothing on that side to compare against. Rationale corrections are the more frequent class in the field. So the sum of `superseded` and `superseded_dimensions_only` counts the corrections that moved a total or a dimension, and is not the number of corrections that occurred. A compare of two snapshots for one task stays available and is a different question from this one.

**Sample loss.** A dispatch can leave no usable sample two ways, and BOTH mean the mean is over fewer dispatches than occurred: a SKIPPED emit that never reached the journal (`journal_emit_skipped`), and an UNRESOLVABLE one that did (`malformed`). Name a count for each whenever it is non-zero; do not compute a rate over them. The returned dict also carries a `coverage` key — it is not an output of this question and is not rendered.

**Exclusions are a category judgment, never number management.** Teachback Task-A gates and teachback-exempt owners are absent from the population because they are not dispatch SITES, and that rests on ground independent of this metric: `TEACHBACK_EXEMPT_AGENT_TYPES` is a pre-existing declaration that those owners are dispatched WITHOUT the Task-A gate, and the gate is what makes a dispatch a dispatch. They fail the question "is this a dispatch requiring understanding verification?", which is a DIFFERENT question from "was this dispatch stamped?" — a teachback-exempt owner is not a variety-eligible DISPATCH, which does not license exempting any dispatch from stamping. NEVER remove a shape from the population because a number looks bad: an owner or dispatch shape that is not being stamped is a gap whose remedy is stamping it.

#### variety_acknowledgment — Teammate Verification Workflow

The teammate becomes the peer reviewer of the orchestrator's variety scoring. The teachback canonical schema includes a required `variety_acknowledgment` sub-field stored alongside the 4 existing teachback fields:

> **Where a dispatch's value depends on the teammate NOT knowing something, stamp every `*_rationale` as `WITHHELD: ignorance-dependent dispatch` and nothing else.** The scores stay real. A rationale that was never written cannot be read, and the seat still files `variety_acknowledgment` — `concern`, naming the withholding — so no required field is missing.

```
"variety_acknowledgment": {
  "rationale_articulates_this_dispatch": "yes" | "no" | "concern",
  "concern": "<required when value != 'yes'; names the smell>"
}
```

**Teammate workflow** (extends pact-teachback skill's Step 1 metadata write):

1. After claiming Task A and reading the task description, the teammate reads the four SCORES of `metadata.variety` on Task B (resolved via `Task A.blocks[0]`). **The `*_rationale` strings are read ONLY AFTER `understanding`, `most_likely_wrong`, `least_confident_item` and `first_action` are composed. This ordering is required, not preferred: those four fields need no rationale to write, and a rationale read earlier cannot be un-read.**
2. Teammate judges each of the four per-dimension rationales against THIS dispatch's actual work — does `novelty_rationale` articulate why THIS dispatch is novel, or does it copy feature-level language? Same check for `scope_rationale`, `uncertainty_rationale`, `risk_rationale`.
3. Teammate records the judgment in `metadata.teachback_submit.variety_acknowledgment`:
   - `"yes"` — all four rationales articulate THIS dispatch's complexity; `concern` field omitted or empty.
   - `"no"` — one or more rationales appear cargo-culted or wrong; teammate names the smell in `concern`.
   - `"concern"` — softer signal; teammate has reservation but not certain; names the doubt in `concern`.

**Lead workflow** (extends teachback review):

The lead reviews `variety_acknowledgment` as part of teachback acceptance per [pact-completion-authority.md §Teachback Review](pact-completion-authority.md#teachback-review). Two acceptance paths:

- **`"yes"`**: standard teachback acceptance; lead marks Task A completed + sends paired wake-signal `SendMessage`.
- **`"no"` or `"concern"`**: lead has two corrective options before acceptance:
  - **EXCEPTION — a withheld stamp**: when Task B's rationales read `WITHHELD: ignorance-dependent dispatch` and the `concern` names that withholding, take NEITHER option — accept the teachback and leave the stamp as written. This overrides the preference for orchestrator-side correction: the teammate's flag is correct BY CONSTRUCTION here, and re-stamping writes the hypothesis the withholding exists to keep out of the task.

  - *Orchestrator-side correction* (preferred when teammate's flag is correct): re-stamp `metadata.variety` on Task B via `TaskUpdate` with refined per-dimension rationales, THEN accept the teachback. The teammate's acknowledgment becomes part of the audit trail; no rejection needed.

    > ⚠️ To re-stamp, RE-SEND THE FULL `variety` OBJECT in ONE `TaskUpdate` call, and include each field you did not revise, unchanged. A write that names `variety` REPLACES the whole object, and it erases each field you omit. It reports no error. Then read the task file back and enumerate the keys of `metadata.variety`.

  - *Teammate-side correction* (when teammate's flag is erroneous): reject the teachback via `metadata.teachback_rejection` with reason explaining why the variety scoring stands as-is; teammate revises and resubmits.

**META-BLOCK escalation at 3+ rejection cycles**: if teammate flags persist across 3+ cycles after lead correction attempts, the standard imPACT META-BLOCK escalation applies — see [pact-completion-authority.md §META-BLOCK](pact-completion-authority.md#meta-block). The 3-cycle bound is the existing protocol's bound; per-dispatch variety stamping inherits, does not redefine.

#### Variety Acknowledgment Signal (Wrap-Up Aggregation)

At wrap-up time, the secretary aggregates `variety_acknowledgment` flag rates across the session's dispatch corpus. Two triggers surface a calibration concern in the orchestration retrospective:

- **Rate trigger**: if more than 20% of teachbacks recorded `"no"` or `"concern"`, flag the orchestrator's variety scoring as potentially miscalibrated for this session's dispatch shape.
- **Single-no trigger**: a single `"no"` flag (stronger signal than `"concern"`) on a load-bearing dispatch surfaces the specific dispatch + smell in the retrospective, even when rate-trigger does not fire.

**Withholding exclusion**: an acknowledgment whose `concern` carries the literal `WITHHELD: ignorance-dependent dispatch` is excluded from BOTH terms of the rate and from the acute-flag list. It acknowledges a dispatch whose rationales were deliberately withheld, so it judges no scoring, and every withheld dispatch produces one by construction — counted, it trips a threshold meaning "teammates flagged your scoring" on dispatches nobody judged. An acknowledgment describing a withholding WITHOUT that literal counts as a REAL SIGNAL. The exclusion must be applied where `concern` is still in hand: the flag list drops it, so an exclusion stated after that list cannot be performed.

The aggregation feeds back into Learning II calibration data alongside the feature-level CalibrationRecord — per-dispatch acknowledgment rates are a leading indicator of orchestrator-side scoring drift.

**Arc scope (resumed/multi-feature sessions)**: both the Q5 divergence aggregation and this Q6 signal aggregation are scoped to the CURRENT arc, not the whole session journal. The wrap-up derives `arc_start` as the latest `variety_assessed.ts` whose `task_id` matches the current `feature_task_id`, EXCLUDING dispatch-marked events (top-level `scope` equal to `"dispatch"` — per-dispatch mirrors carry the dispatch task's id and never anchor an arc) — the platform reuses task_ids across arcs, so the latest-ts match (NOT a plain most-recent `variety_assessed`) identifies the current arc — then passes `--since arc_start` to every journal read (`dispatch_site`, `task_metadata_snapshot`, `journal_emit_skipped`, `teachback_ack`, and the arc-scoped `variety_assessed` re-read that feeds Q5's third argument). The `--since` filter parses timestamps rather than string-comparing them (emit and arc-start timestamps can differ in UTC-zone suffix), is inclusive of the arc-start instant, and fails open to a whole-journal read when no matching `variety_assessed` exists (single-arc and legacy sessions are unchanged). This keeps prior arcs from inflating the Q5 divergence denominator or skewing the Q6 acknowledgment-signal rate.

---
