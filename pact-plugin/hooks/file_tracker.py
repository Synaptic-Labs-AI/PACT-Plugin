#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/file_tracker.py
Summary: PostToolUse hook matching Edit|Write that tracks which agent edits
         which files and warns on inter-agent conflicts.
Used by: hooks.json PostToolUse hook (matcher: Edit|Write)

Non-blocking (PostToolUse cannot block). Warns via additionalContext when
a different agent has already edited the same file.

Input: JSON from stdin with tool_input.file_path
Output: JSON with additionalContext warning if conflict detected
"""

import json
import os
import sys
import time
from pathlib import Path

import shared.pact_context as pact_context
from shared.pact_context import get_session_id, get_team_name, resolve_agent_name
from shared.paths import get_claude_config_dir

try:
    import fcntl
    HAS_FLOCK = True
except ImportError:
    HAS_FLOCK = False

# Suppress false "hook error" display in Claude Code UI on bare exit paths
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def _normalize_path(file_path: str) -> str:
    """Normalize a file path for consistent comparison.

    Resolves symlinks and relative components so that './src/auth.ts',
    'src/auth.ts', and '/abs/path/src/auth.ts' all produce the same key
    in the tracking file.
    """
    return os.path.realpath(file_path)


def track_edit(
    file_path: str,
    agent_name: str,
    tool_name: str,
    tracking_path: str,
    session_id: str = "",
) -> None:
    """Append a file edit record to the tracking file.

    NEW-1 (#878): the editor is identified by the COMPOSITE key
    ``(agent_name, session_id)``, not by ``agent_name`` alone. Under tmux,
    same-``agent_type`` siblings (e.g. two ``backend-coder`` instances)
    collapse to the same ``resolve_agent_name`` value, so an agent-name-only
    key cannot tell them apart and conflict detection false-negatives. The
    ``session_id`` (already in stdin via ``pact_context.init``) supplies the
    per-instance uniqueness in BOTH modes. ``agent_name`` is retained as the
    human-readable LABEL (the friendly-name recovery for the label under tmux
    is a deferred follow-up; detection-uniqueness is what this fix restores).
    """
    file_path = _normalize_path(file_path)
    tracking_file = Path(tracking_path)
    tracking_file.parent.mkdir(parents=True, exist_ok=True)

    new_entry = {
        "file": file_path,
        "agent": agent_name,
        "session_id": session_id,
        "tool": tool_name,
        "ts": int(time.time()),
    }

    # Use file locking to prevent concurrent write corruption
    if HAS_FLOCK:
        with open(tracking_file, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                content = f.read()
                try:
                    entries = json.loads(content) if content.strip() else []
                except (json.JSONDecodeError, IOError):
                    entries = []
                entries.append(new_entry)
                f.seek(0)
                f.truncate()
                f.write(json.dumps(entries))
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    else:
        entries = []
        if tracking_file.exists():
            try:
                entries = json.loads(tracking_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, IOError):
                entries = []
        entries.append(new_entry)
        tracking_file.write_text(json.dumps(entries), encoding="utf-8")


def check_conflict(
    file_path: str,
    agent_name: str,
    tracking_path: str,
    session_id: str = "",
) -> str | None:
    """Check if another EDITOR INSTANCE has edited this file.

    NEW-1 (#878): an editor is the COMPOSITE ``(agent_name, session_id)`` — so
    a different *instance* of the same ``agent_type`` (same ``agent_name``,
    different ``session_id``) is correctly counted as a separate editor and a
    real cross-instance conflict is DETECTED under tmux (the prior
    agent-name-only key false-negatived here). The SAME instance editing twice
    (same composite) is NOT a conflict.

    The conflict message lists the human-readable ``agent`` LABEL of each other
    editor. When two other editors share an ``agent`` label but differ by
    ``session_id`` (same-type siblings under tmux), the label is disambiguated
    with a short session_id suffix so the message names two distinct editors
    rather than a confusing repeated name.
    """
    file_path = _normalize_path(file_path)
    if not agent_name:
        return None

    tracking_file = Path(tracking_path)
    if not tracking_file.exists():
        return None

    try:
        entries = json.loads(tracking_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return None

    self_key = (agent_name, session_id)
    # Collect the distinct OTHER editor instances (composite key) and, per
    # agent label, the set of session_ids seen — so the label can be
    # disambiguated only when a name is genuinely shared across instances.
    other_keys: set[tuple[str, str]] = set()
    sessions_by_agent: dict[str, set[str]] = {}
    for entry in entries:
        if entry.get("file") != file_path:
            continue
        entry_agent = entry.get("agent", "")
        entry_session = entry.get("session_id", "")
        if (entry_agent, entry_session) == self_key:
            continue
        other_keys.add((entry_agent, entry_session))
        sessions_by_agent.setdefault(entry_agent, set()).add(entry_session)

    if not other_keys:
        return None

    labels = []
    for entry_agent, entry_session in other_keys:
        # Disambiguate with a short session_id suffix only when this agent
        # name is shared by more than one instance (otherwise the bare name
        # is unambiguous and cleaner).
        if entry_session and len(sessions_by_agent.get(entry_agent, set())) > 1:
            labels.append(f"{entry_agent} (session {entry_session[:8]})")
        else:
            labels.append(entry_agent)
    others = ", ".join(sorted(labels))
    return (
        f"File conflict: {file_path} was also edited by {others}. "
        f"Consider coordinating via SendMessage to avoid merge conflicts."
    )


def get_environment_delta(
    since_ts: int,
    requesting_agent: str,
    tracking_path: str,
) -> dict[str, str]:
    """Return files modified by OTHER agents since the given timestamp.

    Returns a dict of {file_path: agent_name} for files modified by agents
    other than requesting_agent after since_ts. Used by orchestrator to
    detect environment drift when dispatching or briefing agents.

    Note: Uses inclusive boundary (>=) — entries AT exactly since_ts are included.
    """
    tracking_file = Path(tracking_path)
    if not tracking_file.exists():
        return {}

    try:
        entries = json.loads(tracking_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return {}

    delta: dict[str, str] = {}
    for entry in entries:
        file_path = entry.get("file")
        agent = entry.get("agent")
        if not file_path or not agent:
            continue
        if entry.get("ts", 0) >= since_ts and agent != requesting_agent:
            delta[file_path] = agent

    return delta


def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    pact_context.init(input_data)
    team_name = get_team_name()
    if not team_name:
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    file_path = input_data.get("tool_input", {}).get("file_path", "")
    if not file_path:
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    agent_name = resolve_agent_name(input_data)
    tool_name = input_data.get("tool_name", "")
    # NEW-1 (#878): session_id is the per-instance uniqueness component of the
    # composite editor key. Available via pact_context after init() above.
    # resolve_agent_name is KEPT for the human-readable label.
    session_id = get_session_id()

    tracking_path = str(
        get_claude_config_dir() / "teams" / team_name / "file-edits.json"
    )

    # Check for conflict BEFORE recording this edit. Pass the same
    # (agent_name, session_id) composite so this instance's own prior edits are
    # excluded but a different instance's are detected.
    conflict = check_conflict(file_path, agent_name, tracking_path, session_id)

    # Record this edit
    track_edit(
        file_path, agent_name or "orchestrator", tool_name, tracking_path,
        session_id,
    )

    # Warn if conflict
    if conflict:
        # hookEventName is required by the harness; missing it silently fails open
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": f"\u26a0\ufe0f {conflict}"
            }
        }
        print(json.dumps(output))
    else:
        # Unlike other hooks, this else-branch is new code (the original had
        # no explicit no-conflict path — it fell through to bare sys.exit(0))
        print(_SUPPRESS_OUTPUT)

    sys.exit(0)


if __name__ == "__main__":
    main()
