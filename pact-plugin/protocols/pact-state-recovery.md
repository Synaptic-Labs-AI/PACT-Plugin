## State Recovery Protocol

> **Purpose**: Define how PACT reconstructs workflow state after context compaction,
> session resume, or crash recovery. The session journal is the primary durable store;
> other sources serve as fallbacks.

### Recovery Hierarchy

From most to least durable:

| Source | Location | Survives | Use For |
|--------|----------|----------|---------|
| **Session journal** | `{session_dir}/session-journal.jsonl` | Compaction, task GC, team teardown, crashes | HANDOFFs, phase progress, variety scores, commits, pause state |
| **Task system** | `TaskList` / `TaskGet` | Compaction (summaries only) | Status, blocking, assignment. Task *files* (metadata) may be GC'd |
| **pact-memory** | `~/.claude/pact-memory/memory.db` (matches a default-root pin in code — do not migrate) | Permanently | Cross-session knowledge (not workflow state) |

<!-- PACT_STORE_BAR_BEGIN -->
**STORE ACCESS.** A memory operation (save, search, get, list, update or
delete a record) goes through the pact-memory CLI. YOU DO NOT SELECT A
STORE. Do not name a store by `--db-path`, by an environment variable, or by
one more route somebody adds later. Let the CLI resolve it. A store you
select is not the store the memory of the team lives in, so a save there is
lost rather than shared. STORE INSPECTION is different: a row count, a
column audit, or a schema check on the file. To inspect, do not run a CLI
verb, do not import a module below `skills/pact-memory/scripts/`, and do not
open the store read-write. In ONE command, against ONE resolved path, check
that `memory.db-wal` and `memory.db-shm` are both absent by their full
names, then open with `mode=ro` and `immutable=1`. Without `immutable=1` the
open fails. If a sidecar is present, stop and report. The read does not load
the vector extension, so it cannot answer a question about `vec_memories`.
Stop and report rather than take a barred route.
<!-- PACT_STORE_BAR_END -->
The `pact-memory` skill carries the full rule.

### Recovery Triggers

| Trigger | What Runs | Entry Point |
|---------|-----------|-------------|
| **Session start** | Restore previous session context + detect paused or refreshed work | `session_init.py` → `restore_last_session()`, `check_resume_state()` |
| **Post-compaction** | Orchestrator rebuilds current session state | The orchestrator persona's State Recovery steps + Re-reading Cut Workflow Commands (below) |
| **Manual** | User or orchestrator reads journal directly | CLI: `python3 session_journal.py read --session-dir {session_dir}` |

> **Read output format**: the `read` subcommand prints a SINGLE JSON array — parse with `events = json.loads(output)` and iterate the list; never parse line-by-line, and never pipe through `2>/dev/null` / `|| echo` / `head` (they mask a parse crash as emptiness, which can be misread as genuine absence).

### Journal Event Types

Events are JSONL entries with common fields `v` (schema version), `type`, and `ts` (UTC).

| Type | Written By | Fields | Recovery Use |
|------|-----------|--------|--------------|
| `session_start` | session_init hook | `team`, `session_id`, `project_dir`, `worktree`, `source` | Session boundary marker; `source` ∈ {`startup`, `resume`, `compact`, `clear`, `unknown`} attributes the event to startup vs auto-compact vs `/clear` vs `/resume` for direct triage (no timing-cluster triangulation needed) |
| `session_end` | session_end hook | `warning` (optional) | Detect incomplete shutdowns |
| `session_paused` | pause command | `pr_number`, `pr_url`, `branch`, `worktree_path`, `consolidation_completed`, `team_name` | Resume paused PR work |
| `session_refreshed` | refresh command | `consolidation_completed`, `halt_active`; optional: `halt_task_ids`, `feature_task_id`, `feature_subject`, `team_name`, `next_phase`, `worktrees`, `pr_number` | Resume mid-workstream after context refresh |
| `session_refresh_consumed` | bootstrap command | `refresh_ts` | Retire a consumed refresh prompt (fire-once) |
| `session_consolidated` | wrap-up, pause commands | `pass`, `task_count`, `memories_saved` (all optional int) | Signal that Pass 2 memory consolidation ran this session — consumed by `check_unpaused_pr` so SessionEnd does not warn on consolidated sessions regardless of PR state |
| `variety_assessed` | orchestrate, comPACT, rePACT commands (feature level); any dispatch path (per-dispatch mirror) | `task_id`, `variety`; optional: `scope` (= `dispatch` on per-dispatch mirrors) | Restore variety context |
| `phase_transition` | orchestrate, comPACT | `phase`, `status` (`started`/`completed`) | Determine current phase |
| `checkpoint` | orchestrate command | `phase` (+ workflow-specific snapshot) | Fast recovery point |
| `agent_dispatch` | orchestrate, comPACT | `agent`, `task_id`, `phase` | Track active agents |
| `agent_handoff` | agent_handoff_emitter hook | `agent`, `task_id`, `task_subject`, `handoff` (dict) | Completed work (GC-proof HANDOFF store) |
| `commit` | orchestrate, comPACT | `sha`, `message`, `phase` | Track committed work |
| `s2_state_seeded` | orchestrate command | `worktree`, `agents`, `boundaries` | Restore S2 coordination state |
| `review_dispatch` | peer-review command | `pr_number`, `pr_url`, `reviewers` | Track review phase |
| `review_finding` | peer-review command | `severity`, `finding`, `reviewer` | Aggregate review results |
| `remediation` | peer-review command | `cycle`, `items`, `fixer`, `task_id` (optional) | Track fix iterations |
| `pr_ready` | peer-review command | `pr_number`, `pr_url`, `commits` | Final review state |

