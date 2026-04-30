# Runbook: Inbox-Wake Skill (D1) — Live Dogfood Validation

**Scope**: end-to-end verification of `Skill("PACT:inbox-wake")` Arm and Teardown across lead and teammate sessions. CI tests cover structural invariants (skill-body sections, hook directive presence, callsite token presence); this runbook covers behaviors that require a live `Monitor` task and a live inbox-grow event.

**Operator**: a human (or supervised lead session) running each step interactively. Mark each step pass/fail with timestamp and observed evidence.

**Empirical anchors** (from PREPARE / dogfood evidence carried forward):

- **Kill-mechanism (rejected design)**: cron-fire #1 `2026-04-29T22:49:36Z` killed `monitor_task_id=bu1pmbva7` ~19 s later; isolated Monitor in same session survived ≥10 min. D1 drops cron entirely. See `docs/preparation/591-inbox-wake-skill.md` §C, hypothesis H1 HIGH-confidence.
- **A-test ship-precondition**: isolated Monitor `monitor_task_id=b0zw6x8bj` armed `2026-04-29T23:11:33Z`, survived to `2026-04-29T23:55:09Z` (43.5 min uptime). H4 (undisclosed Monitor wallclock cap) falsified at relevant scale.
- **Long-tool-blocks-wake**: verified `2026-04-30T00:00–00:02Z` in session pact-5951b31c. Peer send `00:01:34Z`; Monitor `INBOX_GREW` fired `00:01:43Z` and `00:01:45Z` during a 90-s blocking sleep; Bash returned `00:02:23Z`; teammate-message content delivered in the *next* turn via standard idle-delivery, not mid-tool. See skill body §Failure Modes.

---

## Pre-Run Checklist

- [ ] Plugin version is 3.21.0 or later (grep `pact-plugin/.claude-plugin/plugin.json`).
- [ ] `pact-plugin/skills/inbox-wake/SKILL.md` exists with frontmatter `name: inbox-wake`.
- [ ] `~/.claude/teams/{TEAM}/inboxes/team-lead.json` exists for the live team.
- [ ] No stale `~/.claude/teams/{TEAM}/inbox-wake-state-*.json` from a prior aborted session (if present, delete before run).

Substitute `{TEAM}` with the active team name throughout.

---

## Step 1 — Fresh-Session Arm (Lead)

**Goal**: confirm `session_init.py` emits the wake-arm directive at SessionStart, the lead invokes the skill, and the STATE_FILE is written.

1. Start a fresh PACT session (or `/clear`).
2. Confirm the lead's first turn shows the wake-arm directive in additionalContext (lead invokes `Skill("PACT:inbox-wake")` with the Arm operation, `agent_name="team-lead"`).
3. Verify the cold-start path: `ls ~/.claude/teams/{TEAM}/inbox-wake-state-team-lead.json` should exist.
4. Inspect the file: `cat ~/.claude/teams/{TEAM}/inbox-wake-state-team-lead.json` — must show 3 fields exactly: `v: 1`, `monitor_task_id: <id>`, `armed_at: <ISO-8601 UTC>`.
5. Confirm `monitor_task_id` is a live task: `TaskGet(monitor_task_id)` reports `status: in_progress`.

**Pass criteria**: STATE_FILE present, schema valid, Monitor task live.

---

## Step 2 — INBOX_GREW Wake on Inbound SendMessage (Lead)

**Goal**: confirm Monitor fires `INBOX_GREW` on inbox byte-grow, ending the lead's turn between tool calls and surfacing the message via standard idle-delivery.

1. From a teammate context (or via direct file append in a separate terminal) cause an inbox-grow on the lead. Easiest: spawn any teammate via `Task(...)`; the spawn's prelude write grows `inboxes/team-lead.json`.
2. Observe lead's stdout for an `INBOX_GREW size=… ts=…` line during a poller-gated wait.
3. Confirm the lead's turn ends and the platform's `useInboxPoller` delivers the message.

**Pass criteria**: `INBOX_GREW` line emitted on stdout (turn-firing); message delivered next idle.

**Negative check**: between grows, stdout must NOT emit any `INBOX_GREW`-shaped line. The Monitor must not turn-fire on every poll cycle.

---

## Step 3 — Long Single-Tool Wake Latency (documented limitation)

**Goal**: empirically confirm that wake events that fire during a single long-running tool call are queued and delivered with the tool's return, NOT mid-tool. This is documented behavior (skill body §Failure Modes "Long single-tool calls block wake delivery"), not a defect.

1. Lead initiates a single long-running Bash call (e.g., `sleep 90`) in parallel with a teammate dispatch that will reply during the sleep.
2. Confirm `INBOX_GREW` line(s) appear during the sleep window.
3. Confirm the sleep completes and only AFTER its return does the lead's next turn process the inbox content.

