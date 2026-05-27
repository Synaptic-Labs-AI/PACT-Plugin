---
name: pact-teachback
description: Command-style teachback protocol for PACT teammates. Invoking this skill directly instructs you to store your teachback in task metadata and idle on awaiting_lead_completion before any implementation work.
---

# Teachback — Store Now

## Canonical schema at a glance

The full teachback payload — all required fields plus the optional method-reconstruction sub-object — at the correct nesting level. Skim this first if you only have time for one section; the rest of the skill explains the why and the surrounding choreography.

```
TaskUpdate(taskId, metadata={

    # Top-level metadata key #1: teachback_submit
    # 5 canonical fields. variety_acknowledgment is an OBJECT (not a string).
    # reasoning_reconstruction is a sibling field of the other 4 (not nested
    # inside any of them, not placed on metadata.handoff).
    "teachback_submit": {
        "understanding":         "<...>",
        "most_likely_wrong":     "<...>",
        "least_confident_item":  "<...>",
        "first_action":          "<...>",
        "variety_acknowledgment": {
            "rationale_articulates_this_dispatch": "yes" | "no" | "concern",
            "concern": "<required when value != 'yes'>"
        },
        # Sibling field — optional below variety 11, required at 11+.
        # The 3 sub-keys are EXACTLY these three names, no substitutions.
        "reasoning_reconstruction": {
            "decision_attribution": "<...>",
            "assumption_trace":     "<...>",
            "contingency_clause":   "<...>"
        }
    },

    # Top-level metadata key #2: intentional_wait
    # SEPARATE top-level sibling of teachback_submit — NOT nested inside it.
    # Written via a SECOND TaskUpdate call after the notify SendMessage; see
    # Action: store teachback now below for the load-bearing 3-step ordering.
    "intentional_wait": {
        "reason":            "awaiting_lead_completion",
        "expected_resolver": "lead",
        "since":             "<canonical_since() output>"
    }
})
```

Each field's full purpose, the variety-band trigger for `reasoning_reconstruction`, and the 4 common wrong-shape mistakes appear in the sections below. The Common mistakes section enumerates the 4 wrong shapes the runtime advisory layer at `hooks/task_lifecycle_gate.py` catches at write time.

## What a teachback is

Invoking this skill means you are about to submit a teachback for your
current task. Do not proceed to implementation work until you have stored
it AND received the team-lead's acceptance.

A teachback is a Pask Conversation Theory verification gate. Before you
start implementing, you restate your understanding of the task so the
team-lead can catch misunderstandings early, before you burn context on a
wrong implementation.

Under the Task A + Task B dispatch shape, your teachback is the deliverable
of Task A. Task B (the primary work) is `blockedBy=[A]` and stays hidden
in your TaskList until the team-lead accepts your teachback by transitioning
Task A to `completed`.

The 5 canonical fields (Step 1) are the L1 (procedure-level) gate; the optional `reasoning_reconstruction` nested field enables the L1.5 (method-level) gate at high-variety dispatches — see [pact-ct-teachback.md §When to Method-Reconstruct](../../protocols/pact-ct-teachback.md#when-to-method-reconstruct). Variety-band thresholds live at the SSOT in `hooks/shared/variety_scorer.py` (`COMPACT_MAX` / `ORCHESTRATE_MAX` / `PLAN_MODE_MAX` + `route_workflow`); do not hard-code the 6 / 10 / 14 thresholds.

## Action: store teachback now

> **Ordering invariant** (audit anchor): the three steps below MUST execute in the order Step 1 → Step 2 → Step 3 — `metadata.teachback_submit` write FIRST, then notify SendMessage, then `intentional_wait` SET. This ordering is load-bearing for the team-lead's [Read-Trigger Precondition](../../protocols/pact-completion-authority.md#read-trigger-precondition): the lead must wait for teammate's wake-signal SendMessage before treating the raw JSON read as authoritative, but the SendMessage is only safe to send AFTER the metadata write has landed on disk. Reversing Step 1 and Step 2 produces false-empty raw reads on the lead side that have triggered false-positive teachback rejection cycles. Reversing Step 2 and Step 3 (idle before SendMessage) silently strands the lead — they will never see the wake-signal because you went idle without sending it. Editors of this skill: do NOT re-order these steps.

**Step 1 — write the teachback to task metadata** (5 canonical fields + 1 optional nested field):

```
TaskUpdate(taskId, metadata={"teachback_submit": {
    "understanding": "<what you understand you're building, key constraints, interfaces>",
    "most_likely_wrong": "<the part of your understanding you are least confident about>",
    "least_confident_item": "<one specific assumption you'd like the team-lead to confirm>",
    "first_action": "<the first concrete step you will take after teachback approval>",
    # ANTI-PATTERN: a free-text STRING here is rejected. variety_acknowledgment
    # MUST be an OBJECT with the 2 keys shown below. The runtime advisory
    # `variety_acknowledgment_schema_invalid_at_write_time` fires at TaskUpdate
    # if you submit a string. See pact-teachback skill Common mistakes row 1.
    "variety_acknowledgment": {                  # REQUIRED — see trigger paragraph below the JSON
        "rationale_articulates_this_dispatch": "yes" | "no" | "concern",
        "concern": "<required when value != 'yes'; names the smell>"
    },

    # ANTI-PATTERN: reasoning_reconstruction belongs HERE — a sibling field on
    # teachback_submit, NOT inside metadata.handoff. The handoff carries
    # reasoning_chain (the sender's own reasoning); the teachback carries
    # reasoning_reconstruction (your reconstruction of the upstream's
    # reasoning). Symmetric concept, different slot. The runtime advisory
    # `reasoning_reconstruction_in_handoff` fires if you place it on the
    # handoff. See pact-teachback skill Common mistakes row 2.

    # OPTIONAL: include per §When to Method-Reconstruct in pact-ct-teachback.md
    # (required at variety >= 11; recommended at 7-10; skipped at 4-6)
    # ANTI-PATTERN: the 3 sub-keys are EXACTLY these three names —
    # decision_attribution / assumption_trace / contingency_clause. Substituting
    # alternatives (e.g. "what-I-learned", "falsification-attempts",
    # "most-likely-wrong-prediction") is rejected as
    # reasoning_reconstruction_subkeys_invalid at write time and as
    # malformed_reasoning_reconstruction at lead-side completion-time review.
    # See pact-teachback skill Common mistakes row 3.
    "reasoning_reconstruction": {
        "decision_attribution": "I understand <upstream agent> chose <decision> because <their stated reason>",
        "assumption_trace":     "This reasoning depends on <assumption A>, <assumption B>, ...",
        "contingency_clause":   "If <assumption A or B> changes, the decision should change to <alternative>"
    }
}})
```

Before composing your teachback, read Task B's `metadata.variety` (via Task A's `blocks[0]`). Judge each of the four per-dimension rationales against THIS dispatch's complexity and record `yes` / `no` / `concern`; when not `yes`, name the smell in `concern`. Full review workflow: [`protocols/pact-variety.md` §Variety Calibration Record](../../protocols/pact-variety.md#variety-calibration-record).

