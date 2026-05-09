# Bootstrap Secretary-Dispatch Runbook (#691)

End-to-end operator runbook for a fresh-session bootstrap, focused on the
Teachback-Gated Dispatch for the secretary spawn (gate-enforced; Task A
teachback + Task B work, `blockedBy=[A]`). Use this to verify that the
orchestrator persona's
Agent Teams Dispatch section and the bootstrap command's secretary-spawn
step stay aligned with what `dispatch_gate.py` and `task_lifecycle_gate.py`
actually enforce. A regression in either document re-introduces friction
on the highest-frequency dispatch in PACT (every session start), so this
runbook is the structural net.

The runbook validates four assertions on the secretary spawn:

- Zero `dispatch_gate` refusals with `rule="no_task_assigned"` (rule ⑧
  satisfied — Task A and Task B both pre-exist with `owner="secretary"`).
- Zero `long_inline_mission` WARNs (rule ⑨ — mission lives in
  `TaskCreate(description=...)`; the spawn prompt stays terse and
  references `TaskList`).
- Task A and Task B exist with correct ownership and the correct
  blocking edge — Task A `addBlocks=[B_id]`; Task B
  `addBlockedBy=[A_id]`, `status="pending"` until Task A completes.
- The secretary delivers the session briefing within the wake
  immediately following Task A acceptance — i.e., the briefing
  `SendMessage` lands on the secretary's first turn after the team-lead
  flips Task A to `completed`.

After each execution, append a row to
[`RUNBOOK_RUN_DATES.md`](RUNBOOK_RUN_DATES.md) under the section header
matching this runbook's filename.

Implementation references:

- Persona dispatch contract: [pact-plugin/agents/pact-orchestrator.md](../../agents/pact-orchestrator.md) §Agent Teams Dispatch
- Persona completion contract: [pact-plugin/agents/pact-orchestrator.md](../../agents/pact-orchestrator.md) §Completion Authority, Teachback Review & Intentional Waiting
- Bootstrap ritual: [pact-plugin/commands/bootstrap.md](../../commands/bootstrap.md) §Step 2 — Spawn `pact-secretary`
- Dispatch gate: `pact-plugin/hooks/dispatch_gate.py` (rule ⑧
  `has_task_assigned`, rule ⑨ `long_inline_mission`)
- Task-lifecycle gate: `pact-plugin/hooks/task_lifecycle_gate.py`
  (TaskCreate teachback `addBlocks` + work-task `addBlockedBy` advisories)
- Secretary briefing surface: [pact-plugin/agents/pact-secretary.md](../../agents/pact-secretary.md) §Session-Start Behavior
- Companion runbook for marker-write diagnostics:
  [662-dispatch-gate.md](662-dispatch-gate.md) — out of scope here

This runbook does NOT cover bootstrap-marker mechanics. The
secretary spawn happens at-or-before `bootstrap_marker_writer.py` writes
the marker; marker-fingerprint regressions are diagnosed in
`662-dispatch-gate.md` §2. This runbook tests dispatch shape only.

---

## Prerequisites

