---
description: Perform end-of-session cleanup and documentation synchronization
---
# PACT Wrap-Up Protocol

You are now entering the **Wrap-Up Phase**. Your goal is to ensure the workspace is clean and documentation is synchronized before the session ends or code is committed.

## 0. Task Audit

Before other cleanup, audit and optionally clean up Task state:

```
1. TaskList: Review all session tasks
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

> Note: `hooks/stop_audit.py` performs automatic audit checks at session end. This table provides wrap-up command guidance for manual orchestrator-driven cleanup.

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

## 4. Team Cleanup

Clean up the session team to free resources:

1. **Shut down remaining teammates**: Send `shutdown_request` to each active teammate and wait for responses.
2. **Delete the team**: Call `TeamDelete` to remove the team directory (`~/.claude/teams/{team_name}/`).
3. **Handle failures**: If `TeamDelete` fails because active members remain, report which teammates are still running and ask the user whether to force shutdown or leave them.

> Note: `hooks/session_end.py` also performs best-effort cleanup of stale team directories from prior sessions. This manual step ensures the *current* session's team is cleanly shut down.
