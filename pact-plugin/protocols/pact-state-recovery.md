## State Recovery Protocol

> **Purpose**: Define how PACT reconstructs workflow state after context compaction,
> session resume, or crash recovery. The session journal is the primary durable store;
> other sources serve as fallbacks.

### Recovery Hierarchy

From most to least durable:

| Source | Location | Survives | Use For |
|--------|----------|----------|---------|
| **Session journal** | `~/.claude/pact-sessions/{slug}/{session_id}/session-journal.jsonl` | Compaction, task GC, TeamDelete, crashes | HANDOFFs, phase progress, variety scores, commits, pause state |
| **Task system** | `TaskList` / `TaskGet` | Compaction (summaries only) | Status, blocking, assignment. Task *files* (metadata) may be GC'd |
| **pact-memory** | `~/.claude/pact-memory/memory.db` | Permanently | Cross-session knowledge (not workflow state) |

### Recovery Triggers

| Trigger | What Runs | Entry Point |
|---------|-----------|-------------|
| **Session start** | Restore previous session context + detect paused work | `session_init.py` → `restore_last_session()`, `check_paused_state()` |
| **Post-compaction** | Orchestrator rebuilds current session state | CLAUDE.md State Recovery steps + workflow command auto-recovery |
| **Manual** | User or orchestrator reads journal directly | CLI: `python3 session_journal.py read --session-dir {session_dir}` |

### Journal Event Types

Events are JSONL entries with common fields `v` (schema version), `type`, and `ts` (UTC).

| Type | Written By | Fields | Recovery Use |
|------|-----------|--------|--------------|
| `session_start` | session_init hook | `team`, `session_id`, `project_dir`, `worktree`, `source` | Session boundary marker; `source` ∈ {`startup`, `resume`, `compact`, `clear`, `unknown`} attributes the event to startup vs auto-compact vs `/clear` vs `/resume` for direct triage (no timing-cluster triangulation needed) |
| `session_end` | session_end hook | `warning` (optional) | Detect incomplete shutdowns |
| `session_paused` | pause command | `pr_number`, `branch`, `worktree_path`, `consolidation_completed`, `team_name` | Resume paused PR work |
| `session_consolidated` | wrap-up, pause commands | `pass`, `task_count`, `memories_saved` (all optional int) | Signal that Pass 2 memory consolidation ran this session — consumed by `check_unpaused_pr` so SessionEnd does not warn on consolidated sessions regardless of PR state |
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

1. Read previous session's journal via `prev_session_dir` extracted from CLAUDE.md (`- Session dir:` line, with fallback derivation from Resume line + project root)
2. Filter `agent_handoff` events → completed work summary
3. Filter `phase_transition` events → phase progress (completed, in-progress)
4. Check `session_end` events → warnings from previous shutdown
5. Truncate long decision summaries to 80 characters
6. Return formatted resume string for orchestrator context

**Paused state detection** (via `check_paused_state`):

1. Read `session_paused` event (most recent) from previous session's journal
2. TTL check: older than 14 days → return stale notice
3. PR validation: `gh pr view` → if MERGED/CLOSED → return informational
4. Return actionable resume prompt with PR number, branch, worktree path

**Post-compaction recovery** (orchestrator rebuilds mid-session):

1. Read session journal for current session → full event history survives
2. `TaskList` → task summaries (status, blocking, ownership)
3. `TaskGet` on in-progress tasks → metadata if task files still exist
4. Journal is authoritative when task metadata is unavailable

### Crash Recovery

The journal survives crashes because:
- **POSIX O_APPEND** guarantees atomic writes — partial writes don't corrupt earlier entries
- **JSONL format** — each line is self-contained; one malformed line doesn't affect others
- **Fail-open reads** — `read_events()` silently skips malformed lines
- **Session-scoped storage** — the journal lives in `~/.claude/pact-sessions/`, not `~/.claude/teams/`, so `TeamDelete` does not remove it

The wrap-up command harvests journal events to pact-memory before session close. The journal persists in the sessions directory for 30 days (TTL cleanup), providing a recovery window even if harvest fails. Paused sessions are exempt from TTL cleanup.

### Content Durability Across Compaction

Claude Code compaction has three durability mechanisms for orchestrator content:

| Mechanism | What Survives | Durability |
|-----------|---------------|------------|
| **Explicit Read calls** | Files loaded via Read tool at bootstrap | **Lossless** — Read tracker auto-re-issues tracked Reads after compaction; `Skills restored` event independently re-processes references above the truncation cut. Two independent restoration paths. |
| **Inline skill body text** | Content written directly in the skill `.md` file | **Partial** — truncated at a cut boundary (~halfway for large files). Late sections silently dropped. |
| **CLAUDE.md / additionalContext** | Routing block, session info, pinned context | **Structural** — re-injected on every turn; highest durability. |

**Why bootstrap.md uses explicit Reads**: The orchestrator's full instructions (~525 lines in `skills/orchestration/SKILL.md` + 8 supplementary protocols) are loaded via explicit Read calls positioned in the first 25 lines of the skill body. This ensures all content survives compaction via the Read tracker, avoiding the position-dependent truncation that affects inline skill body content.

**Verification**: After compaction, all 9 Read targets should appear in `Skills restored` system-reminder events. If any file is missing, the orchestrator still has the SACROSANCT fail-safe summary inline in bootstrap.md.

### Malformed-Stdin Failure Log

When `session_init.py` receives malformed or incomplete stdin (invalid JSON, missing `session_id`, non-string `session_id`, empty/whitespace `session_id`, or an `unknown-*` sentinel), the R3 gate drops the per-session journal anchor to avoid creating an unreapable `unknown-{hex}/` directory. The failure is instead recorded in a global bounded ring buffer at `~/.claude/pact-sessions/_session_init_failures.log` (100-entry cap, JSONL, fail-open). When debugging session start failures that produce no per-session directory — especially failures in teammate sessions whose first-message context is never seen by the user — inspect this log with `cat ~/.claude/pact-sessions/_session_init_failures.log | tail -20`. Each entry records a UTC timestamp, classification (`malformed_json` / `missing_session_id` / `non_string_session_id` / `empty_session_id` / `sentinel_session_id` / `other`), truncated error text (≤200 chars), cwd, and source.