1. Plugin version ≥ 4.1.5 installed at
   `~/.claude/plugins/cache/pact-marketplace/PACT/<version>/` (the
   bundled fix lands in the version that ships #691).
2. No live session for the test team. Inspect
   `~/.claude/teams/{team_name}/config.json` and remove if present, OR
   choose a fresh team name not yet on disk.
3. No stale paused-state for the chosen team:
   ```
   ls ~/.claude/teams/{team_name}/paused-state.json 2>&1
   ```
   Should return `No such file or directory`. If present, the
   orchestrator surfaces it before the secretary spawn and Step 4 below
   does not execute cleanly.
4. Start a **fresh** `claude --agent PACT:pact-orchestrator` session in
   a project that has the plugin installed. Do not reuse a session that
   authored merge of the #691 PR — its hook registrations are stale.

---

## Section 1 — Gate-clean secretary spawn

**Goal**: confirm the orchestrator's bootstrap secretary-spawn produces
zero `dispatch_gate` refusals and zero `long_inline_mission` WARNs.

**Verification surface**: §1 inspects `session-journal.jsonl` because
`dispatch_decision` events are the structural pin for the
gate-fired-correctly assertion; §2-§4 inspect on-disk task and inbox
files which are the canonical ground truth for structural shape and
acceptance pair.

**Steps**:

1. In the fresh session, allow the orchestrator to execute its first
   action (it should auto-invoke `Skill("PACT:bootstrap")`). Observe
   the session-init system reminder is delivered.
2. Watch the orchestrator's tool calls during bootstrap Step 2. The
   canonical sequence per the persona's Agent Teams Dispatch contract
   is five calls:
   1. `TaskCreate(subject="Secretary teachback: ...", ...)` — Task A
   2. `TaskCreate(subject="Session briefing + ...", description=...)` — Task B (mission lives here)
   3. `TaskUpdate(taskId=A, owner="secretary", addBlocks=[B])`
   4. `TaskUpdate(taskId=B, owner="secretary", addBlockedBy=[A])`
   5. `Agent(name="secretary", team_name=<team>, subagent_type="pact-secretary", prompt="YOUR PACT ROLE: teammate (secretary).\n\nYou are joining team <team>. Check `TaskList` for tasks assigned to you.")`
3. Inspect the session journal for any `dispatch_decision` event:
   ```
   tail -50 ~/.claude/pact-sessions/<project>/<sid>/session-journal.jsonl \
     | python3 -c 'import sys,json; \
       [print(json.dumps(json.loads(l), indent=2)) for l in sys.stdin if "dispatch_decision" in l]'
   ```

**Pass criteria**:

- [ ] No `permissionDecisionReason` containing
      `"PACT dispatch_gate: no Task assigned to owner='secretary'"` in
      the orchestrator's tool call output (rule ⑧ did not deny).
- [ ] No `permissionDecisionReason` or `additionalContext` containing
      `"PACT dispatch_gate: prompt is long"` (the user-visible WARN
      message text from rule ⑨).
- [ ] No `dispatch_decision` journal event with `rule="long_inline_mission"`
      (rule ⑨ did not WARN or DENY at the journal level).
- [ ] Either no `dispatch_decision` events in the journal for the
      secretary spawn, OR the events present are
      `decision="ALLOW"` only.
- [ ] The `Agent()` tool call succeeded — secretary process is alive.

**Failure signals**:

- A `dispatch_decision` event with `decision="DENY"` and
  `rule="no_task_assigned"`: persona drift back to the three-step
  pattern (Task B not pre-created before spawn) OR the bootstrap.md
  Step 2 sequence drifted to omit the prerequisite tasks.
- A `dispatch_decision` event with `rule="long_inline_mission"` (any
  decision): the persona's Agent Teams Dispatch section example prompt
  exceeds 800 chars OR omits the `TaskList` reference phrase. The
  mission is leaking into the spawn prompt instead of
  `TaskCreate(description=...)`.

If either signal fires, do NOT proceed to Section 2 — the bootstrap
ritual is mis-aligned with the gates and the regression is the runbook's
primary detection target.

---

## Section 2 — Task A and Task B structural shape

**Goal**: confirm both tasks exist with correct ownership and the
correct blocking edge before any acceptance happens.

**Steps**:

1. Immediately after the secretary spawn (before Task A acceptance),
   inspect the team's task list. Task IDs are the JSON filenames
   (basename without `.json`); map each ID to its subject with:
   ```
   ls ~/.claude/tasks/{team_name}/
   for f in ~/.claude/tasks/{team_name}/*.json; do echo "$(basename $f .json): $(jq -r .subject $f)"; done
   ```
   Two task JSON files should be present (Task A teachback + Task B
   work). Use the Task A and Task B IDs in the steps below as
   `<A_id>` / `<B_id>`.
2. Read both task files:
   ```
   cat ~/.claude/tasks/{team_name}/<A_id>.json | python3 -m json.tool
   cat ~/.claude/tasks/{team_name}/<B_id>.json | python3 -m json.tool
   ```
3. Verify the structural pins listed below.

**Pass criteria**:

- [ ] Task A: `subject` contains `"teachback"` (case-insensitive),
      `owner == "secretary"`, `blocks` contains `<B_id>`,
      `status == "in_progress"` or `"pending"` (the secretary may have
      already claimed it on first turn).
- [ ] Task B: `owner == "secretary"`, `blockedBy` contains `<A_id>`,
      `status == "pending"`.
- [ ] Task B's `description` carries the mission text (the long-form
      briefing scope) — not Task A.
