---
description: Teammate bootstrap — loads team protocol, teachback, memory retrieval, and algedonic protocol
---

# PACT Teammate Bootstrap

<!-- You may have been routed here via the Custom Agent Instructions fallback if no PACT ROLE marker was found. -->

Load the following before any other work:

@${CLAUDE_PLUGIN_ROOT}/skills/pact-agent-teams/SKILL.md
@${CLAUDE_PLUGIN_ROOT}/skills/pact-teachback/SKILL.md
@${CLAUDE_PLUGIN_ROOT}/skills/request-more-context/SKILL.md
@${CLAUDE_PLUGIN_ROOT}/protocols/algedonic.md

## Teachback State Machine (#401)

Tasks at variety >= 7 pass through a 4-state teachback gate before any
Edit/Write/Agent/NotebookEdit tool call. The gate (`teachback_gate.py`)
infers state from metadata content-presence; you do NOT need to write
`metadata.teachback_state` explicitly — the `teachback_submit` /
`teachback_approved` / `teachback_corrections` fields are the
load-bearing signal.

| State | Who writes | What's present | You can run Edit/Write? |
|---|---|---|---|
| `teachback_pending` | (none yet) | neither submit nor approved | NO |
| `teachback_under_review` | you | valid `teachback_submit` | NO — awaiting lead |
| `active` | lead | `teachback_approved` with `conditions_met.unaddressed=[]` | YES |
| `teachback_correcting` | lead | `teachback_corrections`, OR `teachback_approved` with non-empty `unaddressed` | NO — re-submit required |

**Your write is structured, not just a flag.** Replace the legacy
`metadata={"teachback_sent": true}` pattern with:

```
TaskUpdate(taskId, metadata={"teachback_submit": {
  "understanding": "<what you understand you are building, at least 100 chars>",
  "most_likely_wrong": {
    "assumption": "<what could be wrong, at least 40 chars; shares a term with required_scope_items>",
    "consequence": "<what goes wrong if you are wrong, at least 40 chars>"
  },
  "least_confident_item": {
    "item": "<scope item, at least 30 chars>",
    "current_plan": "<concrete next step, at least 30 chars>",
    "failure_mode": "<what could fail, at least 30 chars>"
  },
  "first_action": {
    "action": "<file.py:123 or function_name()>",
    "expected_signal": "<observable result, at least 30 chars>"
  }
}})
```

**Simplified protocol** (variety in [7,9) AND fewer than 2
`required_scope_items`): only `understanding` + `first_action` are
required. The gate routes on `protocol_level` automatically.

**Revision cycle**: if the lead writes `teachback_corrections`,
re-submit ONLY the subfields listed in
`teachback_corrections.request_revisions_on`. Other fields retain their
prior validity at the gate — you do not need to re-write them. See
`pact-ct-teachback.md` for the canonical rules.

**Timeout**: if the lead is non-responsive while you are in
`teachback_under_review`, the `teachback_idle_guard` hook emits an
algedonic ALERT after 3 consecutive idle events.

Phase 1 (advisory) is active at ship time — deny reasons arrive as
`systemMessage` but the tool still runs. Phase 2 (blocking) flips
`teachback_gate._TEACHBACK_MODE` to `blocking` so deny reasons become
actual permission denials. Write your teachbacks correctly NOW so
Phase 2 does not break your workflow later.
