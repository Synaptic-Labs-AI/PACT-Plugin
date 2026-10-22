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
| `session_start` | session_init hook (lead sessions); bootstrap_prompt_gate hook (once, for a lead the session_init hook did not record, at the first prompt whose recording completes. A recording whose CLAUDE.md block rewrite fails in a way that may clear is retried on later prompts, and the third such failure records the lead anyway; a failed `session_start` append is retried quietly on later prompts) | `team`, `session_id`, `project_dir`, `worktree`, `source`; optional: `prev_session_dir` (the session this one follows, when it is another session) | Session boundary marker; `source` names the start that wrote it: `startup` (new session, or a start that reported no source), `resume` (resumed session), `compact` (after compaction), `clear` (after `/clear`), `fork` (session forked from another session), `unknown` (a start that reported any other source), `prompt` (recorded at a prompt instead of at the start) |
| `team_realigned` | session_init hook (a resume or compaction that moves the session to the team the platform is using); bootstrap_marker_writer hook (a lead prompt or spawn that does so); the `realign_team` command | `from`, `to` | Locate the earlier team's task store, `tasks/{from}/`, for carry-across and harvest |
| `session_end` | session_end hook, wrap-up command | `warning` (optional, hook); `source`, `pr_state`, `pr_number`, `pr_source` (optional, wrap-up: `"wrap-up"`, one of `MERGED` / `OPEN` / `CLOSED` / `NONE` / `UNKNOWN`, the PR number as an int for the first three, and `cwd` or `review` for where the state was read) | Detect incomplete shutdowns. A wrap-up `session_end` with `pr_state` `MERGED` and a PR's `pr_number`, at or after the latest `review_dispatch` (at any time, when there is none), records that wrap-up closed that merged PR; one at or after a `session_paused` event for that PR also retires that paused claim |
| `session_paused` | pause command | `pr_number`, `pr_url`, `branch`, `worktree_path`, `consolidation_completed`, `team_name` | Resume paused PR work |
| `session_refreshed` | refresh command | `consolidation_completed`, `halt_active`; optional: `halt_task_ids`, `feature_task_id`, `feature_subject`, `team_name`, `next_phase`, `worktrees`, `pr_number` | Resume mid-workstream after context refresh |
| `session_refresh_consumed` | bootstrap command | `refresh_ts` | Retire a consumed refresh prompt (fire-once) |
| `session_consolidated` | wrap-up, pause commands | `pass`, `task_count`, `memories_saved` (all optional int) | Signal that Pass 2 memory consolidation ran this session — consumed by `check_unpaused_pr`: one at or after the latest `review_dispatch` silences the SessionEnd warning for an open PR or one whose state cannot be read, never for a merged one; with no `review_dispatch`, any consolidation silences it |
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

**Paused state detection** (via `check_resume_state`):

1. Read `session_paused` event (most recent) from the previous session's journal, or from an earlier one when the sessions in between did nothing (at most three back)
2. TTL check: older than 14 days → return stale notice
3. PR validation: `gh pr view` → if MERGED/CLOSED → return informational
4. Return actionable resume prompt with PR number, branch, worktree path

**Refreshed state detection** (via `check_resume_state`):

1. Read `session_refreshed` event (most recent) from the session journal, or from an earlier session's when the sessions in between did nothing (at most three back)
2. Spent check: a `session_refresh_consumed` event whose `refresh_ts` equals the refresh event's `ts` (and whose own `ts` is at-or-after it) retires the claim — no prompt
3. TTL check: older than 48 hours → prefix a stale notice (informational downgrade only, never suppression)
4. Return mid-flight resume prompt: feature task, next phase, worktree paths, and a HALT cross-check against live signal tasks (the event can never suppress a live HALT)

**Post-compaction recovery** (orchestrator rebuilds mid-session):

1. Read session journal for current session → full event history survives
2. `TaskList` → task summaries (status, blocking, ownership)
3. The task file on disk (`cat "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/tasks/{team_name}/{taskId}.json" | jq .metadata.<key>`; the `{team_name}` segment is the realigned team name — `pact-session-context.json`, `team_name` field) → metadata if task files still exist; `TaskGet` does NOT surface it
4. Journal is authoritative when task metadata is unavailable
5. If the journal's latest `team_realigned` event names a `from` store whose in-flight tasks you have not re-created, run the team-split recovery below from its step 3. Skip tasks already re-created: their description cites the old id.

**In-flight work detection** (via `check_resume_state`, on a new or resumed session when no paused or refreshed prompt surfaced):

1. Read the open workflow phases (a `phase_transition` started and not completed since the last `session_consolidated`) and the in-progress tasks in the previous session's team store
2. Either present → return a prompt naming them; treat it as a resume

**Team-split recovery** (when a notice, a SessionStart line or a refused spawn says PACT moved this session to another team, says PACT cannot tell which team is this session's, or says PACT's recorded team has no config):

