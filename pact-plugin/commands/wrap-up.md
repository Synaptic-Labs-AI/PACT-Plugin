---
description: Perform end-of-session cleanup and documentation synchronization
---
# PACT Wrap-Up Protocol

You are now entering the **Wrap-Up Phase**. Your goal is to ensure the workspace is clean, documentation is synchronized, and the session is properly closed.

## 1. Task Audit

Audit and optionally clean up Task state:

```
1. `TaskList`: Review all session tasks
2. For abandoned in_progress tasks: complete or document reason
3. Verify Feature task reflects final state
4. Archive key context to memory (via pact-memory-agent)
5. Report task summary: "Session has N tasks (X completed, Y pending)"
6. IF multi-session mode (CLAUDE_CODE_TASK_LIST_ID set):
   - Offer: "Clean up completed workflows? (Context archived to memory)"
   - User confirms → delete completed feature hierarchies
   - User declines → leave as-is
```

**Cleanup rules** (self-contained for command context):

| Task State | Cleanup Action |
|------------|----------------|
| `completed` Feature task | Archive summary, then delete with children |
| `in_progress` Feature task | Do NOT delete (workflow still active) |
| Orphaned `in_progress` | Document abandonment reason, then delete |
| `pending` blocked forever | Delete with note |

**Why conservative:** Tasks are session-scoped by default (fresh on new session). Cleanup only matters for multi-session work, where user explicitly chose persistence via `CLAUDE_CODE_TASK_LIST_ID`.

> Note: `hooks/stop_audit.sh` performs automatic audit checks at session end. This table provides wrap-up command guidance for manual orchestrator-driven cleanup.

## 2. Documentation Sync

1. **Update CLAUDE.md**: Verify it reflects the current system state (architecture, patterns, components). Run `/PACT:pin-memory` if new permanent context needs pinning.
2. **Prune stale pinned entries**: Review the `## Pinned Context` section in CLAUDE.md. Remove entries whose `<!-- pinned: YYYY-MM-DD -->` dates are old and whose content is no longer relevant.
3. **Verify docs**: Confirm that `docs/<feature>/preparation/` and `docs/<feature>/architecture/` are up-to-date with the implementation. Archive obsolete documentation to `docs/archive/`.

## 3. Workspace Cleanup

- **Identify** any temporary files created during the session (e.g., `temp_test.py`, `debug.log`, `foo.txt`, `test_output.json`).
- **Delete** these files to leave the workspace clean.

## 4. Orchestration Retrospective (Second-Order Cybernetics)

Perform a brief self-assessment. Compare your initial variety assessment and orchestration decisions against actual outcomes. This calibrates future judgment.

**Answer these four questions:**

1. **Variety accuracy**: Was the initial variety score close to actual complexity? Over/under by how much?
2. **Phase efficiency**: Did any phases need to be re-run (imPACT)? Were any skipped phases needed after all?
3. **Specialist fit**: Were specialists well-matched to tasks? Any that should have been different?
4. **Estimation pattern**: Does this match a recurring pattern from prior sessions? (Search pact-memory for `orchestration_calibration` entries)

**Save as pact-memory** (delegate to pact-memory-agent):
```
context: "Orchestration retrospective for {feature}"
goal: "Calibrate orchestration judgment via second-order observation"
decisions: ["Variety scored {X}, actual was {Y}", "Specialist {Z} was {well/poorly} matched because {reason}"]
lessons_learned: ["Pattern: {any recurring observation}"]
entities: ["orchestration_calibration", "{domain}"]
```

**Skip when**: Session was trivial (single comPACT, no variety assessment performed).

## 5. Memory Consolidation (Pass 2)

Create a task for the memory agent:
```
TaskCreate(subject="memory-agent: session consolidation (Pass 2)",
  description="Review all memories saved during this session. Consolidate related entries. Prune superseded memories. Sync Working Memory to CLAUDE.md. Save orchestration retrospective as calibration data. Report summary when done.")
TaskUpdate(taskId, owner="memory-agent")
```

This is the deep-clean pass. Pass 1 (workflow-level HANDOFF review) is the primary mechanism; this consolidation is optional but recommended for sessions with significant work.

## 6. Worktree Cleanup

If a feature worktree exists for the completed work, invoke `/PACT:worktree-cleanup` to remove it cleanly.

## 7. Session Decision

Use `AskUserQuestion`: "Continue working in this session?"

- **Yes**: Keep team alive. Report "Ready for next task."
- **No**: Shut down remaining teammates (send `shutdown_request` to each active teammate and wait for responses). Delete the team (`TeamDelete`). If `TeamDelete` fails because active members remain, report which teammates are still running and ask the user whether to force shutdown or leave them. Report "Session complete."
