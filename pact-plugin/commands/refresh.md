---
description: Mid-workstream context checkpoint — harvest, persist, shut down teammates, stand by for /compact + /PACT:bootstrap
---
# PACT Refresh Protocol

Reset the context window mid-workstream without losing state. Refresh harvests pending knowledge, persists a resumable checkpoint to the session journal, shuts down teammates, and stands by for the user to run `/compact` (canonical) and then `/PACT:bootstrap`. The workstream CONTINUES after the reset — nothing is closed, merged, or cleaned up.

> **Cross-reference**: For a PR-open pause with later resumption, see [pause.md](pause.md). For end-of-session cleanup and close, see [wrap-up.md](wrap-up.md).

---

## When to Use / When NOT

| Command | Purpose | Workstream state after |
|---------|---------|------------------------|
| `/PACT:pause` | PR open, resume the review later | Paused (PR open) |
| `/PACT:wrap-up` | End-of-session cleanup + close | Ended |
| `/PACT:refresh` | Mid-workstream context checkpoint | **Active — continues after `/compact` + `/PACT:bootstrap`** |

Use refresh when the context window is near exhaustion while work is still mid-flight — for example CODE complete but TEST pending, a live HALT/algedonic signal, or worktrees holding unmerged commits. No orchestration retrospective runs (the arc is mid-flight, not ending); `/PACT:pin-memory` is optional; the harvest and its drain confirmation are MANDATORY.

---

## Constraints (apply to every step)

- **LEAD-FRAME-ONLY**: every step that spawns, stops, or messages teammates runs in the lead frame. The team roster is flat — teammates cannot spawn or stop teammates.
- **Never clean worktrees, branches, or commits.** Verification is read-only; the workstream continues after the reset.
- **Never complete or delete tasks** — the feature task, phase tasks, and any HALT/algedonic signal tasks must survive for resume.
- **Never write under `~/.claude/pact-refresh/`.** That directory is legacy state from a removed subsystem; checkpoint state lives ONLY in the session journal.

---

## Steps

> **CRITICAL ordering**: HARVEST (step 2) MUST be drain-confirmed complete BEFORE the shutdown (step 5). The secretary must be alive to process HANDOFFs.

### 1. Preflight — degenerate case

Scan `TaskList` for an in_progress feature task. If NONE exists, tell the user refresh will proceed as harvest-only and `/PACT:wrap-up` may fit better; on their confirm, CONTINUE. Once proceeding, the journal write in step 4 is unconditional — the degenerate case writes a minimal event carrying the two required fields only.

### 2. HARVEST — memory consolidation (Pass 2)

```
TaskCreate(subject="secretary: session consolidation (Pass 2)",
  description="Run Consolidation Harvest for team {team_name}. Follow the Consolidation Harvest workflow in your pact-handoff-harvest skill. Report summary when done.")
TaskUpdate(taskId, owner="secretary")
```

**Drain-confirmation gate (MUST)**: before ANY shutdown step, verify the consolidation task's status is `completed` by READING the task (`TaskGet`, or the raw task JSON on disk) — an explicit completion check, not ordering prose. Not completed ⇒ wait or nudge the secretary; do not proceed to step 5.

### 3. PERSIST/VERIFY — read-only state check + payload computation

For each active worktree, run `git status --porcelain` and report any uncommitted changes to the user — never clean, never commit on their behalf.

Then compute the checkpoint payload:

- `halt_active` + `halt_task_ids`: scan `TaskList` for tasks with `metadata.type` in {`blocker`, `algedonic`} and status not `completed`. Always computable — `false` and omit the ids when none are live.
- `feature_task_id` / `feature_subject`: from the in_progress feature task (omit both in the degenerate case).
- `team_name`: the active session team's name — the same value bootstrap reads from the Current Session block at resume, giving it an identity cross-check.
- `next_phase`: from the phase-task state. Bounded vocabulary, writer-enforced (the journal validator does not check enums): `prepare|architect|code|test|peer-review|deploy`.
- `worktrees`: JSON list of ABSOLUTE paths of the active worktrees.
- `pr_number`: only when a PR is open — informational surface data; it never gates anything.

### 4. Write the checkpoint to the session journal

Emit `session_consolidated` only when step 2's consolidation actually completed, then write the `session_refreshed` checkpoint. The flag is **shell-clamped** via a three-branch `case`: `true` emits, `false` is a no-op, anything else fails fast as a template-substitution bug. Pass the literal string `true` or `false` for `{true_or_false}`.

