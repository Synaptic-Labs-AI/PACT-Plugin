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
  description="Run Consolidation Harvest for team {team_name}. Follow the Consolidation Harvest workflow in your pact-handoff-harvest skill. Report summary when done.")
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

### 5. Write Paused State to Session Journal

Persist session state as a `session_paused` event in the session journal. The event contains PR number, branch, worktree path, and consolidation status — detected by `session_init.py` on resume. See [pact-state-recovery.md](../protocols/pact-state-recovery.md) for the full recovery protocol.

When consolidation ran successfully (step 3 completed), ALSO emit a `session_consolidated` event BEFORE the `session_paused` write. This is the signal consumed by `check_unpaused_pr` in `session_end.py` so the SessionEnd hook can recognize consolidation-ran sessions uniformly whether they went through wrap-up or pause. The bash template below is **shell-clamped** via a three-branch `case` statement — `true` emits, `false` is a no-op, and anything else (empty string, `True`, `TRUE`, a stray integer, an accidental placeholder) fails fast with a stderr message and non-zero exit. The orchestrator MUST pass the literal string `true` or `false` for `{true_or_false}` (matching `session_paused`'s `consolidation_completed` field type); any other value is treated as a template-substitution bug, not a caller convention.

```bash
set -e
trap 'rc=$?; echo "[JOURNAL WRITE FAILED] pause.md (bash line $LINENO): \"${BASH_COMMAND%%$'\''\n'\''*}\" exit=$rc" >&2; exit $rc' ERR
# Emit session_consolidated only when consolidation actually ran in step 3.
# Shell-clamped via case/esac so the prose contract is enforced mechanically
# — the session_consolidated signal cannot be falsely emitted when
# consolidation did not run, and an invalid flag value fails fast rather
# than silently taking the false branch.
case '{true_or_false}' in
  true)
    python3 "{plugin_root}/hooks/shared/session_journal.py" write \
      --type session_consolidated --session-dir '{session_dir}' --stdin <<'JSON'
{"pass": 2, "task_count": {task_count}, "memories_saved": {memories_saved}}
JSON
    ;;
  false)
    ;;  # intentional no-op
  *)
    echo "[pause.md] invalid {true_or_false} flag: '{true_or_false}' (expected literal 'true' or 'false')" >&2
    exit 1
    ;;
esac
python3 "{plugin_root}/hooks/shared/session_journal.py" write \
  --type session_paused --session-dir '{session_dir}' --stdin <<'JSON'
{"pr_number": {pr_number}, "pr_url": "{pr_url}", "branch": "{branch}", "worktree_path": "{worktree_path}", "consolidation_completed": {true_or_false}, "team_name": "{team_name}"}
JSON
```

> ⚠️ **Heredoc-stdin contract**: This write uses `--stdin <<'JSON' ... JSON` (quoted delimiter). The quoted delimiter disables bash variable expansion, so an apostrophe in `{branch}` (e.g., `feat/o'connor-fix`) or in `{pr_url}` cannot close the shell quote and silently drop the event. The orchestrator must still produce JSON-valid string content (escape `\"`, `\\`, and control chars when constructing the body).

**Event fields**:
| Field | Type | Description |
|-------|------|-------------|
| `pr_number` | integer | GitHub PR number |
| `pr_url` | string | Full URL to the PR |
| `branch` | string | Git branch name |
| `worktree_path` | string | Absolute path to the worktree |
| `consolidation_completed` | boolean | Whether memory consolidation finished successfully |
| `team_name` | string | Session team name (format: `pact-{session_hash}`) |

The timestamp (`ts`) is set automatically by `make_event()` and serves the same purpose as the previous `paused_at` field.

### 6. Shut Down Teammates

Invoke `Skill("PACT:inbox-wake")` and execute the Teardown operation before sending shutdown_request to teammates. This stops the lead's Monitor task and unlinks the STATE_FILE; it is the parallel-safety-net path that runs alongside the PostToolUse 1→0 last-active Teardown directive. Idempotent and best-effort — see [skills/inbox-wake/SKILL.md §Teardown Block](../skills/inbox-wake/SKILL.md#teardown-block).

Send `shutdown_request` individually to each active teammate **by name** and wait for responses. The secretary must have completed consolidation tasks (steps 1 and 3) before receiving the shutdown request.

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