- [ ] Neither task has the inverse pin: Task A is NOT
      `blockedBy=[<B_id>]`; Task B is NOT `addBlocks=[<A_id>]`.

**Failure signals**:

- Only one task on disk: persona / bootstrap.md drift to the legacy
  single-task pattern.
- Task A `addBlocks` empty, or Task B `addBlockedBy` empty:
  `task_lifecycle_gate.py` advisories
  (`teachback_addblocks_missing`, `work_addblockedby_missing`) should
  have fired during bootstrap; check the journal.
- Mission in Task A's `description` instead of Task B's: persona
  drifted on the "mission lives in Task B" convention.

---

## Section 3 — Task A acceptance and Task B unblocking

**Goal**: confirm the team-lead-driven acceptance two-call pair
correctly transitions Task A to `completed` AND wakes the secretary, and
that Task B becomes claimable.

**Steps**:

1. The orchestrator should detect the secretary's
   `metadata.teachback_submit` write (delivered in the secretary's first
   turn) and apply the Acceptance two-call atomic pair documented in
   the persona's Completion Authority section (SendMessage FIRST per
   the lifecycle-gate ordering invariant):
   1. `SendMessage(to="secretary", "[team-lead→secretary] Task accepted...", summary="Task accepted")`
   2. `TaskUpdate(taskId=<A_id>, status="completed")`
2. Inspect Task A on disk after acceptance:
   ```
   cat ~/.claude/tasks/{team_name}/<A_id>.json | python3 -c \
     'import sys,json; d=json.load(sys.stdin); print(d.get("status"), d.get("metadata",{}).get("teachback_submit") is not None)'
   ```
3. Inspect Task B status — should now be claimable
   (`blockedBy` resolution invisible to the teammate without the wake,
   but the structural state on disk shows the upstream task completed):
   ```
   cat ~/.claude/tasks/{team_name}/<B_id>.json | python3 -c \
     'import sys,json; d=json.load(sys.stdin); print(d.get("status"), d.get("blockedBy"))'
   ```

**Pass criteria**:

- [ ] Task A `status == "completed"` and
      `metadata.teachback_submit` is present (the teammate's payload
      from before acceptance — preserved through completion).
- [ ] Task B's upstream Task A is now in terminal state — the teammate
      can claim Task B on its next turn.
- [ ] The `SendMessage` from team-lead to secretary landed (visible in
      `~/.claude/teams/{team_name}/inboxes/secretary.json` byte size
      growth or the inbox content directly).

**Failure signals**:

- Task A `status == "completed"` but no paired `SendMessage` to
  secretary: `task_lifecycle_gate.py` advisory
  `completion_no_paired_send` should have fired in the journal. The
  secretary will idle indefinitely on `awaiting_lead_completion`.
- Task A still `pending` / `in_progress` after the orchestrator's
  acceptance turn: persona drift on the Completion Authority two-call
  pair OR the orchestrator missed the teachback signal.

---

## Section 4 — Secretary briefing within one wake

**Goal**: confirm the secretary delivers the session briefing within the
wake immediately following Task A acceptance.

**Steps**:

1. After the team-lead's acceptance two-call pair (Section 3), wait for
   the secretary's next turn. The secretary's first action after
   acceptance should be to claim Task B and deliver the session briefing
   via `SendMessage` to team-lead.
2. Inspect the team-lead's inbox immediately after the secretary's wake:
   ```
   cat ~/.claude/teams/{team_name}/inboxes/team-lead.json
   ```
3. Look for a message from `secretary` containing briefing-shape content
   (recent project context, Working Memory cleanup summary, calibration
   data, optional compact-summary findings).

**Pass criteria**:

- [ ] Within the secretary's first turn after Task A acceptance, an
      inbox message from `secretary → team-lead` is delivered
      containing session-briefing language (any of: "session briefing",
      "Working Memory", "recent project context", calibration summary).
- [ ] No interleaving teammate-side `metadata.handoff_rejection` write
      on Task B (the secretary should NOT reject — Task B description
      is the briefing scope, not a contested mission).

**Failure signals**:

- The secretary's first turn after acceptance contains no `SendMessage`
  to team-lead: the secretary spawn may have failed to load the
  pact-secretary persona (regression in agent frontmatter) or the
  acceptance wake `SendMessage` did not land (Section 3 failure
  cascading).
- A briefing arrives multiple turns later: this is a soft signal —
  briefing scope may exceed one turn (extensive Working Memory cleanup,
  large calibration corpus). NOT a hard fail; document in the run row.

---

## Section 5 — Acceptance summary

A successful run hits all four sections. Append the result row to
`RUNBOOK_RUN_DATES.md` under
`## 691-bootstrap-secretary-dispatch.md` with: ISO date in UTC,
operator name, plugin version under test, sections passed (out of 4),
inline-mission mode setting in effect (`warn` / `deny` / `shadow`), and
any per-section observations.

If §1 fails (gate refusal or `long_inline_mission` fired): regression in
the persona's Agent Teams Dispatch section OR the bootstrap command's
Step 2. File a P1 issue and revert the offending commit. The bootstrap
is the highest-frequency dispatch in PACT — every session start hits it
— so a §1 regression compounds across every operator session.

If §2 fails: structural shape drift. Inspect the journal for
`task_lifecycle_gate` advisories — they should have fired but did not
block. File a P1 issue (gate weakening regression) or P2 (persona drift,
gate still catches it advisorily).

If §3 fails: Completion Authority two-call pair drift. File a P2 issue;
the secretary will idle on every session until the persona is corrected.

If §4 fails (briefing missing entirely): pact-secretary persona
regression. File a P2 issue against the secretary agent definition.

---

## Section 6 — Troubleshooting (false-positive failure modes)

### 6.1 Briefing delayed past one turn

The secretary's session-start behavior includes Working Memory cleanup
plus calibration corpus search plus optional compact-summary processing.
On a session with a large calibration corpus or a pending
`compact-summary.txt`, the briefing may legitimately span two turns —
the secretary delivers a partial briefing on turn 1 and continues in
turn 2. Section 4 records this as a soft signal, not a hard fail.

### 6.2 Existing team config from prior session

If the team already exists on disk
(`~/.claude/teams/{team_name}/config.json` present), the
bootstrap-ritual reuses the team and skips `TeamCreate`. The secretary
spawn still proceeds through the Teachback-Gated Dispatch sequence — Section 1
still applies. Confirm via the orchestrator's tool calls that the
sequence executed regardless of team-create vs reuse path.

### 6.3 Paused state surfaces before secretary spawn

If `~/.claude/teams/{team_name}/paused-state.json` exists at session
start, the bootstrap ritual surfaces it to the user BEFORE Step 2.
Section 1's pass criteria do not apply until the user resolves the
paused-state choice. Treat this as a "runbook prerequisite not met"
rather than a §1 failure — re-run after clearing paused state per
prerequisites above.

### 6.4 Inline-mission WARN with `PACT_DISPATCH_INLINE_MISSION_MODE=shadow`

If the operator has `PACT_DISPATCH_INLINE_MISSION_MODE=shadow` set in
the shell environment, rule ⑨ returns `ALLOW` and emits a journal event
without surfacing an `additionalContext` advisory. Section 1's pass
criteria still hold (no DENY, no WARN-shaped advisory text), but the
journal will record `decision="ALLOW"` with `rule="long_inline_mission"`.
This is an environment-mode artifact, not a regression. Document the
mode in the run row's "inline-mission mode" column. The companion
runbook [662-dispatch-gate.md](662-dispatch-gate.md) §3.1 covers shadow
mode in detail.

### 6.5 Hook registration stale (in-session author)

Hooks load at session start, not on file change — Claude Code reads
`pact-plugin/hooks/*.py` once when the session begins, so a session that
authored the merge of #692 will be running the OLD hook code (per pinned
CLAUDE.md memory `4fa2311 → 27aa95e`). Re-running this runbook in that
same session will produce a phantom-green result.

If you authored the #691 merge in this session, your hook registrations
are stale and the gates may not fire even if the persona/bootstrap.md
remained mis-aligned. Section 1 pass under stale hooks is **not** a
true pass. Restart in a fresh session post-merge before recording a
runbook row.
