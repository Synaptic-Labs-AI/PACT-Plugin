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

Spawn `pact-secretary` as the session secretary. It delivers the session briefing at spawn, answers memory queries during the session, and processes HANDOFFs at workflow boundaries. Memory queries from any other agent are blocked until the secretary is alive.

Spawn the secretary once per session — reuse the existing instance on subsequent re-invocations of this command rather than spawning a duplicate.

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

## Step 5 — BOOTSTRAP CONFIRMATION (required)

This step unlocks code-editing tools (`Edit`, `Write`) and agent spawning (`Agent`, `NotebookEdit`), which are blocked by the `bootstrap_gate` PreToolUse hook until the bootstrap-complete marker exists.

Find the `PACT_SESSION_DIR=<path>` line in your context (injected by `bootstrap_prompt_gate` at every prompt while the marker is absent). Run:

```
mkdir -p "<path>" && touch "<path>/bootstrap-complete"
```

Substitute `<path>` with the value from `PACT_SESSION_DIR=`. The marker name `bootstrap-complete` is the load-bearing literal that `bootstrap_gate.is_marker_set` checks; do not rename it.

<!-- Coupling: marker name "bootstrap-complete" must match shared.BOOTSTRAP_MARKER_NAME
     in pact-plugin/hooks/shared/__init__.py.
     Pattern: convention-must-be-enforced-not-just-documented (test_three_surface_split_enforcement.py
     pins the persona §2 / bootstrap.md split; this comment pins the marker-name SSOT). -->
