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
| teammate (tmux) | the specialist value, e.g. `pact-architect`, `pact-backend-coder` |
| teammate (in-process) | **the teammate's own `name`**, e.g. `background-work-coder` — NOT the configured `agentType`. See the mode split below. |
| plain / non-PACT primary | **field absent** |

`pact_context.is_lead()` / `classify_session_role()` are the single resolvers;
both test exact membership of `agent_type` in `LEAD_AGENT_TYPES`. A
`startswith("pact-")` test is WRONG — it misclassifies the unqualified lead
spelling `pact-orchestrator` as a teammate.

#### 🔴 `agent_type` IS NOT ALWAYS A TYPE — the in-process/tmux split

**An earlier version of this table said the teammate value is "the specialist
value" with no mode split. MEASURED FALSE 2026-09-11.** On a live in-process
Agent-Teams teammate `PostToolUse` `Bash` frame, `agent_type` carried the
teammate's **own name** (`background-work-coder`), while that member's
`agentType` in the team config was `pact-backend-coder`. **The frame's
`agent_type` and the config's `agentType` are different values.**

Fixture: `tests/fixtures/role_frames.py` ::
`captured_posttooluse_teammate_inprocess_bash_background` (real key set,
synthetic values).

Consequences, because code in this repo reasons on the old premise:

- **Role classification is unaffected.** Both resolvers test membership in
  `LEAD_AGENT_TYPES`, and neither a name nor a type is in that set, so a
  teammate still classifies as a teammate either way.
- **Any code treating this field AS A TYPE is on a false premise** — including
  `resolve_agent_name`'s Step 4, which strips a `pact-` prefix and returns the
  remainder as a name. In-process there is no prefix to strip, so Step 4
  returns the name verbatim and *happens* to be right. That is a coincidence
  of the value, not a property of the step.
- **An argument that excludes a Step-4 type-strip by observing distinct names
  across same-`agentType` members is INVALID.** Step 4 strips the FRAME's
  field, which already differs per member in-process.
- A caller needing identity from this field must VALIDATE it — membership in
  the team config's `members[]` `name` list — rather than trust its shape. See
  `shared/background_work.py` :: `agent_type_names_a_member`, including its
  stated residual.

**tmux is UNTESTED for this field on a `Bash` `PostToolUse` frame.** The tmux
row above rests on a captured `PreToolUse` frame. No tmux team was available
on the machine where this was measured, so the Bash path went unexercised —
**that bounds the verification, not the behaviour.** The tmux row is
therefore the LEAST certain row in this table, and a reader who needs it
should re-measure rather than cite it.

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
  `agent_id`. The member check reads only the exact shape of `agent_id`, to
  tell an in-process teammate from an Agent-tool subagent, and falls back to
  `agent_type` when the id is absent; lead versus teammate never keys on it.
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
on `PreToolUse` in both topologies). **PostCompact** was captured live on
2026-08-26 (#1504 step 0): a lead manual `/compact` in an in-process session —
`agent_type` carries the qualified lead spelling as the matrix inferred, and the
frame also carries `session_id`, `trigger`, `prompt_id`, and a non-empty
`compact_summary` (committed shape: `tests/fixtures/role_frames.py`
`postcompact_lead_manual`). "journal-resolvable in this process?" = does
`session_journal.get_journal_path()` return a non-empty path — i.e. can THIS
process write the canonical session journal. It is **process-scoped**: a
teammate process has no persisted session-context file, so its journal path is
empty.

| Hook event | Role field | Lead value | Teammate value | Plain | `team_name` in stdin? | journal-resolvable here? |
|---|---|---|---|---|---|---|
| SessionStart | `agent_type` (none for an in-process teammate's `source: compact`) | lead spelling | `pact-<specialist>`; an in-process teammate's `source: compact` frame carries the lead spelling | absent | no | lead: yes (persists context) · in-process: yes (the lead's journal) · separate-process: no |
| UserPromptSubmit | `agent_type` | lead spelling | *(no teammate fire path — see note)* | absent | no | lead: yes |
| PreToolUse | `agent_type` | lead spelling | `pact-<specialist>` | — | **no** | lead: yes · teammate: no |
| PostToolUse (incl. `TaskCreate` / `TaskUpdate`) | `agent_type` | lead spelling | `pact-<specialist>` | — | **no** | lead: yes · teammate: no |
| TaskCompleted | `agent_type` | lead spelling | `pact-<specialist>` | — | lead: **no** · teammate: **yes** (also `teammate_name`) | lead: yes · teammate: no |
| PreCompact | none for an in-process teammate | lead spelling | in-process: lead spelling · separate-process: `pact-<specialist>` (inferred) | — | no | not read |
| PostCompact | `agent_type` (none for an in-process teammate) | lead spelling | in-process: lead spelling · separate-process: `pact-<specialist>` (inferred) | — | no | lead: yes · in-process: yes (the lead's journal) · separate-process: no |

PostCompact capture provenance: live append-only hook dump, 2026-08-26, lead
manual `/compact` in the in-process dogfood session (PACT 4.6.44). The committed
verbatim shape is `tests/fixtures/role_frames.py` `postcompact_lead_manual`; its
`session_id` presence is the premise the #1504 session-scoped writer resolves on.
In-process teammates DO compact on their own, and their compaction frames were
captured live on 2026-09-14. PreCompact, SessionStart with `source: compact` and
PostCompact all fire in the lead's process carrying the lead's `agent_type`,
`session_id` and `transcript_path`, and no `agent_id`, `agent_name` or
`agent_transcript_path`. No field separates them from the lead's own compaction
frames, so `is_lead` is True for both. Committed shapes: `tests/fixtures/role_frames.py`
`captured_compaction_teammate_*` and `captured_compaction_lead_*`.
Separate-process (tmux) teammate compaction frames and plain PostCompact shapes
remain matrix-inferred.
`is_lead` is READ on PreToolUse and PostCompact (and SessionStart /
UserPromptSubmit / PostToolUse) but is NOT read on TaskCompleted — that frame is
captured for the #917 emit-path, which gates on `team_name` + journal
writability rather than this predicate. On PreToolUse, `is_lead` is read via the
match-all `bootstrap_gate` (matcher `''`, so it fires before every tool —
including `TaskCreate` / `TaskUpdate`), the `Edit` / `Write` pin gates, and the
`TaskUpdate`-matched `handoff_ordering_gate` (which gates both of its rules on
`is_lead`). `TaskCreate` has no PreToolUse-matched hook — its only
task-lifecycle observer is the PostToolUse-matched `task_lifecycle_gate` (the
row above).

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
