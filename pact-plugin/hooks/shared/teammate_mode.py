#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/shared/teammate_mode.py
Summary: Resolve the effective Claude Code `teammateMode` setting from on-disk
         settings sources, mirroring the runtime config getter's file-readable
         precedence. Fail-open by construction — every public function is total
         (never raises), because the sole consumer runs on the SessionStart hot
         path where an uncaught exception would break bootstrap.
Used by: session_init.py (in-process startup notice). Designed for reuse by
         future consumers via resolve_effective_teammate_mode().

Background (verified live, Claude Code 2.1.156):
  `teammateMode` is a first-class Claude Code SETTING (one of {"auto","tmux",
  "in-process"}), NOT a GrowthBook/Statsig flag. The runtime getter
  g1("teammateMode","auto") scans settings sources highest-priority-first,
  then a ~/.claude.json legacy fallback, then defaults to "auto". A per-launch
  `--teammate-mode` CLI override lives only in the parent process's memory and
  is invisible to a hook subprocess (absent from stdin, env, and every settings
  file). This module therefore approximates the runtime value via the
  FILE-READABLE precedence only; the caller fails SAFE toward emitting when the
  resolved value is not positively "tmux".
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

# Full allowed value set (CLI .choices + settings-UI enum). A value outside
# this set is treated as "not defined here" (fail-open → skip the source).
VALID_TEAMMATE_MODES = frozenset({"auto", "tmux", "in-process"})

# Runtime default when no source defines the key (mirrors g1's default arg).
_DEFAULT_MODE = "auto"


def _read_teammate_mode(path: Path) -> Optional[str]:
    """Read top-level `teammateMode` from one JSON settings file.

    Returns the value if it is a recognized mode string, else None.
    Fail-open: ANY missing-file / unreadable / parse-error / wrong-type /
    unrecognized-value condition returns None (caller treats None as "this
    source does not define the key" and moves on). Never raises.
    """
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        value = data.get("teammateMode")
        if isinstance(value, str) and value in VALID_TEAMMATE_MODES:
            return value
        return None
    except Exception:  # noqa: BLE001 — fail-open per module contract
        return None


def _managed_settings_path() -> Path:
    """Return the OS-specific enterprise managed-settings.json path.

    Enterprise managed settings outrank every other source in Claude Code's
    runtime getter (g1), so this is the highest-precedence entry in
    _settings_source_paths(). The returned value is an absolute OS-specific
    literal: it makes NO Path.home()/expanduser call and performs NO I/O here
    (the file is read fail-open by _read_teammate_mode like any other source).
    On a machine without a managed deployment the path simply does not exist
    (exists()=False → skipped), so adding it is a no-op off managed fleets.

    WSL's /mnt/c/... rewrite of the Windows path is deferred — not handled here.
    Paths verified live against Claude Code 2.1.156.
    """
    if sys.platform == "darwin":
        return Path("/Library/Application Support/ClaudeCode/managed-settings.json")
    if sys.platform == "win32":
        return Path(r"C:\Program Files\ClaudeCode\managed-settings.json")
    return Path("/etc/claude-code/managed-settings.json")  # linux / default


def _settings_source_paths() -> list[Path]:
    """Settings sources in runtime precedence order (highest first).

    Mirrors the FILE-READABLE portion of Claude Code's settings precedence:
      1. enterprise managed-settings (OS-specific; highest — managed wins in g1)
      2. project  .claude/settings.local.json   (local)
      3. project  .claude/settings.json         (project)
      4. user     ~/.claude/settings.json        (user)
    Reading the enterprise managed-settings layer at the TOP of the precedence
    (it outranks all others in g1) CLOSES the former false-suppress edge: a
    managed `teammateMode` of "in-process" / "auto" OVER a lower-layer "tmux"
    now resolves correctly (managed read first → non-tmux → EMIT) instead of
    this helper reading the lower "tmux" and wrongly SUPPRESSING. The managed
    file is absent on dev machines (exists()=False → skipped), so this adds
    zero behavior change off managed fleets.

    The ONE remaining accepted never-false-suppress breach is the in-memory
    `--teammate-mode` CLI override: it lives only in the parent process's
    memory and is invisible to a hook subprocess (see module docstring), so a
    live `--teammate-mode in-process` over a "tmux" config can still be missed.
    That edge is irreducible from a hook; the managed-settings layer is now
    handled (highest precedence).

    Path-resolution conventions (test-seam compatible):
      - the managed path is an absolute OS-specific literal (see
        _managed_settings_path); it makes no Path.home()/expanduser call, so it
        does not perturb the home-monkeypatch test seam.
      - project paths derive from CLAUDE_PROJECT_DIR (already consumed by
        session_init.py); defaults to "." when unset.
      - user path uses Path.home() (NOT os.path.expanduser) so tests that
        monkeypatch Path.home resolve correctly.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")
    project_claude = Path(project_dir) / ".claude"
    return [
        _managed_settings_path(),
        project_claude / "settings.local.json",
        project_claude / "settings.json",
        Path.home() / ".claude" / "settings.json",
    ]


def resolve_effective_teammate_mode() -> str:
    """Return the effective teammateMode the runtime would resolve from disk.

    Scans settings sources (managed → local → project → user), then the ~/.claude.json
    legacy global-config fallback, then defaults to "auto". Restricted to the
    file-readable precedence: a live `--teammate-mode` CLI override cannot be
    recovered from a hook and is NOT reflected here.

    Returns one of {"tmux","in-process","auto"}. "auto" is returned both when
    a source explicitly sets "auto" AND when nothing defines the key / every
    source is unreadable (indistinguishable downstream; both fail SAFE to
    emit). Total — never raises.

    Reuse note: this "auto" conflation erases explicit-"auto" vs nothing-
    defined/unreadable — correct for the current emit-policy (both → emit), but
    any future consumer needing to distinguish them must add a separate
    signal; it MUST NOT assume "auto" means an explicit user choice.
    """
    try:
        for path in _settings_source_paths():
            value = _read_teammate_mode(path)
            if value is not None:
                return value
        legacy = _read_teammate_mode(Path.home() / ".claude.json")
        if legacy is not None:
            return legacy
        return _DEFAULT_MODE
    except Exception:  # noqa: BLE001 — total contract (defense in depth)
        return _DEFAULT_MODE


def should_emit_inprocess_notice() -> bool:
    """True unless the effective teammateMode is positively "tmux".

    Fail-safe direction (#864): EMIT on "in-process" / "auto" / anything not
    confidently "tmux". The cost is asymmetric — a false suppress silently
    reinstates the in-process idle-stall the notice exists to warn about,
    while a false emit is one harmless extra startup line. Total — never
    raises; any unexpected failure returns True (emit).
    """
    try:
        return resolve_effective_teammate_mode() != "tmux"
    except Exception:  # noqa: BLE001 — fail-safe → emit
        return True