**Pass criteria**: timing matches the empirical anchor (peer send ~T+0; `INBOX_GREW` ~T+10–15s during the tool call; tool return at T+90s; teammate-message delivery in the next turn). The wake mechanism does NOT interrupt mid-tool.

---

## Step 4 — Teammate-Side Arm at SubagentStart

**Goal**: confirm `peer_inject.py` emits the teammate-side wake-arm directive on SubagentStart and the spawned teammate arms its own Monitor on its own inbox.

1. Spawn a teammate (any pact-* agent type).
2. Confirm the teammate's first turn shows the wake-arm directive in additionalContext with the teammate's `agent_name` interpolated.
3. After the teammate's first non-Read tool call, verify `~/.claude/teams/{TEAM}/inbox-wake-state-{teammate-name}.json` exists with valid 3-field schema.
4. Confirm the teammate's `monitor_task_id` is distinct from the lead's and is live.

**Pass criteria**: per-teammate STATE_FILE present, schema valid, Monitor live, distinct from lead's Monitor.

---

## Step 5 — Teammate-Side Teardown on shutdown_request

**Goal**: confirm a teammate executes Teardown before approving `shutdown_request` (per `pact-agent-teams` `## Shutdown`), stopping its Monitor and unlinking its sidecar.

1. Send `shutdown_request` to the teammate spawned in Step 4.
2. Confirm in the teammate's response that it invoked `Skill("PACT:inbox-wake")` Teardown with its own `agent_name` BEFORE approving.
3. After teammate process terminates, verify `inbox-wake-state-{teammate-name}.json` is gone.
4. Verify the teammate's `monitor_task_id` is no longer live (`TaskGet` reports completed/stopped or not-found).

**Pass criteria**: teammate sidecar unlinked, teammate Monitor stopped, teardown ran before approval.

**Tolerance check**: if the teammate's Monitor died silently mid-session, `TaskStop` returns a `tool_use_error`; teardown must continue and unlink the sidecar regardless. The skill body's §Teardown Block phrase "ignoring not-found errors" is the load-bearing guard.

---

## Step 6 — Lead-Side Teardown via /wrap-up or /pause

**Goal**: confirm the lead's `/wrap-up` step 3 (Workspace Cleanup) and `/pause` step 6 (Tear Down the Lead's Wake Mechanism) execute Teardown for the lead, stopping the lead's Monitor and unlinking `inbox-wake-state-team-lead.json`.

1. Run `/PACT:wrap-up` (or `/PACT:pause`).
2. Observe the Teardown invocation in the command flow.
3. Verify `inbox-wake-state-team-lead.json` is gone after teardown step.
4. Verify the lead's `monitor_task_id` is no longer live.

**Pass criteria**: lead sidecar unlinked, lead Monitor stopped, Teardown ran in the documented step order (wrap-up §3 BEFORE §6 worktree cleanup; pause §6 BEFORE §7 teammate shutdown).

---

## Step 7 — Force-Termination Glob Cleanup

**Goal**: confirm `session_end.py::cleanup_wake_registry` globs `inbox-wake-state-*.json` and unlinks every per-agent sidecar in one pass when the lead's session terminates without running through `/wrap-up` or `/pause`.

1. With at least one lead sidecar AND one or more teammate sidecars present (e.g., teammates that were force-terminated without their `## Shutdown` Teardown firing), force-terminate the lead's session.
2. Verify `~/.claude/teams/{TEAM}/inbox-wake-state-*.json` returns no matches after the SessionEnd hook fires.

**Pass criteria**: zero `inbox-wake-state-*.json` files remain after force-termination.

**Note**: orphaned Monitor tasks die with the team's process tree; the registry sidecars are what `cleanup_wake_registry` exists to reap.

---

## Cleanup

After the run, regardless of pass/fail:

1. Force-stop any leftover Monitor tasks: `TaskList` and `TaskStop` any task whose subject contains the inbox-grow loop body.
2. Remove any leftover sidecars: `rm -f ~/.claude/teams/{TEAM}/inbox-wake-state-*.json`.
3. Record run results inline in this file (date, pass/fail per step, anomalies) or in a session journal entry.

---

## References

- Skill body: `pact-plugin/skills/inbox-wake/SKILL.md`
- Architect doc: `docs/architecture/591-inbox-wake-skill.md` — §10 (charter scope), §11 (test invariants), §12 (failure modes), §15 (symmetric scope)
- Charter: `pact-plugin/protocols/pact-communication-charter.md` §Wake Mechanism
- PREPARE: `docs/preparation/591-inbox-wake-skill.md` — §C kill-mechanism, §D alternatives, PASSIVE WAKE TEST series
- Issue #591 (this feature); #594 (skill-body line-count ceiling); #444 (compaction durability + hook-emitted-directives)