**Step 2 — notify the team-lead** (lightweight prose, NOT the full payload):

```
SendMessage(
    to="team-lead",
    message=(
        "[<your-agent-name>→team-lead] Teachback submitted on Task #<A_id>. "
        "See metadata.teachback_submit. Idling on awaiting_lead_completion."
    ),
    summary="Teachback submitted: <topic>"
)
```

> # ANTI-PATTERN: Step 1 and Step 3 are SEPARATE TaskUpdate calls writing
> SEPARATE top-level metadata keys. Nesting `intentional_wait` INSIDE
> `teachback_submit` (one combined TaskUpdate) is invisible to the
> `is_self_complete_exempt` predicate at `shared/intentional_wait.py`, which
> reads `metadata.intentional_wait` directly and does not descend into
> nested objects. Your idle is unprotected. The runtime advisory
> `intentional_wait_nested_in_teachback_submit` fires at write time. See
> pact-teachback skill Common mistakes row 4.

**Step 3 — SET `intentional_wait` and idle**:

```
TaskUpdate(taskId, metadata={"intentional_wait": {
    "reason": "awaiting_lead_completion",
    "expected_resolver": "lead",
    "since": "<canonical_since() output: tz-aware ISO-8601 UTC>"
}})
```

Do NOT begin Task B until Task A's status transitions to `completed`. The team-lead's wake-signal SendMessage confirms acceptance — you cannot self-wake to poll TaskList while idle.

