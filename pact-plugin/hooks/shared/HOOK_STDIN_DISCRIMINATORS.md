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

- **`agent_id` / `agent_name`** — ABSENT on tmux hook stdin. (On some older
  bundles a teammate `PostToolUse` frame carried `agent_id`; it is not
  dependable across bundles. `is_lead` deliberately never reads it.)
- **`team_name`** — absent on most events; present on stdin only for a
  **teammate `TaskCompleted`** frame (see the table). It identifies the team,
  not the role, and it is NOT a "this is a teammate" flag you can rely on for
  any other event.
- **`teammate_name`** — present only on `TaskCompleted` (and `TeammateIdle`),
  and only for a teammate. Not a general role signal.

## Per-event truth table

Values below are from verbatim stdin captured under tmux (Claude Code 2.1.167).
"journal-resolvable in this process?" = does `session_journal.get_journal_path()`
return a non-empty path — i.e. can THIS process write the canonical session
journal. It is **process-scoped**: a teammate process has no persisted
session-context file, so its journal path is empty.

| Hook event | Role field | Lead value | Teammate value | Plain | `team_name` in stdin? | journal-resolvable here? |
|---|---|---|---|---|---|---|
| SessionStart | `agent_type` | lead spelling | `pact-<specialist>` | absent | no | lead: yes (persists context) · teammate: no |
| UserPromptSubmit | `agent_type` | lead spelling | *(no teammate fire path — see note)* | absent | no | lead: yes |
| PreToolUse / PostToolUse (incl. `TaskCreate` / `TaskUpdate`) | `agent_type` | lead spelling | `pact-<specialist>` | — | **no** | lead: yes · teammate: no |
| TaskCompleted | `agent_type` | lead spelling | `pact-<specialist>` | — | lead: **no** · teammate: **yes** (also `teammate_name`) | lead: yes · teammate: no |
| PostCompact | `agent_type` | lead spelling | `pact-<specialist>` | — | no | lead: yes · teammate: no |

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
  frames substantiating this table (the `captured_*` accessors), each carrying
  `_meta.capture_method` provenance.
- `pact-plugin/hooks/shared/pact_context.py` — `is_lead` / `classify_session_role`
  (the resolvers) and `get_journal_path` resolution via the session context.

---

*Background: the discriminator audit and the marker-poisoning failure it
explains are tracked under #812 (audit) and #917 (the emit-path bug);
teammate-context non-persistence under tmux is #877. These pointers are
provenance only — the behavioral facts above stand on their own.*
