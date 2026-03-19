---
description: Perform end-of-session cleanup and documentation synchronization
---
# PACT Wrap-Up Protocol

You are now entering the **Wrap-Up Phase**. Your goal is to ensure the workspace is clean, documentation is synchronized, and the session is properly closed.

> **Cross-reference**: For parking a session (PR open, not ready to merge), see [park.md](park.md). Park consolidates memory and persists state without worktree cleanup or task deletion.

## 1. Memory Consolidation (Pass 2)

Create a consolidation task for the secretary:
```
TaskCreate(subject="secretary: session consolidation (Pass 2)",
  description="First: read TaskList for any completed tasks with unprocessed HANDOFFs, and check breadcrumb file at ~/.claude/teams/{team_name}/completed_handoffs.jsonl for remaining entries. Process any found. Then: review all memories saved during this session, consolidate related entries, prune superseded memories, sync Working Memory to CLAUDE.md, save orchestration retrospective as calibration data. Delete breadcrumb file when done. Report summary when done.")
TaskUpdate(taskId, owner="secretary")
```

This is the deep-clean pass. Pass 1 (workflow-level HANDOFF review) is the primary mechanism; this consolidation is recommended — skip only for trivial sessions (single comPACT, no variety assessment performed).

> **Why this runs first**: Memory consolidation reads task HANDOFFs via `TaskGet`. Task audit (step 3) may delete completed tasks. Running consolidation first ensures HANDOFF data is available.

## 2. Documentation Sync

1. **Run `/PACT:pin-memory`** (no arguments): Reviews the session for pin-worthy context, pins what matters, and prunes stale entries. This handles both CLAUDE.md updates and pinned content maintenance in one invocation.
2. **Verify docs**: Confirm that `docs/<feature>/preparation/` and `docs/<feature>/architecture/` are up-to-date with the implementation. Archive obsolete documentation to `docs/archive/`.

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

**Save as pact-memory** (delegate to secretary):
```
context: "Orchestration retrospective for {feature}"
goal: "Calibrate orchestration judgment via second-order observation"
decisions: ["Variety scored {X}, actual was {Y}", "Specialist {Z} was {well/poorly} matched because {reason}"]
lessons_learned: ["Pattern: {any recurring observation}"]
entities: ["orchestration_calibration", "{domain}"]
```

**Skip when**: Session was trivial (single comPACT, no variety assessment performed).

## 5. Worktree Cleanup

Check for open PRs associated with the current worktree branch:
- **PR merged or no PR**: Clean up parked state if it exists (`rm -f ~/.claude/pact-sessions/{slug}/parked-state.json`), then invoke `/PACT:worktree-cleanup` to remove the worktree cleanly.
- **PR still open**: Skip worktree cleanup. Write `parked-state.json` (see [park.md step 5](park.md) for schema). Set `consolidation_completed: true` because wrap-up steps 1-4 already performed memory consolidation. Report: "Worktree preserved — PR still open. Use `/PACT:park` to consolidate and pause, or `/PACT:peer-review` to continue review."

## 6. Task Audit

Audit and optionally clean up Task state:

```
1. `TaskList`: Review all session tasks
2. For abandoned in_progress tasks: complete or document reason
3. Verify Feature task reflects final state
4. Report task summary: "Session has N tasks (X completed, Y pending)"
5. IF multi-session mode (CLAUDE_CODE_TASK_LIST_ID set):
   - Offer: "Clean up completed workflows? (Context archived to memory)"
   - User confirms → delete completed feature hierarchies
   - User declines → leave as-is
```

**Cleanup rules**:

| Task State | Cleanup Action |
|------------|----------------|
| `completed` Feature task | Archive summary, then delete with children |
| `in_progress` Feature task | Do NOT delete (workflow still active) |
| Orphaned `in_progress` | Document abandonment reason, then delete |
| `pending` blocked forever | Delete with note |

**Why conservative:** Tasks are session-scoped by default. Cleanup only matters for multi-session work via `CLAUDE_CODE_TASK_LIST_ID`.

## 7. Session Decision

Use `AskUserQuestion`: "Continue working in this session?"

- **Yes**: Keep team alive. Report "Ready for next task."
- **No**: Shut down remaining teammates (send `shutdown_request` to each active teammate and wait for responses). Delete the team (`TeamDelete`). If `TeamDelete` fails because active members remain, report which teammates are still running and ask the user whether to force shutdown or leave them. Report "Session complete."