**On rejection** (team-lead writes `metadata.teachback_rejection`): see [pact-agent-teams §On Rejection](../pact-agent-teams/SKILL.md#on-rejection-wake-signal-receipt).

## Common mistakes

The 4 wrong shapes below are the ones the runtime advisory layer at `hooks/task_lifecycle_gate.py` catches at TaskUpdate write time. Each row names the wrong shape, the canonical correction (cross-referenced to [§Canonical schema at a glance](#canonical-schema-at-a-glance) above), and the rejection-reason enum the lead-side gate emits if the wrong shape reaches completion-time. The row numbers are stable grep-anchors: if the runtime advisory text says "See Common mistakes row N", that row is the canonical correction.

| # | Wrong shape | Canonical shape | Rejection-reason enum (write-time advisory / lead-side gate) |
|---|---|---|---|
| 1 | `variety_acknowledgment` as a free-text STRING describing your work, or as an OBJECT whose `rationale_articulates_this_dispatch` value is outside the set `{yes, no, concern}` | OBJECT with `{rationale_articulates_this_dispatch: "yes" \| "no" \| "concern", concern: "..." (required when value != "yes")}` — see the field in [§Canonical schema at a glance](#canonical-schema-at-a-glance) | `variety_acknowledgment_schema_invalid_at_write_time` (advisory) / schema-gate rejection at completion-time |
| 2 | `reasoning_reconstruction` placed inside `metadata.handoff` (the sender-side reasoning slot) | TOP-LEVEL sibling on `metadata.teachback_submit`, alongside `understanding` / `most_likely_wrong` / `least_confident_item` / `first_action` / `variety_acknowledgment` — see [§Canonical schema at a glance](#canonical-schema-at-a-glance) | `reasoning_reconstruction_in_handoff` (advisory) / `malformed_reasoning_reconstruction` (lead-side schema gate) |
| 3 | `reasoning_reconstruction` with non-canonical sub-key names (e.g. `what-I-learned`, `falsification-attempts`, `most-likely-wrong-prediction`), or sub-keys whose values are empty / whitespace / non-string | Exactly 3 sub-keys: `decision_attribution`, `assumption_trace`, `contingency_clause`, each a non-empty string — see [§Canonical schema at a glance](#canonical-schema-at-a-glance) | `reasoning_reconstruction_subkeys_invalid` (advisory) / `malformed_reasoning_reconstruction` or `empty_reasoning_reconstruction_field` (lead-side schema gate) |
| 4 | `intentional_wait` nested inside `teachback_submit` (one combined TaskUpdate call) | SEPARATE `TaskUpdate` calls with `intentional_wait` as a TOP-LEVEL metadata sibling of `teachback_submit` — see Step 3 and [§Canonical schema at a glance](#canonical-schema-at-a-glance) | `intentional_wait_nested_in_teachback_submit` (advisory) |

Rows 1–4 align 1:1 with the 4 write-time advisory rules in `task_lifecycle_gate.py`. The advisory text ends with `See pact-teachback skill Common mistakes row N` — that N maps directly to a row in this table. If a rule name above ever drifts, this section drifts with it.

## When to include reasoning_reconstruction

Include the nested `reasoning_reconstruction` sub-object whenever the dispatching task's variety score is **11+** (`ROUTE_PLAN_MODE` / `ROUTE_RESEARCH_SPIKE`). At variety 7-10 (`ROUTE_ORCHESTRATE`) it is **recommended but not required** — the lead may request reconstruction on follow-up if upstream decisions are non-trivial. At variety 4-6 (`ROUTE_COMPACT`) it is **skipped** — absence is the expected default.

| Variety score | Workflow route | reasoning_reconstruction | Lead behavior on absence |
|---|---|---|---|
| 4–6 | `ROUTE_COMPACT` | Skipped — not expected | Accept teachback; absence is the expected default. |
| 7–10 | `ROUTE_ORCHESTRATE` | Recommended (NOT required) | Accept teachback; lead MAY SendMessage requesting reconstruction on follow-up if upstream decisions are non-trivial. |
| 11–14 | `ROUTE_PLAN_MODE` (plan-mode + orchestrate) | REQUIRED | Reject teachback with `metadata.teachback_rejection{reason="missing_reasoning_reconstruction"}` plus a correction SendMessage. |
| 15–16 | `ROUTE_RESEARCH_SPIKE` | REQUIRED (treated identically to plan-mode) | Same as `ROUTE_PLAN_MODE` — reject on absence. |

Source of truth for the band cuts: `hooks/shared/variety_scorer.py` (`COMPACT_MAX` / `ORCHESTRATE_MAX` / `PLAN_MODE_MAX`); this table mirrors the SSOT at [pact-ct-teachback.md §When to Method-Reconstruct](../../protocols/pact-ct-teachback.md#when-to-method-reconstruct) — do not paraphrase the `ROUTE_*` literals. Variety **10** is the TOP of `ROUTE_ORCHESTRATE` (recommended-not-required); variety **11** is the BOTTOM of `ROUTE_PLAN_MODE` (required). The 10|11 boundary is the cut.

The three sub-keys are three cognitive operations on the upstream's HANDOFF: `decision_attribution` restates what the upstream decided and why; `assumption_trace` lists the falsifiable propositions the reasoning depends on; `contingency_clause` names a concrete alternative if those assumptions are false. Vague answers are lead-side reject signals — see [pact-ct-teachback.md §When to Method-Reconstruct](../../protocols/pact-ct-teachback.md#when-to-method-reconstruct) for anti-pattern examples + the full variety-band gate.

If you are dispatched as an owner in `TEACHBACK_EXEMPT_AGENT_TYPES` (currently `{pact-secretary}` per `hooks/shared/intentional_wait.py`), the entire teachback gate is bypassed — including this sub-field. No carve-out logic needed.

## Ordering rule

You must store your teachback (`metadata.teachback_submit` write) before any Edit/Write/Bash call used for implementation work. Reading files to understand the task (Read, Glob, Grep) is permitted before teachback; those are understanding actions, not implementation actions.

Under the Task A + Task B dispatch shape, this ordering is structurally reinforced: Task B is hidden behind `blockedBy=[A]` until Task A's status transitions to `completed`. The `metadata.teachback_submit` write IS your teachback delivery; the team-lead's `TaskUpdate(A, status="completed")` paired with a wake-signal SendMessage IS approval.

## Post-store behavior

Idle on `awaiting_lead_completion` until the team-lead's wake-signal arrives. Do NOT speculatively begin Task B; the team-lead's status flip is the gate.

If you have other claimable, unblocked tasks unrelated to this dispatch (a separate Task A from a different mission), you may claim and work them. The wait is per-task, not per-agent.

## Exception

Consultant questions (a peer asks you something) do not require a teachback. You only teachback on task dispatches.