### Recovery Steps

**Cross-session recovery** (session resume via `restore_last_session`):

1. Read previous session's journal via `prev_session_dir` extracted from CLAUDE.md (`- Session dir:` line, with fallback derivation from Resume line + project root)
2. Filter `agent_handoff` events, then group by `(agent, task_subject)` and keep the latest for each group → completed work summary (the family is multi-event, so one group can hold a superseded copy)
3. Filter `phase_transition` events → phase progress (completed, in-progress)
4. Check `session_end` events → warnings from previous shutdown
5. Truncate long decision summaries to 80 characters
6. Return formatted resume string for orchestrator context

**Paused state detection** (via `check_paused_state`):

1. Read `session_paused` event (most recent) from previous session's journal
2. TTL check: older than 14 days → return stale notice
3. PR validation: `gh pr view` → if MERGED/CLOSED → return informational
4. Return actionable resume prompt with PR number, branch, worktree path

**Refreshed state detection** (via `check_resume_state`):

1. Read `session_refreshed` event (most recent) from the session journal
2. Spent check: a `session_refresh_consumed` event whose `refresh_ts` equals the refresh event's `ts` (and whose own `ts` is at-or-after it) retires the claim — no prompt
3. TTL check: older than 48 hours → prefix a stale notice (informational downgrade only, never suppression)
4. Return mid-flight resume prompt: feature task, next phase, worktree paths, and a HALT cross-check against live signal tasks (the event can never suppress a live HALT)

**Post-compaction recovery** (orchestrator rebuilds mid-session):

1. Read session journal for current session → full event history survives
2. `TaskList` → task summaries (status, blocking, ownership)
3. The task file on disk (`cat "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/tasks/{team_name}/{taskId}.json" | jq .metadata.<key>`) → metadata if task files still exist; `TaskGet` does NOT surface it
4. Journal is authoritative when task metadata is unavailable

### Crash Recovery

The journal survives crashes because:
- **POSIX O_APPEND** guarantees atomic writes — partial writes don't corrupt earlier entries
- **JSONL format** — each line is self-contained; one malformed line doesn't affect others
- **Fail-open reads** — `read_events()` silently skips malformed lines
- **Session-scoped storage** — the journal lives in `{config_dir}/pact-sessions/`, not `{config_dir}/teams/`, so team teardown does not remove it

`{config_dir}` is this session's Claude config root — the value of `$CLAUDE_CONFIG_DIR` when set and non-empty, otherwise `$HOME/.claude`. Read it off an absolute path the platform already injected into your context — your plugin root is `{config_dir}/plugins/…` — rather than shelling out for the variable. Substitute it before running any command; never assume `~/.claude`.

The wrap-up command harvests journal events to pact-memory before session close. The journal persists in the sessions directory for 30 days (TTL cleanup), providing a recovery window even if harvest fails. Paused sessions are exempt from TTL cleanup.

### Re-reading Cut Workflow Commands

After a compaction, if a PACT workflow you started is still in progress in `TaskList`, and its re-attached copy is cut short or missing, read `{plugin_root}/commands/<name>.md` in full with `Read` before you continue it, where `PACT:<name>` is that workflow. If `Read` reports a partial view, read the remaining pages. Do not invoke the workflow again: that starts it over. If the file shows `$ARGUMENTS` where the task it was started for belongs, take that task from its re-attached copy or from your compact summary.

### Malformed-Stdin Failure Log

When `session_init.py` receives malformed or incomplete stdin (invalid JSON, missing `session_id`, non-string `session_id`, empty/whitespace `session_id`, or an `unknown-*` sentinel), the stdin-validation gate drops the per-session journal anchor to avoid creating an unreapable `unknown-{hex}/` directory. The failure is instead recorded in a global bounded ring buffer at `{config_dir}/pact-sessions/_session_init_failures.log` (100-entry cap, JSONL, fail-open). When debugging session start failures that produce no per-session directory — especially failures in teammate sessions whose first-message context is never seen by the user — inspect this log with `cat "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/pact-sessions/_session_init_failures.log" | tail -20`. Each entry records a UTC timestamp, classification (`malformed_json` / `missing_session_id` / `non_string_session_id` / `empty_session_id` / `sentinel_session_id` / `other`), truncated error text (≤200 chars), cwd, and source.

---
