# Hook-stdin role discriminators

Which stdin field tells a hook whether it is running in the **team-lead**, a
**teammate**, or a **plain / non-PACT** process — and which fields look usable
but are not. Read this before writing any predicate that branches on session
role.

## The one rule

**`agent_type` is the universal role discriminator.** It is the only field
present and correct on every hook event, in every process, under the tmux
(separate-process) teammate topology. The signal is **value-membership, not
field-presence**:

| Role | `agent_type` value |
|------|--------------------|
| team-lead | `PACT:pact-orchestrator` **or** `pact-orchestrator` (both spellings the harness can stamp) |
| teammate | the specialist value, e.g. `pact-architect`, `pact-backend-coder` |
| plain / non-PACT primary | **field absent** |

`pact_context.is_lead()` / `classify_session_role()` are the single resolvers;
both test exact membership of `agent_type` in `LEAD_AGENT_TYPES`. A
`startswith("pact-")` test is WRONG — it misclassifies the unqualified lead
spelling `pact-orchestrator` as a teammate.

### Do NOT key a role decision on these

- **`agent_id` / `agent_name`** — ABSENT on tmux hook stdin (a tmux teammate
  frame and the lead's frame carry no `agent_id`). (On some older bundles a
  teammate `PostToolUse` frame carried `agent_id`; it is not dependable across
  bundles. `is_lead` deliberately never reads it.) **Mode-dependent (Claude Code
  2.1.177, captured):** an *in-process* subagent `PreToolUse` frame DOES carry
  `agent_id` — under the in-process topology the subagent shares the lead's
  `session_id` (the identity collapse), so `agent_id` is the only in-frame
  differentiator there — but it is ABSENT on tmux-teammate and lead frames.
  Present in one topology and absent in the other, it is NOT a reliable
  cross-mode role signal: key role on `agent_type` (present in both), never
  `agent_id`.
- **`team_name`** — absent on most events; present on stdin only for a
  **teammate `TaskCompleted`** frame (see the table). It identifies the team,
  not the role, and it is NOT a "this is a teammate" flag you can rely on for
  any other event.
- **`teammate_name`** — present only on `TaskCompleted` (and `TeammateIdle`),
  and only for a teammate. Not a general role signal.

## Per-event truth table

Values below are grounded in verbatim stdin captured under tmux (Claude Code
2.1.167) for **SessionStart, UserPromptSubmit, PostToolUse, and TaskCompleted**,
and under Claude Code 2.1.177 for **PreToolUse** (three real frames: a tmux
teammate, a lead, and an in-process subagent — confirming `agent_type` is stamped
on `PreToolUse` in both topologies). Only the **PostCompact** row is now NOT
separately captured (marked `†`): its `agent_type` shape is inferred from the
uniform harness-stamping the captured frames establish (PostCompact has only a
synthesized-from-matrix builder). "journal-resolvable in this process?" = does
`session_journal.get_journal_path()` return a non-empty path — i.e. can THIS
process write the canonical session journal. It is **process-scoped**: a
teammate process has no persisted session-context file, so its journal path is
empty.

| Hook event | Role field | Lead value | Teammate value | Plain | `team_name` in stdin? | journal-resolvable here? |
|---|---|---|---|---|---|---|
| SessionStart | `agent_type` | lead spelling | `pact-<specialist>` | absent | no | lead: yes (persists context) · teammate: no |
| UserPromptSubmit | `agent_type` | lead spelling | *(no teammate fire path — see note)* | absent | no | lead: yes |
| PreToolUse | `agent_type` | lead spelling | `pact-<specialist>` | — | **no** | lead: yes · teammate: no |
| PostToolUse (incl. `TaskCreate` / `TaskUpdate`) | `agent_type` | lead spelling | `pact-<specialist>` | — | **no** | lead: yes · teammate: no |
| TaskCompleted | `agent_type` | lead spelling | `pact-<specialist>` | — | lead: **no** · teammate: **yes** (also `teammate_name`) | lead: yes · teammate: no |
| PostCompact `†` | `agent_type` | lead spelling | `pact-<specialist>` | — | no | lead: yes · teammate: no |

