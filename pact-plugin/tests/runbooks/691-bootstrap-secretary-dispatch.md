# Bootstrap Secretary-Dispatch Runbook (#691)

End-to-end operator runbook for a fresh-session bootstrap, focused on the
single-task **teachback-exempt** dispatch for the secretary spawn (the
`pact-secretary` agentType is in `TEACHBACK_EXEMPT_AGENT_TYPES`, so there is
NO Task A teachback — one work-task, `owner="secretary"`, no `blockedBy`).
Use this to verify that the orchestrator persona's Agent Teams Dispatch
section and the bootstrap command's secretary-spawn step stay aligned with
what `dispatch_gate.py` and `task_lifecycle_gate.py` actually enforce. A
regression in either document re-introduces friction on the highest-frequency
dispatch in PACT (every session start), so this runbook is the structural net.

The runbook validates four assertions on the secretary spawn:

- Zero `dispatch_gate` refusals with `rule="no_task_assigned"` (rule ⑧
  satisfied — the single work-task pre-exists with `owner="secretary"`
  before the `Agent()` spawn).
- Zero `long_inline_mission` WARNs (rule ⑨ — mission lives in
  `TaskCreate(description=...)`; the spawn prompt stays terse and
  references `TaskList`).
- Exactly ONE work-task exists with `owner="secretary"` and NO `blockedBy`
  — no Task A teachback task, no blocking edge (the teachback-exemption
  path; `task_lifecycle_gate.work_addblockedby_missing` is SUPPRESSED for
  the secretary via `is_teachback_exempt`).
- The secretary delivers the session briefing on its FIRST turn after
  spawn (no Task-A-acceptance gate) AND self-completes the briefing task
  (`secretary: deliver session briefing`), then stays alive as memory
  consultant + HANDOFF harvester.

After each execution, append a row to
[`RUNBOOK_RUN_DATES.md`](RUNBOOK_RUN_DATES.md) under the section header
matching this runbook's filename.

Implementation references:

- Persona dispatch contract: [pact-plugin/agents/pact-orchestrator.md](../../agents/pact-orchestrator.md) §Agent Teams Dispatch
- Self-completion carve-out: [pact-plugin/agents/pact-orchestrator.md](../../agents/pact-orchestrator.md) §Completion Authority (secretary session briefing + memory-save self-complete carve-out) + [pact-plugin/protocols/pact-completion-authority.md](../../protocols/pact-completion-authority.md)
- Bootstrap ritual: [pact-plugin/commands/bootstrap.md](../../commands/bootstrap.md) §Step 2 — Spawn `pact-secretary` (the SSOT for the current dispatch shape)
- Dispatch gate: `pact-plugin/hooks/dispatch_gate.py` (rule ⑧
  `has_task_assigned`, rule ⑨ `long_inline_mission`)
- Task-lifecycle gate: `pact-plugin/hooks/task_lifecycle_gate.py` (the
  `work_addblockedby_missing` advisory is SUPPRESSED for teachback-exempt
  owners via `is_teachback_exempt`; the `self_completion` advisory is
  SUPPRESSED for the secretary via `is_self_complete_exempt`)
- Secretary briefing surface: [pact-plugin/agents/pact-secretary.md](../../agents/pact-secretary.md) §At Spawn (Session Briefing)
- Companion runbook for marker-write diagnostics:
  [662-dispatch-gate.md](662-dispatch-gate.md) — out of scope here

This runbook does NOT cover bootstrap-marker mechanics. The
secretary spawn happens at-or-before `bootstrap_marker_writer.py` writes
the marker; marker-fingerprint regressions are diagnosed in
`662-dispatch-gate.md` §2. This runbook tests dispatch shape only.

---

## Prerequisites

1. Plugin version ≥ 4.1.5 installed at
   `~/.claude/plugins/cache/pact-marketplace/PACT/<version>/`.
2. No live session for the test team. Inspect
   `~/.claude/teams/{team_name}/config.json` and remove if present, OR
   choose a fresh team name not yet on disk.
