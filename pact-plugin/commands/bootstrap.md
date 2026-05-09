---
description: PACT session-start ritual — team create/reuse, secretary spawn, paused-state surface, bootstrap marker
---

# Session-Start Ritual

The persona body's §2 Session-Start Ritual is your invocation contract; this command holds the mechanical detail. Execute the steps below in order, substituting Session Placeholder Variables from your context.

---

## Step 1 — Team create or reuse

Read `team_name` from the **Current Session** block in the project's `CLAUDE.md` (preferred location: `$CLAUDE_PROJECT_DIR/.claude/CLAUDE.md`; legacy fallback: `$CLAUDE_PROJECT_DIR/CLAUDE.md`). The `session_init` hook writes this block at session start.

- If `~/.claude/teams/{team_name}/config.json` exists → **reuse**: the team is live; do not recreate.
- If absent → **create** via the Agent Teams `TeamCreate` action with `name={team_name}`. Every specialist dispatch requires the team to exist.

## Step 2 — Spawn `pact-secretary`

Spawn the session secretary using the Teachback-Gated Dispatch below (See persona §Agent Teams Dispatch for the canonical pattern applied to other dispatches):

1. `TaskCreate(subject="secretary: TEACHBACK for session briefing", description="<teachback gate brief; cross-ref to Task B for the mission>")` — Task A
2. `TaskCreate(subject="secretary: Session briefing + HANDOFF readiness", description="<full mission: deliver session briefing on spawn, answer memory queries during the session, process HANDOFFs at workflow boundaries; CONTEXT / MISSION / INSTRUCTIONS / GUIDELINES per the orchestrator persona §13 Recommended Agent Prompting Structure>")` — Task B
3. `TaskUpdate(A_id, owner="secretary", addBlocks=[B_id])`
4. `TaskUpdate(B_id, owner="secretary", addBlockedBy=[A_id])`
5. `Agent(name="secretary", team_name="{team_name}", subagent_type="pact-secretary", prompt="YOUR PACT ROLE: teammate (secretary).\n\nYou are joining team {team_name}. Check `TaskList` for tasks assigned to you.")`
    - **Use `subagent_type="pact-secretary"` and the canonical `name="secretary"` — the literal name is load-bearing**.

The secretary delivers the session briefing at spawn, answers memory queries during the session, and processes HANDOFFs at workflow boundaries. Memory queries from any other agent are blocked until the secretary is alive.

Spawn the secretary **only once per session** — reuse the same secretary for any subsequent memory queries or HANDOFF harvesting.

## Step 3 — Surface paused state

If `~/.claude/teams/{team_name}/paused-state.json` exists, read it and surface its contents to the user. **Do not silently resume.** Ask the user to confirm whether to continue the paused workflow or start fresh; their choice drives next-step dispatch.

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
