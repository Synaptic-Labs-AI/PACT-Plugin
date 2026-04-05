## State Recovery Protocol

> **Purpose**: Define how PACT reconstructs workflow state after context compaction,
> session resume, or crash recovery. The session journal is the primary durable store;
> other sources serve as fallbacks.

### Recovery Hierarchy

From most to least durable:

| Source | Location | Survives | Use For |
|--------|----------|----------|---------|
| **Session journal** | `~/.claude/teams/{team_name}/session-journal.jsonl` | Compaction, task GC, crashes | HANDOFFs, phase progress, variety scores, commits, pause state |
| **Task system** | `TaskList` / `TaskGet` | Compaction (summaries only) | Status, blocking, assignment. Task *files* (metadata) may be GC'd |
| **pact-memory** | `~/.claude/pact-memory/memory.db` | Permanently | Cross-session knowledge (not workflow state) |

### Recovery Triggers

| Trigger | What Runs | Entry Point |
|---------|-----------|-------------|
| **Session start** | Restore previous session context + detect paused work | `session_init.py` → `restore_last_session()`, `check_paused_state()` |
| **Post-compaction** | Orchestrator rebuilds current session state | CLAUDE.md State Recovery steps + workflow command auto-recovery |
| **Manual** | User or orchestrator reads journal directly | CLI: `python3 session_journal.py read --team {team_name}` |

### Journal Event Types

Events are JSONL entries with common fields `v` (schema version), `type`, and `ts` (UTC).

| Type | Written By | Fields | Recovery Use |
|------|-----------|--------|--------------|
| `session_start` | session_init hook | `team`, `session_id`, `project_dir`, `worktree` | Session boundary marker |
| `session_end` | session_end hook | `warning` (optional) | Detect incomplete shutdowns |
| `session_paused` | pause command | `pr_number`, `branch`, `worktree_path`, `consolidation_completed`, `team_name` | Resume paused PR work |
| `variety_assessed` | orchestrate command | `score`, `dimensions` | Restore variety context |
| `phase_transition` | orchestrate, comPACT | `phase`, `status` (`started`/`completed`) | Determine current phase |
| `checkpoint` | orchestrate command | Workflow-specific snapshot | Fast recovery point |
| `agent_dispatch` | orchestrate, comPACT | `agent`, `task_id`, `domain` | Track active agents |
| `agent_handoff` | handoff_gate hook | `agent`, `task_subject`, `handoff` (dict) | Completed work (GC-proof HANDOFF store) |
| `commit` | orchestrate, comPACT | `hash`, `message` | Track committed work |
| `s2_state_seeded` | orchestrate command | `boundaries`, `conventions` | Restore S2 coordination state |
| `review_dispatch` | peer-review command | `reviewers`, `pr_number` | Track review phase |
| `review_finding` | peer-review command | `reviewer`, `severity`, `summary` | Aggregate review results |
| `remediation` | peer-review command | `cycle`, `items` | Track fix iterations |
| `pr_ready` | peer-review command | `pr_number`, `status` | Final review state |

### Recovery Steps

**Cross-session recovery** (session resume via `restore_last_session`):

1. Read previous team's journal via `prev_team_name` extracted from CLAUDE.md
2. Filter `agent_handoff` events → completed work summary
3. Filter `phase_transition` events → phase progress (completed, in-progress)
4. Check `session_end` events → warnings from previous shutdown
5. Truncate long decision summaries to 80 characters
6. Return formatted resume string for orchestrator context

**Paused state detection** (via `check_paused_state`):

1. Read `session_paused` event (most recent) from previous team's journal
2. TTL check: older than 14 days → return stale notice
3. PR validation: `gh pr view` → if MERGED/CLOSED → return informational
4. Return actionable resume prompt with PR number, branch, worktree path

**Post-compaction recovery** (orchestrator rebuilds mid-session):

1. Read session journal for current team → full event history survives
2. `TaskList` → task summaries (status, blocking, ownership)
3. `TaskGet` on in-progress tasks → metadata if task files still exist
4. Journal is authoritative when task metadata is unavailable

### Crash Recovery

The journal survives crashes because:
- **POSIX O_APPEND** guarantees atomic writes — partial writes don't corrupt earlier entries
- **JSONL format** — each line is self-contained; one malformed line doesn't affect others
- **Fail-open reads** — `read_events()` silently skips malformed lines

The wrap-up command uses **drain-before-delete**: the journal persists until the secretary confirms harvest is complete. This prevents data loss when the session ends.

---
