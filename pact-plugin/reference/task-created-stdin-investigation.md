# TaskCreated stdin-shape probe — 2026-04-20

Investigation artifact for #401 HIGH #2 uncertainty resolution.
Documents the empirical probe methodology, what was observed, and the
GAP that blocks literal compliance with the architect's Commit #5 gate.

## Context

Per `docs/architecture/teachback-gate/COMPONENT-DESIGN.md` §Hook 2
§Stdin payload assumption + empirical probe requirement:

> Preparer R2 flagged: TaskCreated stdin is inferred, not empirically
> known (no current PACT hook consumes it). Architect decision: add an
> empirical probe as Commit #0 (precursor), not as part of the schema
> validator itself.

Commit #0 (1727d84) shipped `pact-plugin/hooks/_task_created_probe.py`
which echoes stdin JSON to stderr, plus a `TaskCreated` hooks.json
registration. Lead's Clarif #2 directed: "Create a throwaway task
locally in the worktree to trigger the TaskCreate event deterministically.
DO NOT rely on observe next unrelated TaskCreate."

## Methodology attempted

1. Verified Commit #0 shipped the probe + TaskCreated hooks.json block
   in the worktree (`feat/teachback-gate-401`).
2. Discovered the installed plugin at
   `~/.claude/plugins/cache/pact-marketplace/PACT/3.17.13/hooks/` did
   NOT have the probe file or the TaskCreated registration — plugin
   cache is NOT a live symlink to the worktree.
3. Manually copied probe + modified hooks.json into the installed
   plugin path (backed up original to `hooks.json.teachback-probe.bak`).
4. Triggered two `TaskCreate` calls (throwaway tasks #10 + #11) to
   invoke the TaskCreated platform event.
5. Looked for probe stderr output in: tool-response feedback (absent),
   `~/.claude/pact-sessions/<slug>/<session>/` contents (absent),
   `~/.claude/debug/latest` (broken symlink; no current log),
   `~/Library/Logs/Claude Code/` (not present on this system).
6. Reverted plugin-cache mutations cleanly (TaskCreated count=0 in
   installed hooks.json; probe file removed).
7. Deleted throwaway tasks #10 and #11.

## Observation

**Hook stderr for TaskCreated events is not surfaced to the teammate
context.** Unlike PreToolUse / PostToolUse hooks (which route stderr
back through tool-result feedback) and TaskCompleted / TeammateIdle
hooks (which surface via the `TaskCompleted hook feedback:` /
`TeammateIdle hook feedback:` channel — visible in this session as
teammate_completion_gate fired 60+ times), TaskCreated hooks appear to
fire silently from a teammate's observation surface.

Confirmed OBSERVED: task JSON file disk shape (from
`~/.claude/tasks/<team>/<id>.json` after TaskCreate lands):

```json
{
  "id": "10",
  "subject": "...",
  "description": "...",
  "activeForm": "...",
  "status": "pending",
  "blocks": [],
  "blockedBy": [],
  "metadata": { /* whatever TaskCreate payload metadata was */ }
}
```

NOT observable from teammate context: platform-emitted hook stdin shape
for TaskCreated events.

## Inference from sibling hooks

Existing hook consumers of task-event stdin (per R2 + live source):

- `handoff_gate.py` (TaskCompleted) reads stdin keys: `task_id`,
  `task_subject`, `teammate_name`, `team_name`. Does NOT read
  `metadata` from stdin — always uses `_read_task_json` disk fallback
  (handoff_gate.py:242-253).
- `teammate_idle.py` (TeammateIdle) reads stdin keys: `teammate_name`,
  `team_name`. Also reads task list via `get_task_list()` disk scan
  rather than trusting stdin.
- Preparer R2 inference for TaskCreated: `task_id`, `task_subject`,
  `task_description`, `teammate_name`, `team_name`, possibly
  `metadata`.

**Load-bearing conclusion**: the shipped PACT hooks treat stdin as
optimization-only and ALWAYS disk-read for metadata. The same
discipline should apply to `task_schema_validator.py` (Commit #5): do
NOT trust stdin metadata even if it's present; disk-read via
`_read_task_json` (hoisted in Commit #4) is the authoritative source.

## Residual uncertainty

- Whether TaskCreated stdin includes `metadata` at all: UNKNOWN. Still
  inferred, not observed.
- Whether TaskCreated stdin field names match TaskCompleted's
  (`task_id` + `task_subject` + `teammate_name`): STRONG inference but
  not observed.

## Impact on Commit #5 task_schema_validator.py

The validator MUST use disk-read as the authoritative data source.
Stdin parsing is an optimization (avoid disk I/O when metadata is
present) but never the enforcement path. Specifically:

1. `_is_agent_dispatch_task(input_data)` pass-through predicate: check
   stdin for available fields (`task_id`, `task_subject`, `metadata`);
   if `metadata` is absent, fall through to disk read via
   `_read_task_json(task_id, team_name)`.
2. Validation rules read from disk-sourced metadata dict, not stdin
   directly.
3. Fail-open if `task_id` is absent from stdin (cannot identify the
   task to disk-read).

This matches `handoff_gate.py:242` pattern verbatim:
```python
task_data = _read_task_json(task_id, team_name)
metadata = task_data.get("metadata", {})
```

## Next steps

- Commit #5 author: reference this investigation in
  `task_schema_validator.py` module docstring.
- Commit #5 author: design the pass-through predicate to prefer
  disk-read for metadata, treat stdin metadata as optional.
- Consider adding a tiny dev-time helper hook (not shipped in PR):
  one-off `TASKCREATED_PROBE=1` env-gated echo to a file in
  `~/.claude/pact-sessions/...` that a future developer can inspect
  locally. Scope creep — not in #401.
- The probe file + TaskCreated hooks.json registration ARE STILL
  SHIPPED IN COMMIT #0; Commit #5 replaces the probe with the real
  validator per the architect's lifecycle plan. Do not back out
  Commit #0 — its registration slot is reused by Commit #5.

## Lead-visible blocker surfaced

A SendMessage was sent to team-lead naming this gap explicitly. Pending
lead direction on:

- (a) accept the disk-read fallback discipline (my recommendation),
- (b) attempt a different observation mechanism (e.g., modifying probe
  to `tee` stdin to a sidecar file in the session dir so
  non-tool-result-surfaced stderr still leaves a trace),
- (c) request Anthropic-side surfacing enhancement for TaskCreated
  hook observability (out of #401 scope).
