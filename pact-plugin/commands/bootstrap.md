---
description: PACT session-start ritual — identify the session team (platform-provisioned), secretary spawn, paused-state surface, bootstrap marker
---

# Session-Start Ritual

The persona body's §2 Session-Start Ritual is your invocation contract; this command holds the mechanical detail. Execute the steps below in order, substituting Session Placeholder Variables from your context.

---

## Step 1 — Identify the session team

Read `team_name` from the **Current Session** block in the project's `CLAUDE.md` (preferred location: `$CLAUDE_PROJECT_DIR/.claude/CLAUDE.md`; legacy fallback: `$CLAUDE_PROJECT_DIR/CLAUDE.md`). The `session_init` hook writes this block at session start.

The platform manages exactly one team per session, named `{team_name}` — it is provisioned automatically; you do not create it (the `TeamCreate`/`TeamDelete` tools no longer exist). Use `{team_name}` for every specialist dispatch.

**Team-config expectations (bidirectional)**: on resume, a PRESENT team config is reused — never re-create it. An ABSENT config is the NORM after a clean session end — the platform provisions the session team; both states are normal, neither is corruption. **Ghost-detection**: if a spawn or send fails with a team-not-found error, do NOT blind-retry `Agent()` — the platform spawn is non-atomic and the process may already be running. Check for inbound messages from the "failed" agent name first; re-spawn only if it stays silent.

## Step 2 — Spawn `pact-secretary`

Spawn the session secretary using single-task dispatch — the `pact-secretary` agentType is exempt from the teachback gate. No Task A teachback round-trip.

1. `TaskCreate(subject="secretary: deliver session briefing", description="<full mission: deliver session briefing on spawn, answer memory queries during the session, process HANDOFFs at workflow boundaries; CONTEXT / MISSION / INSTRUCTIONS / GUIDELINES per the orchestrator persona §13 Recommended Agent Prompting Structure>")` — single work task. The subject names a **discrete deliverable** (the briefing), NOT the secretary's standing role; the standing duties (memory queries, HANDOFF harvest) live in the description as mission context and are tracked by their own later tasks.
2. `TaskUpdate(task_id, owner="secretary")` — assign to the secretary; no `addBlockedBy` (no teachback gate)
3. `Agent(name="secretary", team_name="{team_name}", subagent_type="pact-secretary", prompt="YOUR PACT ROLE: teammate (secretary).\n\nYou are joining team {team_name}. As your FIRST action, Invoke Skill(\"PACT:pact-team-registration\") to record your identity. Then check `TaskList` for tasks assigned to you.")`
    - **Use `subagent_type="pact-secretary"` and the canonical `name="secretary"` — the literal name is load-bearing**.

The secretary delivers the session briefing at spawn, answers memory queries during the session, and processes HANDOFFs at workflow boundaries. The briefing task is a discrete deliverable: the secretary MUST self-complete it (`TaskUpdate(status="completed")`) as the final act of delivering the briefing — you do NOT complete it, and you MUST NOT expect to. Completing the task does NOT end the secretary's role; it continues as memory consultant and HANDOFF harvester for the rest of the session. Memory queries from any other agent are blocked until the secretary is alive.

Spawn the secretary **only once per session** — reuse the same secretary for any subsequent memory queries or HANDOFF harvesting — AND respawn it after `/PACT:refresh`, whose shutdown stopped the previous secretary process. Post-refresh, respawning is MANDATORY BEFORE any SendMessage to the secretary name: a send to the stopped name resurrects its stale pre-refresh transcript instead of starting fresh.

## Step 3 — Surface paused state

Paused state is surfaced **automatically** by the `session_init` hook: when the previous session ran `/PACT:pause`, it wrote a `session_paused` event to the session journal, and `session_init` reads that event on resume and injects the paused-work prompt into the SessionStart context. There is **no `paused-state.json` file** — do not attempt to read one (nothing writes it). Watch the SessionStart context for a "Paused work detected" line. If it appears, **do not silently resume.** Surface it to the user and ask whether to continue the paused workflow or start fresh; their choice drives next-step dispatch.