3. No stale paused-state for the chosen team:
   ```
   ls ~/.claude/teams/{team_name}/paused-state.json 2>&1
   ```
   Should return `No such file or directory`. If present, the
   orchestrator surfaces it before the secretary spawn and Section 4 below
   does not execute cleanly.
4. Start a **fresh** `claude --agent PACT:pact-orchestrator` session in
   a project that has the plugin installed. Do not reuse a session that
   authored the merge of the bootstrap change under test — its hook
   registrations are stale (see §6.5).

---

## Section 1 — Gate-clean secretary spawn

**Goal**: confirm the orchestrator's bootstrap secretary-spawn produces
zero `dispatch_gate` refusals and zero `long_inline_mission` WARNs.

**Verification surface**: §1 inspects `session-journal.jsonl` because
`dispatch_decision` events are the structural pin for the
gate-fired-correctly assertion; §2-§4 inspect on-disk task and inbox
files which are the canonical ground truth for structural shape and the
self-completion transition.

**Steps**:

1. In the fresh session, allow the orchestrator to execute its first
   action (it should auto-invoke `Skill("PACT:bootstrap")`). Observe
   the session-init system reminder is delivered.
2. Watch the orchestrator's tool calls during bootstrap Step 2. The
   canonical sequence per the persona's Agent Teams Dispatch contract is
   three calls (single-task, teachback-exempt — NO Task A):
   1. `TaskCreate(subject="secretary: deliver session briefing", description=...)` — single work task (mission lives in `description`)
   2. `TaskUpdate(taskId, owner="secretary")` — assign to the secretary; NO `addBlockedBy` (no teachback gate)
   3. `Agent(name="secretary", team_name=<team>, subagent_type="pact-secretary", prompt="YOUR PACT ROLE: teammate (secretary).\n\nYou are joining team <team>. As your FIRST action, Invoke Skill(\"PACT:pact-team-registration\") to record your identity. Then check `TaskList` for tasks assigned to you.")`

   > The secretary spawn prompt carries the register first-action directive
   > (the secretary self-supplies `secretary@<team>` and registers before its
   > briefing). This runbook still validates only the dispatch SHAPE; the
   > register-fires assertion lives in
   > [885-team-registration-smoke.md](885-team-registration-smoke.md).
3. Inspect the session journal for any `dispatch_decision` event:
   ```
   tail -50 ~/.claude/pact-sessions/<project>/<sid>/session-journal.jsonl \
     | python3 -c 'import sys,json; \
       [print(json.dumps(json.loads(l), indent=2)) for l in sys.stdin if "dispatch_decision" in l]'
   ```

**Pass criteria**:

- [ ] No `permissionDecisionReason` containing
      `"PACT dispatch_gate: no Task assigned to owner='secretary'"` in
      the orchestrator's tool call output (rule ⑧ did not deny — the
      single work-task pre-exists with `owner="secretary"`).
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
  `rule="no_task_assigned"`: the work-task was not pre-created with
  `owner="secretary"` before the spawn, OR the bootstrap.md Step 2
  sequence drifted to omit the prerequisite `TaskCreate` + `TaskUpdate`.
- A SECOND task created with a `"teachback"` subject (or a `blockedBy`
  edge on the work-task): drift BACK to the superseded two-task
  teachback-gated shape. The secretary is teachback-exempt — a Task A
  teachback round-trip should NOT be created.
- A `dispatch_decision` event with `rule="long_inline_mission"` (any
  decision): the persona's Agent Teams Dispatch section example prompt
  exceeds 800 chars OR omits the `TaskList` reference phrase. The
  mission is leaking into the spawn prompt instead of
  `TaskCreate(description=...)`.

If any signal fires, do NOT proceed to Section 2 — the bootstrap
ritual is mis-aligned with the gates and the regression is the runbook's
primary detection target.

---

## Section 2 — Single work-task structural shape

**Goal**: confirm exactly ONE work-task exists with correct ownership and
NO blocking edge — the teachback-exempt single-task shape.