```bash
set -e
trap 'rc=$?; echo "[JOURNAL WRITE FAILED] refresh.md (bash line $LINENO): \"${BASH_COMMAND%%$'\''\n'\''*}\" exit=$rc" >&2; exit $rc' ERR
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
    echo "[refresh.md] invalid {true_or_false} flag: '{true_or_false}' (expected literal 'true' or 'false')" >&2
    exit 1
    ;;
esac
python3 "{plugin_root}/hooks/shared/session_journal.py" write \
  --type session_refreshed --session-dir '{session_dir}' --stdin <<'JSON'
{"consolidation_completed": {true_or_false}, "halt_active": {halt_active}, "halt_task_ids": {halt_task_ids}, "feature_task_id": "{feature_task_id}", "feature_subject": "{feature_subject}", "team_name": "{team_name}", "next_phase": "{next_phase}", "worktrees": {worktrees}, "pr_number": {pr_number}}
JSON
```

**Degenerate case** (no active workstream): omit every optional field — write the two required fields only:

```bash
python3 "{plugin_root}/hooks/shared/session_journal.py" write \
  --type session_refreshed --session-dir '{session_dir}' --stdin <<'JSON'
{"consolidation_completed": {true_or_false}, "halt_active": false}
JSON
```

> ⚠️ **Heredoc-stdin contract**: the writes use `--stdin <<'JSON' ... JSON` (quoted delimiter), so an apostrophe in a substituted value cannot close the shell quote and silently drop the event. The orchestrator must still produce JSON-valid string content (escape `\"`, `\\`, and control chars).

**Event fields**:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `consolidation_completed` | boolean | yes | Whether the step 2 harvest completed |
| `halt_active` | boolean | yes | Whether any HALT/algedonic signal task is live at refresh time |
| `halt_task_ids` | list of strings | no | Ids of the live signal tasks (diagnostic — the live task store is the SSOT at resume) |
| `feature_task_id` | string | no | The in_progress feature task id |
| `feature_subject` | string | no | The in_progress feature task subject |
| `team_name` | string | no | The active session team's name — resume-time identity cross-check |
| `next_phase` | string | no | Bounded vocabulary above |
| `worktrees` | list of strings | no | Absolute paths of active worktrees |
| `pr_number` | integer | no | Open PR number — surface-only, never gates surfacing |

The timestamp (`ts`) is set automatically by the journal writer and becomes the checkpoint's claim id — bootstrap's consumption write binds to it.

### 5. SHUTDOWN teammates

Shut down each active teammate **by name**, staggered 1 teammate per turn — the stagger counts ops, and request + stop = 2 ops (rate-limit discipline): graceful `shutdown_request` first, then `TaskStop(name)` as the guarantee tier — `shutdown_request` is cooperative-only (empirically, on the tmux backend an approved `shutdown_response` does not terminate the teammate's pane/process); `TaskStop` is authoritative.

```
For each active teammate:
  SendMessage(to="{teammate_name}", message={"type": "shutdown_request", "reason": "Context refresh — checkpoint written, resuming after /compact"})
  then: TaskStop("{teammate_name}")
```

EXPECTED post-state: each stop removes that member's roster entry — the config FILE and the team IDENTITY survive; a lead-only roster is the correct post-refresh state, not corruption. Do NOT add synchronous send-confirmation (message delivery lands asynchronously) — verify by a disk re-read of the team config, or simply proceed. Do NOT delete the team.

### 6. STANDBY

Report to the user:

```
"Context checkpoint complete. Run /compact now, then /PACT:bootstrap to resume mid-flight."
```

Until bootstrap runs:

- **FORBID new dispatches** — no TaskCreate-and-assign, no specialist spawns.
- **RESPAWN-BEFORE-SEND**: messaging ANY pre-refresh teammate name resurrects its stale transcript and re-adds the member — bootstrap must respawn a fresh process under that name first. This includes the secretary.
- **Expect and IGNORE stragglers**: messages a teammate generated BEFORE its stop can deliver minutes later during standby. A straggler is NOT evidence the teammate survived; do not reply and do not re-engage — any reply triggers the resume-on-send hazard above.
- `/clear` is tolerated but non-canonical (no compact summary is written; the bootstrap gate re-engages either way); `/compact` is the canonical path.

If the Telegram bridge is active, send a notification:

```
telegram_notify("Context checkpoint complete. Run /compact, then /PACT:bootstrap to resume mid-flight.")
```