**Refreshed state**: also watch the SessionStart context for a refreshed-workstream prompt (a "Refreshed workstream detected" line carrying a `refresh_ts=` key). If present, this is a DECLARED CONTINUATION, not a fresh start: surface the mid-flight state to the user (feature, next phase, worktrees, any HALT line) and AUTO-PROCEED to respawn the specialists the named next phase needs. Ask the user ONLY on inconsistency: the prompt's HALT line has no matching live blocker task in `TaskList` (or live blockers exist the prompt doesn't mention), or a listed worktree path does not exist on disk. A HALT line always surfaces to the user regardless.

**Consumption write (fire-once)**: immediately after confirming resumption (secretary respawned, mid-flight state surfaced), retire the refresh prompt by writing the consumption event — substitute the `refresh_ts=` value copied VERBATIM from the surfaced prompt:

```bash
python3 "{plugin_root}/hooks/shared/session_journal.py" write \
  --type session_refresh_consumed --session-dir '{session_dir}' --stdin <<'JSON'
{"refresh_ts": "{refresh_ts}"}
JSON
```

If the prompt said `refresh_ts=UNAVAILABLE`, skip the write (the prompt may re-surface once; its staleness downgrade bounds the repetition). Never write a consumption event when no refresh prompt surfaced. On a quit-then-new-session resume the consumption event lands in the NEW session's journal (a harmless orphan record) — fire-once on that path is enforced by the one-hop-back journal read, while the ts-bound consumption covers the same-session paths (`/compact` and same-session `--resume`).

## Step 4 — Plugin banner

Surface the plugin banner — a single line of the form `PACT plugin: <version> (root: ~/.claude/plugins/cache/pact-marketplace/PACT/<version>)` — in the bootstrap-confirmation reply. The banner is pre-rendered by the `format_plugin_banner()` helper in `hooks/shared/plugin_manifest.py` (reading the live version from `plugin.json`) and delivered through the `session_init` SessionStart system reminder + the per-prompt `peer_inject` surface; no manual composition is needed — echo what the hook already produced. If the session-start system reminder has been dropped (post-compaction), fall back in order: (a) read the `- Plugin root:` line in CLAUDE.md's Current Session block (the path embeds the version), then (b) read `plugin.json["version"]` directly. The `<version>` placeholder above is illustrative — do not substitute it manually.

---

## Session Placeholder Variables

Command files use `{team_name}`, `{session_dir}`, and `{plugin_root}` as literal brace-wrapped placeholders. **Substitution is manual textual replacement** performed by the orchestrator before invoking shell commands — there is no template engine.

| Placeholder | CLAUDE.md line | Context JSON key | Description |
|-------------|---------------|-----------------|-------------|
| `{team_name}` | `- Team:` | `team_name` | Session team name |
| `{session_dir}` | `- Session dir:` | derived from `session_id` + `project_dir` | Session journal directory |
| `{plugin_root}` | `- Plugin root:` | `plugin_root` | Installed plugin root for CLI paths |

**Source precedence**: when the `session_init` hook delivers substitution instructions inline (in the SessionStart system reminder at the top of the session), **those hook-delivered values are authoritative** and take precedence over the Current Session block in `CLAUDE.md`. The `CLAUDE.md` block is the fallback source, used only when the hook context has been lost (e.g., after compaction drops the initial system reminder).

**Per-field fallback**: if an individual variable is missing from `CLAUDE.md` (e.g., a session block written by an older `session_init` that didn't record `- Plugin root:`), fall back to `pact-session-context.json` in the current session directory for that one variable. Do not re-read the whole set from JSON when a single field is missing.

**Last-resort fallback for `{plugin_root}`**: if both `CLAUDE.md` and `pact-session-context.json` are unavailable, use `$HOME/.claude/protocols/pact-plugin/../` (symlink traversal). If the resolved path does not exist, stop and report the issue to the user rather than continuing with a broken path.

---

## Marker (hook-managed)

The bootstrap-complete marker at `<session_dir>/bootstrap-complete` is written by the `bootstrap_marker_writer.py` UserPromptSubmit hook once the ritual's pre-conditions are observable on disk: team config exists AND `secretary` is in `members[]`. The marker self-installs on the next user prompt after Steps 1-2 complete. No LLM action is required for the marker.

If a `bootstrap_gate` PreToolUse refusal indicates the marker is missing after the ritual, see the `bootstrap_marker_writer.py` source for the pre-condition checks; the most likely cause is a delayed secretary spawn that hasn't yet propagated to `~/.claude/teams/{team_name}/config.json`.
