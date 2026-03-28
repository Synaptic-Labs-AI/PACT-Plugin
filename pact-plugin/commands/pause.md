---
description: Pause the session — consolidate memory, persist state, shut down teammates
---
# PACT Pause Protocol

Pause the current session for later resumption. This is a **memory-critical subset of wrap-up** — it consolidates knowledge and persists session state without cleaning up the worktree or deleting tasks.

> **Cross-reference**: For full end-of-session cleanup (worktree removal, task audit, session decision), see [wrap-up.md](wrap-up.md). Pause is invoked automatically when the user chooses "Pause work for now" in `/PACT:peer-review`, `/PACT:comPACT`, or `/PACT:wrap-up`.

---

## When to Use

- PR is open but not ready to merge
- User wants to pause work and resume later (same or different session)
- End of day / context switch — preserve knowledge before teammates shut down

---

## Steps

> **CRITICAL**: Steps 1-3 (consolidation) MUST complete BEFORE step 6 (teammate shutdown). The secretary needs to be alive to process HANDOFFs.

### 1. Memory Consolidation (Pass 2)

```
TaskCreate(subject="secretary: session consolidation (Pass 2)",
  description="First: read TaskList for any completed tasks with unprocessed HANDOFFs, and check breadcrumb file at ~/.claude/teams/{team_name}/completed_handoffs.jsonl for remaining entries. Process any found. Then: review all memories saved during this session, consolidate related entries, prune superseded memories, sync Working Memory to CLAUDE.md. Delete breadcrumb file when done (use `python3 -c "from pathlib import Path; Path('...').unlink(missing_ok=True)"` — not shell `rm`, to avoid sensitive-file permission prompts). Report summary when done.")
TaskUpdate(taskId, owner="secretary")
```

### 2. Documentation Sync

Run `/PACT:pin-memory` (no arguments): Reviews the session for pin-worthy context, pins what matters, and prunes stale entries.

### 3. Orchestration Retrospective

Delegate calibration data save to the secretary:

```
TaskCreate(subject="secretary: save orchestration retrospective",
  description="Save orchestration calibration: context='Orchestration retrospective for {feature}', goal='Calibrate orchestration judgment via second-order observation', decisions=[variety accuracy, specialist fit, phase efficiency], entities=['orchestration_calibration', '{domain}']. Report summary when done.")
TaskUpdate(taskId, owner="secretary")
```

**Skip when**: Session was trivial (single comPACT, no variety assessment performed).

### 4. Task Status Report

Report task summary without deleting any tasks:

```
1. TaskList: Review all session tasks
2. Report: "Session has N tasks (X completed, Y in_progress, Z pending)"
3. Do NOT delete or complete any tasks — they must survive for session resume
```

### 5. Write Paused State

Persist session state for the `session_init` hook to detect on resume:

```bash
mkdir -p ~/.claude/pact-sessions/{slug}/
```

Write `~/.claude/pact-sessions/{slug}/paused-state.json`:

```json
{
  "pr_number": 288,
  "pr_url": "https://github.com/owner/repo/pull/288",
  "branch": "feat/pause-mode-consolidation-289",
  "worktree_path": "/path/to/.worktrees/feat/pause-mode-consolidation-289",
  "paused_at": "2026-03-18T09:30:00Z",
  "consolidation_completed": true,
  "team_name": "pact-d7ab1edb"
}
```

**Schema fields**:
| Field | Type | Description |
|-------|------|-------------|
| `pr_number` | integer | GitHub PR number |
| `pr_url` | string | Full URL to the PR |
| `branch` | string | Git branch name |
| `worktree_path` | string | Absolute path to the worktree |
| `paused_at` | string | ISO 8601 timestamp of when session was paused |
| `consolidation_completed` | boolean | Whether memory consolidation finished successfully |
| `team_name` | string | Session team name (format: `pact-{session_hash}`) |

**Slug derivation**: Use the project directory basename — the same derivation as `session_init.py` (`Path(project_dir).name`). For example, if the project directory is `/Users/me/Sites/my-app`, the slug is `my-app`.

### 6. Shut Down Teammates

Send `shutdown_request` to all active teammates and wait for responses. The secretary must have completed consolidation tasks (steps 1 and 3) before receiving the shutdown request.

```
For each active teammate:
  SendMessage(to="{teammate_name}", message={"type": "shutdown_request", "reason": "Session paused"})
```

Do NOT delete the team — it will be garbage-collected or reused on resume.

### 7. Report

```
"Session paused. PR #{N} open at {url}. Resume with `/PACT:peer-review`."
```

If Telegram bridge is active, send a notification:
```
telegram_notify("Session paused. PR #{N} open at {url}.")
```