1. Recorded team without a config, and no live candidate team that matches this session: run bootstrap Step 2's `TaskCreate` and `TaskUpdate` for the secretary, adding to the task's description the line `pact-recovery: <session id>-<UTC time as YYYYMMDDTHHMMSS>`, where the session id is the last part of `{session_dir}`. Then spawn the secretary. If a realign notice follows, continue from step 3; otherwise the recovery is done. If the spawn is refused, run `grep -rlF --include='*.json' '<that line>' {config_dir}/tasks/`. Each match is a task file, and its parent directory is a team. When nothing matches, or the matches sit in more than one directory, report that to the user and stop. Otherwise run the realign command in step 2 with the name of the one matching directory; on a nonzero exit, report its reason to the user and stop. Otherwise spawn the secretary, then continue from step 3 unless the command reported nothing to carry across.
2. Ambiguity notice: do not spawn. Put the named teams to the user with `AskUserQuestion`, then run `cd "{plugin_root}/hooks" && python3 -m shared.realign_team --team <chosen team>`. Only the lead runs this command; a teammate never does. On a nonzero exit, report its reason to the user and do not spawn. If it reports nothing to carry across, the recovery is done.
3. Read the latest `team_realigned` event: `python3 "{plugin_root}/hooks/shared/session_journal.py" read --session-dir '{session_dir}' --type team_realigned`. Its `from` is the earlier team. Its `to` is this session's team: use it as `{team_name}` from here on, in place of any team name the SessionStart context gave.
4. `TaskList` shows only `{team_name}`'s tasks. Read the in-progress tasks in `{config_dir}/tasks/{from}/` (the realign notice lists their ids), and re-create in `{team_name}` with `TaskCreate` each one whose work is still in flight, carrying its subject, description, owner and, on a work task, its `variety` stamp, and citing its `tasks/{from}/` id in the new description. Reference a submitted teachback or handoff you still need by its old task-file path. Do not edit or delete the files in `tasks/{from}/`.
5. When you re-create a teachback task and the work task it gates, link them again: `TaskUpdate(<teachback id>, addBlocks=[<work id>])` and `TaskUpdate(<work id>, addBlockedBy=[<teachback id>])`. Finish steps 4 and 5 for every in-flight task before you spawn a specialist for any of them.
6. At the next harvest, tell the secretary the journal holds a `team_realigned` event; the harvest reads `tasks/{from}/` as well.

### Crash Recovery

The journal survives crashes because:
- **POSIX O_APPEND** guarantees atomic writes — partial writes don't corrupt earlier entries
- **JSONL format** — each line is self-contained; one malformed line doesn't affect others
- **Fail-open reads** — `read_events()` silently skips malformed lines
- **Session-scoped storage** — the journal lives in `{config_dir}/pact-sessions/`, not `{config_dir}/teams/`, so team teardown does not remove it

`{config_dir}` is this session's Claude config root — the value of `$CLAUDE_CONFIG_DIR` when set and non-empty, otherwise `$HOME/.claude`. Read it off an absolute path the platform already injected into your context — your plugin root is `{config_dir}/plugins/…` — rather than shelling out for the variable. Substitute it before running any command; never assume `~/.claude`.

The wrap-up command harvests journal events to pact-memory before session close. The session directory, journal included, is removed 30 days after the newest modification time of the directory or of an entry directly inside it, or 180 days after that time when the journal records a `session_paused` or `session_refreshed` event. A directory past its limit is kept while its journal holds an `agent_handoff` event recorded after its last `session_consolidated` event. The current session's directory is never removed.

### Re-reading Cut Workflow Commands

After a compaction, if a PACT workflow you started is still in progress in `TaskList`, and its re-attached copy is cut short or missing, read `{plugin_root}/commands/<name>.md` in full with `Read` before you continue it, where `PACT:<name>` is that workflow. If `Read` reports a partial view, read the remaining pages. Do not invoke the workflow again: that starts it over. If the file shows `$ARGUMENTS` where the task it was started for belongs, take that task from its re-attached copy or from your compact summary.

### Malformed-Stdin Failure Log

When `session_init.py` receives malformed or incomplete stdin (invalid JSON, missing `session_id`, non-string `session_id`, empty/whitespace `session_id`, or an `unknown-*` sentinel), the stdin-validation gate drops the per-session journal anchor to avoid creating an unreapable `unknown-{hex}/` directory. The failure is instead recorded in a global bounded ring buffer at `{config_dir}/pact-sessions/_session_init_failures.log` (100-entry cap, JSONL, fail-open). When debugging session start failures that produce no per-session directory — especially failures in teammate sessions whose first-message context is never seen by the user — inspect this log with `cat "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/pact-sessions/_session_init_failures.log" | tail -20`. Each entry records a UTC timestamp, classification (`malformed_json` / `missing_session_id` / `non_string_session_id` / `empty_session_id` / `sentinel_session_id` / `other`), truncated error text (≤200 chars), cwd, and source.

---
