"""
Location: pact-plugin/hooks/shared/paths.py
Summary: Single source of truth for the Claude Code config/state root.
         Resolves $CLAUDE_CONFIG_DIR (fail-loud, monkeypatch-safe, single-path),
         falling back to ~/.claude. Every PACT hook that reads/writes state
         under the config dir derives its base from get_claude_config_dir().
Used by: shared/{constants,session_registry,failure_log,merge_guard_common,
         task_utils,pact_context,...}.py and the hook entrypoints
         (dispatch_gate, session_init, session_end, ...).
"""

from __future__ import annotations

import os
from pathlib import Path


def get_claude_config_dir(env=None, home=None) -> Path:
    """Resolve the Claude Code config/state root.

    Pure resolver of (env, home) with both defaulting to the live process
    globals, so L2/resolution-consumer tests drive it via monkeypatch.setenv +
    Path.home redirect (NO DI — injecting there is a vacuous green), while L1
    resolver-unit tests MAY pass env=/home= to assert the contract directly.

    Precedence (fail-loud — NEVER a silent wrong-root fallback):
      1. $CLAUDE_CONFIG_DIR, honored EVEN IF the dir does not yet exist (the
         platform creates it; only CONSUMERS fail-open on a missing dir).
         set-but-empty / whitespace == UNSET.
           "~"      -> home
           "~/x"    -> home / "x"     (exact-prefix slice; monkeypatch-safe)
           "/abs"   -> Path("/abs")
           "rel"    -> Path("rel")    (honored as-is; surfaced via observability)
      2. fallback (unset): home / ".claude"

    Returns an UNRESOLVED path — the single .resolve() stays at the call sites
    that perform containment checks. NO expanduser/lstrip/removeprefix.
    """
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    raw = (env.get("CLAUDE_CONFIG_DIR") or "").strip()
    if not raw:
        return home / ".claude"
    if raw == "~":
        return home
    if raw.startswith("~/"):
        return home / raw[2:]          # exact 2-char prefix — NOT lstrip, NOT removeprefix
    return Path(raw)