`†` PostCompact is NOT separately captured this campaign; its `agent_type` shape
is inferred from the uniform harness-stamping the captured SessionStart /
UserPromptSubmit / PostToolUse / TaskCompleted / PreToolUse frames establish.
`is_lead` is READ on PreToolUse and PostCompact (and SessionStart /
UserPromptSubmit / PostToolUse) but is NOT read on TaskCompleted — that frame is
captured for the #917 emit-path, which gates on `team_name` + journal
writability rather than this predicate. On PreToolUse, `is_lead` is read via the
match-all `bootstrap_gate` (matcher `''`, so it fires before every tool —
including `TaskCreate` / `TaskUpdate`) and the `Edit` / `Write` pin gates; there
is no `TaskCreate` / `TaskUpdate`-matched PreToolUse hook (that matcher is
PostToolUse-only — `task_lifecycle_gate`, the row above).

### UserPromptSubmit has no teammate fire path

An `Agent`-spawned team teammate never fires `UserPromptSubmit`: it wakes via
inbox / `SendMessage` (a context injection, not a hookable tool). So the
`if not is_lead: return` guard in the bootstrap hooks
(`bootstrap_marker_writer.py`, `bootstrap_prompt_gate.py`) is a **plain /
non-PACT primary-session** guard — it is NOT discriminating teammates. (A
*headless* `--agent pact-<specialist> -p` launch is a primary process, not a
team teammate, and CAN fire `UserPromptSubmit` — but that is a different launch
mode, not the team topology.)

### `UserPromptSubmit` carries no `source`

`source` (e.g. `"startup"`) is a **SessionStart-only** field. A real
`UserPromptSubmit` frame does not carry it. A fixture or test that asserts
`source` on a `UserPromptSubmit` frame is modeling a shape the platform does
not deliver.

## Why marker-claim and journal-write must share a precondition

The two facts a teammate `TaskCompleted` frame exposes together are the trap:
its stdin **carries `team_name`** (enough to resolve a team-scoped path) while
its process **cannot resolve the canonical journal** (no persisted
session-context file → empty journal path). A side-effect that resolves the
first precondition but not the second — for example, claiming a team-scoped
dedup marker and then writing the journal — will **claim without writing**: the
marker is taken, the write is lost, and a later writable process (the lead) is
permanently suppressed by the now-poisoned marker.

The rule that follows: **any optimistic "we did it" marker must be gated on the
writability of the thing it promises.** Resolve the marker-claim precondition
and the write precondition from the **same** process-scoped signal
(`get_journal_path()` non-empty), so a process that cannot write defers to one
that can rather than poisoning the shared marker. Do not substitute `is_lead`
for this: the load-bearing question is "can THIS process write the journal," not
"is this the lead" — they usually coincide but the writability test is the
precise one.

## See also

- `HOOK_INPUT_CONVENTIONS.md` (sibling) — conventions for consuming
  `hook_event_name` and pinning the verbatim platform stdin shape.
- `pact-plugin/tests/fixtures/role_frames.py` — the committed real captured
  frames substantiating this table (the `captured_*` accessors, including the
  Claude Code 2.1.177 `PreToolUse` captures: tmux teammate, lead, and in-process
  subagent), each carrying `_meta.capture_method` provenance.
- `pact-plugin/hooks/shared/pact_context.py` — `is_lead` / `classify_session_role`
  (the resolvers) and `get_journal_path` resolution via the session context.

---

*Background: the discriminator audit and the marker-poisoning failure it
explains are tracked under #812 (audit) and #917 (the emit-path bug);
teammate-context non-persistence under tmux is #877. These pointers are
provenance only — the behavioral facts above stand on their own.*
