---
description: Perform end-of-session cleanup and documentation synchronization
---
# PACT Wrap-Up Protocol

You are now entering the **Wrap-Up Phase**. Your goal is to ensure the workspace is clean and documentation is synchronized before the session ends or code is committed.

## 0. Task Audit

Before other cleanup, audit and optionally clean up Task state:

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

---

## 1. Documentation Synchronization
- **Scan** the workspace for recent code changes.
- **Update** `docs/CHANGELOG.md` with a new entry for this session:
    - **Date/Time**: Current timestamp.
    - **Focus**: The main task or feature worked on.
    - **Changes**: List modified files and brief descriptions.
    - **Result**: The outcome (e.g., "Completed auth flow", "Fixed login bug").
- **Verify** that `CLAUDE.md` reflects the current system state (architecture, patterns, components).
- **Verify** that `docs/<feature>/preparation/` and `docs/<feature>/architecture/` are up-to-date with the implementation.
- **Update** any outdated documentation.
- **Archive** any obsolete documentation to `docs/archive/`.

## 2. Workspace Cleanup
- **Identify** any temporary files created during the session (e.g., `temp_test.py`, `debug.log`, `foo.txt`, `test_output.json`).
- **Delete** these files to leave the workspace clean.

## 3. Final Status Report
- **Report** a summary of actions taken:
    - **Tasks**: N total (X completed, Y pending, Z cleaned up)
    - Docs updated: [List files]
    - Files archived: [List files]
    - Temp files deleted: [List files]
    - Status: READY FOR COMMIT / REVIEW

If no actions were needed, state "Workspace is clean and docs are in sync."

## 4. Orchestration Retrospective (Second-Order Cybernetics)

Before closing the session, perform a brief self-assessment. Compare your initial variety assessment and orchestration decisions against actual outcomes. This calibrates future judgment.

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

## 5. Team Cleanup

Clean up the session team to free resources:

1. **Shut down remaining teammates**: Send `shutdown_request` to each active teammate and wait for responses.
2. **Delete the team**: Call `TeamDelete` to remove the team directory (`~/.claude/teams/{team_name}/`).
3. **Handle failures**: If `TeamDelete` fails because active members remain, report which teammates are still running and ask the user whether to force shutdown or leave them.
