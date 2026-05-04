"""
Location: pact-plugin/hooks/shared/plugin_manifest.py
Summary: Read plugin manifest (plugin.json) for diagnostic surfacing of the
         running plugin's name, version, and resolved root path. Fail-open:
         the public API never raises and always returns a non-empty banner
         string. Used by SessionStart (session_init.py) to surface a
         single-line `additionalContext` diagnostic so readers can cross-
         reference worktree edits against the installed-cache root at a
         glance.
Used by: session_init.py (Slot A, context_parts).

No file locking — read-only access to a Claude-Code-managed file that is
never mutated after plugin install.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

_UNSET = "<unset>"
_UNKNOWN_META = "unknown"
_PREFIX = "PACT plugin: "

# Symmetric with project-wide render-bound string handling: matches
# `session_state._RENDER_STRIP_RE`. Covers C0 controls (0x00-0x1f, incl.
# \n, \r, VT, FF, ESC), DEL (0x7f), NEL (U+0085), LINE SEPARATOR (U+2028),
# PARAGRAPH SEPARATOR (U+2029) — every character `str.splitlines()` or an
# LLM tokenizer may treat as a line break, plus render-hostile control
# bytes. Asymmetric strip sets across interpolation sinks become the
# attacker's entry point (see security-engineer
# patterns_symmetric_sanitization.md).
_RENDER_STRIP_RE = re.compile(r"[\x00-\x1f\x7f  ]")


def _sanitize(text: str) -> str:
    """Strip C0 controls, DEL, and Unicode line terminators.

    Matches `session_state._RENDER_STRIP_RE.sub("", ...)` exactly. Empty-
    string replacement (not space) mirrors the canonical project form.
    """
    return _RENDER_STRIP_RE.sub("", text)


def _resolve_plugin_root() -> Optional[str]:
    """Return CLAUDE_PLUGIN_ROOT env var, or None if empty/unset.

    Reads the env var directly rather than calling
    `pact_context.get_plugin_root()`. session_init.py's Slot A append fires
    at line 708 BEFORE `write_context()` at line 836, so the pact_context
    cache is empty when the banner is emitted. Direct env read is the
    correct source at this ordering; a future "SSOT cleanup" refactor
    routing through pact_context would silently break the banner.
    """
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
    regression in the helpers — SessionStart is a hot path, and an
    uncaught exception here would break additionalContext delivery for
    every session.
    """
    try:
        plugin_root = _resolve_plugin_root()
        # Sanitize root_display for contract consistency: the single-line
        # contract applies to ALL banner inputs, not just the plugin-authored
        # name/version. CLAUDE_PLUGIN_ROOT is Claude-Code-managed, but routing
        # the env var through _sanitize keeps the invariant total.
        root_display = _sanitize(plugin_root) if plugin_root else _UNSET

        if plugin_root is None:
            return f"{_PREFIX}{_UNKNOWN_META} (root: {root_display})"

        data = _read_manifest(plugin_root)
        if data is None:
            return f"{_PREFIX}{_UNKNOWN_META} (root: {root_display})"

        name = data.get("name")
        version = data.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            return f"{_PREFIX}{_UNKNOWN_META} (root: {root_display})"
        if not name or not version:
            return f"{_PREFIX}{_UNKNOWN_META} (root: {root_display})"

        # Defense-in-depth: strip line-break characters from plugin-authored
        # fields so a pathological manifest cannot inject a line break into
        # the single-line additionalContext chain. The banner does NOT carry
        # the PACT ROLE marker prefix and is not consumed by the routing
        # block's line-anchored substring check, so this is belt-and-
        # suspenders rather than a primary defense.
        name = _sanitize(name)
        version = _sanitize(version)
        return f"{_PREFIX}{name} {version} (root: {root_display})"
    except Exception:  # noqa: BLE001 — total-function outer fail-open
        return f"{_PREFIX}{_UNKNOWN_META} (root: {_UNSET})"
