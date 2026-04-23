"""
Location: pact-plugin/hooks/shared/plugin_manifest.py
Summary: Read plugin manifest (plugin.json) for diagnostic surfacing of the
         running plugin's name, version, and resolved root path. Fail-open:
         the public API never raises and always returns a non-empty banner
         string. Used by SessionStart (session_init.py) and SubagentStart
         (peer_inject.py) to surface a single-line `additionalContext`
         diagnostic so readers can cross-reference worktree edits against
         the installed-cache root at a glance (#500).
Used by: session_init.py (Slot A, context_parts), peer_inject.py (between
         peer_context and _TEACHBACK_REMINDER in get_peer_context return).

No file locking — read-only access to a Claude-Code-managed file that is
never mutated after plugin install.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

_UNSET = "<unset>"
_UNKNOWN_META = "unknown"


def _resolve_plugin_root() -> Optional[str]:
    """Return CLAUDE_PLUGIN_ROOT env var, or None if empty/unset."""
    root = os.environ.get("CLAUDE_PLUGIN_ROOT", "").strip()
    return root or None


def _read_manifest(plugin_root: str) -> Optional[dict]:
    """Read and parse .claude-plugin/plugin.json under `plugin_root`.

    Returns the parsed dict on success, or None on any filesystem / decode /
    JSON / non-dict failure. Does not raise.
    """
    manifest_path = Path(plugin_root) / ".claude-plugin" / "plugin.json"
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def format_plugin_banner() -> str:
    """Return a single-line diagnostic banner for additionalContext.

    Total function: never raises, always returns a non-empty string with
    prefix "PACT plugin: " and no newline/carriage-return characters.

    Happy path:
        "PACT plugin: {name} {version} (root: {plugin_root})"

    Fail-open (any read/parse/schema failure):
        "PACT plugin: unknown (root: {plugin_root_or_<unset>})"

    The outer blanket try/except guarantees totality against any future
    regression in the helpers — SessionStart and SubagentStart are hot
    paths, and an uncaught exception here would break additionalContext
    delivery for every session.
    """
    try:
        plugin_root = _resolve_plugin_root()
        root_display = plugin_root if plugin_root else _UNSET

        if plugin_root is None:
            return f"PACT plugin: {_UNKNOWN_META} (root: {root_display})"

        data = _read_manifest(plugin_root)
        if data is None:
            return f"PACT plugin: {_UNKNOWN_META} (root: {root_display})"

        name = data.get("name")
        version = data.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            return f"PACT plugin: {_UNKNOWN_META} (root: {root_display})"
        if not name or not version:
            return f"PACT plugin: {_UNKNOWN_META} (root: {root_display})"

        # Defense-in-depth: strip \n / \r from plugin-authored fields so a
        # pathological manifest cannot inject a line break into the
        # single-line additionalContext chain. The banner does NOT carry the
        # PACT ROLE marker prefix and is not consumed by the routing block's
        # line-anchored substring check, so this is belt-and-suspenders
        # rather than a primary defense.
        name = name.replace("\n", " ").replace("\r", " ")
        version = version.replace("\n", " ").replace("\r", " ")
        return f"PACT plugin: {name} {version} (root: {root_display})"
    except Exception:  # noqa: BLE001 — total-function outer fail-open
        return f"PACT plugin: {_UNKNOWN_META} (root: {_UNSET})"
