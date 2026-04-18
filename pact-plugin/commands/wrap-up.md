---
description: Perform end-of-session cleanup and documentation synchronization
---
# PACT Wrap-Up Protocol

You are now entering the **Wrap-Up Phase**. Your goal is to ensure the workspace is clean, documentation is synchronized, and the session is properly closed.

> **Cross-reference**: For pausing a session (PR open, not ready to merge), see [pause.md](pause.md). Pause consolidates memory and persists state without worktree cleanup or task deletion.

## 1. Memory Consolidation (Pass 2)

Create a consolidation task for the secretary:
```
TaskCreate(subject="secretary: session consolidation (Pass 2)",
  description="Run Consolidation Harvest for team {team_name}. Follow the Consolidation Harvest workflow in your pact-handoff-harvest skill. Report summary when done.")
TaskUpdate(taskId, owner="secretary")
```

This is the deep-clean pass. Pass 1 (workflow-level HANDOFF review) is the primary mechanism; this consolidation is recommended — skip only for trivial sessions (single comPACT, no variety assessment performed).

> **Why this runs first**: Memory consolidation reads task HANDOFFs via `TaskGet`. Task audit (step 7) may delete completed tasks. Running consolidation first ensures HANDOFF data is available.

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

## 5. Journal Drain-Before-Close

Before ending the session (step 8), ensure all journal entries have been processed:

1. Confirm the secretary has completed the consolidation harvest (step 1). The secretary should confirm via `SendMessage`: "All journal entries processed to pact-memory."
2. **Only on confirmation**: Proceed to worktree cleanup and session decision.
3. **If secretary cannot confirm**: Warn user — unprocessed journal entries will not be distilled to pact-memory. The journal itself is safe (stored in `~/.claude/pact-sessions/`, not the team directory).

**Journal events**: Write a `session_end` event after confirmation, then emit an unconditional `session_consolidated` event so the SessionEnd detector (`check_unpaused_pr`) can recognize this session as consolidated regardless of whether the wrap-up took the "PR merged / no PR" branch or the "PR still open" branch:
```bash
set -e
trap 'rc=$?; echo "[JOURNAL WRITE FAILED] wrap-up.md (bash line $LINENO): \"${BASH_COMMAND%%$'\''\n'\''*}\" exit=$rc" >&2; exit $rc' ERR
python3 "{plugin_root}/hooks/shared/session_journal.py" write \
  --type session_end --session-dir '{session_dir}'
python3 "{plugin_root}/hooks/shared/session_journal.py" write \
  --type session_consolidated --session-dir '{session_dir}' --stdin <<'JSON'
{"pass": 2, "task_count": {task_count}, "memories_saved": {memories_saved}}
JSON
```

The `session_consolidated` write is unconditional — it fires regardless of whether step 6 takes the "PR still open" branch (which ALSO writes `session_paused`) or the "PR merged / no PR" branch (which previously wrote nothing and caused the false-positive warning). `{task_count}` and `{memories_saved}` come from the secretary's consolidation summary (step 1); when the secretary cannot produce exact counts, emit the event with `0` for either field rather than skipping the write — the event's EXISTENCE is the detector signal and the payload is advisory audit trail.

**Recovery note**: The journal lives in `~/.claude/pact-sessions/{slug}/{session_id}/`, independent of the team directory — it survives both natural TTL cleanup and explicit `TeamDelete`. Old session directories are cleaned automatically after 30 days (with paused-session preservation). See [pact-state-recovery.md](../protocols/pact-state-recovery.md) for the full State Recovery Protocol.

## 6. Worktree Cleanup

Check for open PRs associated with the current worktree branch:
- **PR merged or no PR**: Invoke `/PACT:worktree-cleanup` to remove the worktree cleanly.
- **PR still open**: Skip worktree cleanup. Write a `session_paused` event to the journal (see [pause.md step 5](pause.md) for the event schema). Set `consolidation_completed: true` because wrap-up steps 1-4 already performed memory consolidation. Report: "Worktree preserved — PR still open. Use `/PACT:pause` to consolidate and pause, or `/PACT:peer-review` to continue review."

## 7. Task Audit

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

## 8. Session Decision

Use `AskUserQuestion` with these exact options:
- **"Yes, continue"** (description: "Keep team alive, ready for next task") → On selection: Report "Ready for next task."
- **"Pause work for now"** (description: "Save session knowledge and pause — resume later") → On selection: invoke `/PACT:pause`
- **"No, end session"** (description: "Natural cleanup — platform reaps processes, 30-day TTL cleans directories (recommended)") → On selection: Report "Session complete. Teammate processes will be terminated when this session ends. Team and task directories (`~/.claude/teams/`, `~/.claude/tasks/`) are reaped automatically after 30 days by TTL cleanup."
- **"End session (graceful)"** (description: "Explicit shutdown + TeamDelete — for immediate cleanup or recovery from interrupted sessions") → On selection: Shut down remaining teammates — send `shutdown_request` individually to each active teammate **by name** (do NOT broadcast structured messages via `to: "*"` — broadcasts only support plain text). Wait for each response. Delete the team (`TeamDelete`). If `TeamDelete` fails because active members remain, report which teammates are still running and ask the user whether to force shutdown or leave them. Report "Session complete."
