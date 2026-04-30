# Inbox-Wake Runbook (#591)

End-to-end operator runbook for the lead-side inbox-wake mechanism. Use this to verify a fresh-session arm/teardown cycle, observe 30/60/120 quiet-window coalescing, diagnose failure modes, and inspect runtime state.

Implementation references:
- Arm command: [pact-plugin/commands/watch-inbox.md](../../commands/watch-inbox.md)
- Teardown command: [pact-plugin/commands/unwatch-inbox.md](../../commands/unwatch-inbox.md)
- Charter contract surface: [pact-plugin/protocols/pact-communication-charter.md §Wake Mechanism](../../protocols/pact-communication-charter.md#wake-mechanism)
- Lifecycle hook: `pact-plugin/hooks/wake_lifecycle_emitter.py` (PostToolUse, matcher `TaskCreate|TaskUpdate`)
- Resume-arm hook: `pact-plugin/hooks/session_init.py` (Option-C resume gap closure)
- Registry cleanup hook: `pact-plugin/hooks/session_end.py` (`cleanup_wake_registry`)

The hook registration takes effect at the **next fresh session** after these files are merged. Hooks loaded in the session that authors them do not fire — verify in a fresh session.

---

## 1. Lifecycle: When Arm and Teardown Fire

| Event | Trigger | Mechanism |
|---|---|---|
| **Arm — first active task** | PostToolUse fires after `TaskCreate` (or `TaskUpdate` with owner assignment) and the team transitions 0→1 active teammate task | `wake_lifecycle_emitter.py` emits `additionalContext` directive: *"Invoke Skill('PACT:watch-inbox') before continuing."* The lead invokes the command on its next turn. |
| **Arm — session resume** | `SessionStart` fires and the team's task list already has active teammate tasks (resumed session) | `session_init.py` Option-C path: hook reads `~/.claude/tasks/{team}/` filtered by `_lifecycle_relevant`; if count ≥ 1, emits unconditional Arm directive via `additionalContext`. |
| **Teardown — last active task** | PostToolUse fires after `TaskUpdate(status=completed)` and the team transitions 1→0 active teammate tasks | `wake_lifecycle_emitter.py` emits Teardown directive: *"Invoke Skill('PACT:unwatch-inbox') — no remaining teammate work."* |
| **Teardown — operator command** | Lead invokes `/wrap-up` (after all teammate tasks have completed) | Command body contains an explicit `Skill("PACT:unwatch-inbox")` invocation as a hook-silent-fail safety net. Active-task count is naturally 0 at this point, so the Teardown is harmless and useful as a catch for the rare case where the PostToolUse 1→0 directive was missed. |
| **Manual user invocation** | User types `/PACT:watch-inbox` or `/PACT:unwatch-inbox` in chat | Debug/recovery surface. Idempotent on watch-inbox (STATE_FILE-present check no-ops); best-effort on unwatch-inbox (tolerates already-stopped Monitor). Useful for re-arming after silent Monitor death or silencing Monitor noise mid-session. |
| **Registry cleanup** | `SessionEnd` fires (any session-termination path including force-termination) | `session_end.py::cleanup_wake_registry(team_name)` unlinks `inbox-wake-state.json` if present. Hook cannot reach `TaskStop` — Monitor process dies with the session. |

The PostToolUse hook is the **primary** lifecycle mechanism. The `/wrap-up` command body is the **only command-file callsite** — a hook-silent-fail safety net for the all-tasks-completed exit. `/pause` and `/imPACT` deliberately do NOT invoke Teardown: those commands run with active teammate tasks remaining, and the lead's Monitor must stay armed for them. The watch-inbox command's idempotent cold-start check + unwatch-inbox's best-effort Teardown make the /wrap-up safety-net fire harmless even when the PostToolUse 1→0 directive already ran.

---

## 2. 30/60/120 Behavioral Verification

The Monitor's state machine has three timing constants:

| Constant | Value | Meaning |
|---|---|---|
| `POLL` | 30 s | Polling cadence; granularity floor for grow detection |
| `QUIET_REQUIRED` | 60 s | Required consecutive quiet seconds before LAST_GROW emits |
| `MAX_DELAY` | 120 s | Sustained-traffic ceiling; emits MAX_DELAY edge if a burst never reaches QUIET_REQUIRED |

The state machine emits `INBOX_GREW size=… ts=… edge=…` lines on three edges: `FIRST_GROW`, `LAST_GROW`, `MAX_DELAY`. Each emit ends the turn (stdout discipline).

### 2.1 Verify FIRST_GROW + LAST_GROW (single discrete burst)

In a fresh session with at least one active teammate task (Monitor armed):

1. Send a single message to the lead's inbox from a teammate (e.g., a peer ping).
2. Observe the lead wakes within ~30–60 s with one `INBOX_GREW … edge=FIRST_GROW` emit.
3. Wait 60+ s with no further inbox writes.
4. Observe the lead wakes again with one `INBOX_GREW … edge=LAST_GROW` emit.

Two emits per discrete burst is the design target. Three or more emits within a quiet window indicates a state-machine regression.

### 2.2 Verify MAX_DELAY ceiling (sustained traffic)

1. Have a peer write to the lead's inbox repeatedly at intervals < 30 s for at least 130 s (preventing the burst from reaching QUIET_REQUIRED).
2. Observe `INBOX_GREW … edge=FIRST_GROW` at the start of the burst.
3. Observe `INBOX_GREW … edge=MAX_DELAY` after ~120 s of sustained traffic.
4. After traffic stops and 60 s of quiet pass, observe `INBOX_GREW … edge=LAST_GROW`.

MAX_DELAY caps unbounded suppression — without it, sustained traffic produces zero LAST_GROW emits and the lead never wakes during the burst.

### 2.3 Smoke-check the Monitor process

```
ps -ef | grep '[i]nbox-wake' | head -5
```

A persistent `Monitor`-spawned shell holding the `while true; sleep 30` loop should appear once the lead is armed. Process count > 1 for the same team indicates a concurrent re-arm orphan (rare; cleaned up by next Teardown).

---

## 3. Failure-Mode Diagnosis

### 3.1 Silent Monitor death

Symptom: lead is in a session with active tasks but never wakes on inbox grow. Manual SendMessage from a peer does not surface until lead idles for unrelated reasons.

Diagnosis:

```
cat ~/.claude/teams/{team_name}/inbox-wake-state.json
```

If the file shows `v=1` and a `monitor_task_id`, the lead believes it is armed but the Monitor process has died. This is the **silent death** failure mode — undetectable in-session.

Recovery: invoke `Skill("PACT:watch-inbox")` manually on the lead. watch-inbox sees STATE_FILE present and is a no-op by default; force re-arm by first deleting the STATE_FILE:

```
rm ~/.claude/teams/{team_name}/inbox-wake-state.json
```

then invoke Arm again. The next cold-start writes a fresh STATE_FILE and spawns a new Monitor.

### 3.2 Long single-tool calls block wake delivery

Symptom: lead is mid-turn in a single long-running tool call (e.g., a 90-second blocking `sleep` or a long `Bash` command); peer messages do not surface until the tool returns.

Diagnosis: this is **expected behavior, not a failure**. The wake mechanism's promise is "messages surface between tool calls within a turn," not "instant interrupt anywhere." Verified empirically 2026-04-30T00:00–00:02Z (skill body §Failure Modes).

Mitigation: prefer multiple short tool calls over one long blocking call when wake responsiveness matters. For unavoidable long calls, accept the bounded latency.

### 3.3 Malformed STATE_FILE

Symptom: STATE_FILE exists but parse fails; Arm cold-starts unexpectedly on every Arm directive.

Diagnosis:

```
cat ~/.claude/teams/{team_name}/inbox-wake-state.json
python3 -c 'import json,sys; print(json.load(open(sys.argv[1])))' \
  ~/.claude/teams/{team_name}/inbox-wake-state.json
```

If the file content is not valid JSON or `v != 1`, Arm correctly treats it as not-armed and cold-starts (overwriting on atomic-rename write). This is **fail-safe, not fail-broken** — the next cold-start corrects state.

Recovery: none required. The next Arm directive cold-starts cleanly; the malformed file is overwritten.

### 3.4 Schema-rejection of hook output

Symptom: PostToolUse fires (visible in `~/.claude/sessions/{sid}/transcript.jsonl` if available) but no Arm/Teardown directive lands in the lead's next turn.

Diagnosis: check `wake_lifecycle_emitter.py` JSON output schema. Required field is `hookSpecificOutput.hookEventName: "PostToolUse"`. Missing → silent schema-validation rejection by the platform. Verified empirically in PREPARE probe round 2.

Recovery: not an operator concern — this is a code regression. File a bug; the structural test in `pact-plugin/tests/test_inbox_wake_lifecycle.py` should catch this before merge.

### 3.5 Concurrent re-arm

Symptom: two Monitor processes for the same team (`ps -ef | grep '[i]nbox-wake'` shows multiple).

Diagnosis: rare race between PostToolUse first-active-transition Arm and SessionStart-resume Arm. Atomic-rename STATE_FILE write makes corruption impossible; the second cold-start wins, leaving the first Monitor's `monitor_task_id` orphaned in the harness task list.

Recovery: next Teardown's `TaskStop(STATE_FILE.monitor_task_id)` only stops the winner. Orphan dies with session-process termination. `cleanup_wake_registry` covers the registry sidecar regardless.

---

## 4. STATE_FILE Inspection

The STATE_FILE lives at `~/.claude/teams/{team_name}/inbox-wake-state.json` with exactly 3 fields:

```json
{
  "v": 1,
  "monitor_task_id": "<task-id-returned-by-Monitor>",
  "armed_at": "2026-04-30T05:42:17Z"
}
```

### 4.1 Quick inspection commands

Show armed state:

```
cat ~/.claude/teams/{team_name}/inbox-wake-state.json
```

Pretty-print + extract the Monitor task id:

```
python3 -c 'import json; d=json.load(open("'"$HOME"'/.claude/teams/{team_name}/inbox-wake-state.json")); print(d.get("monitor_task_id"))'
```

Verify the inbox file the Monitor watches:

```
ls -la ~/.claude/teams/{team_name}/inboxes/team-lead.json
wc -c < ~/.claude/teams/{team_name}/inboxes/team-lead.json
```

The `wc -c` byte size is exactly what the Monitor compares against on each poll. A growing byte count between polls is the wake trigger.

### 4.2 Force-termination cleanup verification

After force-terminating a session (kill -9 the parent, system crash, etc.), the STATE_FILE may be left behind. On next session start, `cleanup_wake_registry(team_name)` runs in `session_end.py` and unlinks the stale STATE_FILE. Verify:

```
ls ~/.claude/teams/{team_name}/inbox-wake-state.json 2>&1
```

Should return `No such file or directory` after a clean session-end pass.

---

## 5. End-to-End Runbook (Fresh Session)

The minimum cycle to confirm the mechanism works in a fresh session after merge:

1. Start a fresh session in a project with the plugin installed at the merged version.
2. Run `/PACT:orchestrate` or `/PACT:comPACT` with any small feature; observe a teammate task is created.
3. Verify `wake_lifecycle_emitter.py` fired Arm: check the lead's next turn for `additionalContext` text "Invoke Skill('PACT:watch-inbox')".
4. Confirm STATE_FILE exists: `ls ~/.claude/teams/{team_name}/inbox-wake-state.json`.
5. Have the teammate idle, then send a peer ping that grows the lead's inbox.
6. Observe the lead wakes with `INBOX_GREW … edge=FIRST_GROW`.
7. After 60 s quiet, observe `INBOX_GREW … edge=LAST_GROW`.
8. Complete all teammate tasks; verify Teardown directive lands and STATE_FILE is unlinked.
9. Run `/PACT:wrap-up`; verify (idempotent) the hook-silent-fail safety-net Teardown invocation completes without error against an already-torn-down state.

A successful run hits all 9 steps. Step failures map to the failure modes in §3.