**Steps**:

1. Immediately after the secretary spawn, inspect the team's task list.
   Task IDs are the JSON filenames (basename without `.json`); map each
   ID to its subject with:
   ```
   ls ~/.claude/tasks/{team_name}/
   for f in ~/.claude/tasks/{team_name}/*.json; do echo "$(basename $f .json): $(jq -r .subject $f)"; done
   ```
   Exactly ONE secretary task JSON should be present (the work-task). Use
   its ID below as `<work_id>`.
2. Read the task file:
   ```
   cat ~/.claude/tasks/{team_name}/<work_id>.json | python3 -m json.tool
   ```
3. Verify the structural pins listed below.

**Pass criteria**:

- [ ] The work-task: `subject == "secretary: deliver session briefing"`,
      `owner == "secretary"`, `blockedBy` empty (or absent),
      `status == "pending"` or `"in_progress"` (the secretary may have
      already claimed it on its first turn).
- [ ] The work-task's `description` carries the mission text (the
      long-form briefing scope + standing duties).
- [ ] There is NO second task with a `"teachback"` subject, and the
      work-task has NO `addBlocks` / `blockedBy` edge.

**Failure signals**:

- Two tasks on disk (a `"teachback"` Task A + a blocked work-task):
  persona / bootstrap.md drift BACK to the superseded two-task
  teachback-gated pattern. The secretary is teachback-exempt.
- The work-task has a non-empty `blockedBy`: a stray teachback gate was
  applied (`task_lifecycle_gate.work_addblockedby_missing` is suppressed
  for the secretary, so a `blockedBy` here is an over-gating drift).
- Mission text in the spawn prompt instead of the work-task
  `description`: persona drifted on the "mission lives in `description`"
  convention (would also trip rule ⑨ in §1).

---

## Section 3 — Briefing delivery + self-completion on first turn

**Goal**: confirm the secretary delivers the session briefing on its
FIRST turn after spawn (no Task-A-acceptance gate) AND self-completes the
briefing work-task as the final act of delivering the briefing.

**Steps**:

1. After the secretary spawn, wait for the secretary's first turn. Per
   `pact-secretary.md` §At Spawn (Session Briefing), the secretary
   claims the work-task (`status="in_progress"`), delivers the briefing
   via `SendMessage` to team-lead, then self-completes the work-task
   (`TaskUpdate(status="completed")`). There is NO team-lead acceptance
   step — the briefing is a discrete deliverable the secretary owns end
   to end.
2. Inspect the team-lead's inbox immediately after the secretary's wake:
   ```
   cat ~/.claude/teams/{team_name}/inboxes/team-lead.json
   ```
3. Inspect the work-task on disk after the secretary's first turn:
   ```
   cat ~/.claude/tasks/{team_name}/<work_id>.json | python3 -c \
     'import sys,json; d=json.load(sys.stdin); print(d.get("status"), d.get("owner"))'
   ```

**Pass criteria**:

- [ ] Within the secretary's first turn after spawn, an inbox message
      from `secretary → team-lead` is delivered containing
      session-briefing language (any of: "session briefing", "Working
      Memory", "recent project context", calibration summary).
- [ ] The work-task `status == "completed"` with `owner == "secretary"`
      — the secretary SELF-completed it (the
      `is_self_complete_exempt`-witnessed carve-out; the team-lead did
      NOT flip it).
- [ ] No `dispatch_decision` / `task_lifecycle_gate` `self_completion`
      advisory fired against the secretary's self-completion (the
      advisory is suppressed for the secretary via
      `is_self_complete_exempt`).

**Failure signals**:

- The work-task lingers `in_progress` indefinitely after the briefing is
  delivered: self-completion regression — the secretary delivered the
  briefing but did not self-complete the task (the original #889 bug).
  Check `pact-secretary.md` §At Spawn step 6 + the self-complete
  carve-out.
- A `self_completion` advisory with `metadata.completion_disputed=true`
  written to the work-task: the carve-out is not resolving — verify the
  team config records `secretary` with `agentType="pact-secretary"` (the
  carve-out keys on team-config agentType, not owner name).
- The secretary's first turn after spawn contains no `SendMessage` to
  team-lead: the secretary spawn may have failed to load the
  pact-secretary persona (regression in agent frontmatter).

---

## Section 4 — Secretary stays alive (consultant + harvester)

**Goal**: confirm self-completing the briefing task does NOT end the
secretary's role — it remains alive for memory queries and HANDOFF
harvest.

**Steps**:

1. After the secretary self-completes the briefing task (Section 3),
   confirm the secretary process is still alive and in Consultant Mode
   (it did not shut down on completion).
2. Optionally exercise a memory query: have the team-lead send the
   secretary a `SendMessage` query and confirm a response is delivered.
3. Confirm the secretary's re-enter-lifecycle did not re-claim the
   already-completed briefing task:
   ```
   cat ~/.claude/tasks/{team_name}/<work_id>.json | python3 -c \
     'import sys,json; d=json.load(sys.stdin); print(d.get("status"))'
   ```

**Pass criteria**:

- [ ] The secretary process is alive after self-completing the briefing
      task (it did not terminate).
- [ ] The completed briefing task is NOT re-claimed (`status` stays
      `"completed"`) — per `pact-secretary.md` §After Session Briefing —
      Re-enter Standard Lifecycle, the already-self-completed briefing
      task does not reappear as claimable.
- [ ] A memory query to the secretary (if exercised) is answered — the
      secretary is in Consultant Mode.

**Failure signals**:

- The secretary process terminates immediately after self-completing the
  briefing task: regression in the "completion does not end your role"
  framing — the secretary should remain alive as consultant + harvester.
- The secretary re-claims the completed briefing task (flips it back to
  `in_progress`): re-enter-lifecycle drift — the §At Spawn self-completion
  and the re-enter-lifecycle note are out of sync.

---

## Section 5 — Run summary

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

If §2 fails: structural shape drift (a stray teachback Task A, or a
`blockedBy` edge). Inspect the journal for `task_lifecycle_gate`
advisories. File a P1 issue (over-gating regression) or P2 (persona
drift, gate still catches it advisorily).

If §3 fails (briefing missing OR task left `in_progress`): the briefing
delivery + self-completion path regressed — the original #889 failure
mode. File a P2 issue against the pact-secretary §At Spawn behavior.

If §4 fails (secretary terminates on completion, or re-claims the task):
the "completion does not end the role" framing regressed. File a P2
issue against the pact-secretary persona.

---

## Section 6 — Troubleshooting (false-positive failure modes)

### 6.1 Briefing delayed past one turn

The secretary's session-start behavior includes Working Memory cleanup
plus calibration corpus search plus optional compact-summary processing.
On a session with a large calibration corpus or a pending
`compact-summary.txt`, the briefing may legitimately span two turns —
the secretary delivers a partial briefing on turn 1 and continues in
turn 2, self-completing the work-task after the briefing is fully
delivered. Section 3 records a multi-turn briefing as a soft signal, not
a hard fail; document it in the run row.

### 6.2 Existing team config from prior session

If the team already exists on disk
(`~/.claude/teams/{team_name}/config.json` present), the
bootstrap-ritual reuses the team and skips `TeamCreate`. The secretary
spawn still proceeds through the single-task teachback-exempt sequence —
Section 1 still applies. Confirm via the orchestrator's tool calls that
the three-call sequence executed regardless of team-create vs reuse path.

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
authored the merge of a bootstrap/hook change will be running the OLD
hook code (per pinned CLAUDE.md memory `4fa2311 → 27aa95e`). Re-running
this runbook in that same session will produce a phantom-green result.

If you authored the bootstrap change under test in this session, your
hook registrations are stale and the gates may not fire even if the
persona/bootstrap.md remained mis-aligned. Section 1 pass under stale
hooks is **not** a true pass. Restart in a fresh session post-merge
before recording a runbook row.
