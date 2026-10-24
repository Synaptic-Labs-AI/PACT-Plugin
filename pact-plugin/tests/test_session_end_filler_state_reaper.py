"""
Tests for cleanup_old_filler_state (session_end.py) — the TTL reaper for
the filler-gradient counter state (issue #1657).

Smoke-level CODE-phase arms: TTL expiry, fresh-file retention, symlink
skip, non-file skip (never rmtree), missing root, and default-root
resolution via CLAUDE_CONFIG_DIR. The reaper owns FILES in a PACT-owned
root, so the guards under test are the ones the plan pins: no
name-pattern gate, skip symlinks, never rmtree non-files.
"""

import os
import time
from pathlib import Path

from session_end import cleanup_old_filler_state


def _touch(root: Path, name: str, age_days: float) -> Path:
    """Create `root/name` as a regular file backdated to `age_days` ago."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_text('{"total": 1}', encoding="utf-8")
    stamp = time.time() - age_days * 86400
    os.utime(path, (stamp, stamp))
    return path


def test_reaps_expired_file_keeps_fresh_file(tmp_path):
    root = tmp_path / "pact-filler-state"
    old = _touch(root, "old-session.json", age_days=40)
    fresh = _touch(root, "live-session.json", age_days=1)
    reaped, skipped = cleanup_old_filler_state(state_base_dir=str(root))
    assert (reaped, skipped) == (1, 0)
    assert not old.exists()
    assert fresh.exists()


def test_skips_symlinks_even_when_stale(tmp_path):
    """A planted or convenience symlink is never followed and never
    unlinked, whatever its age."""
    root = tmp_path / "pact-filler-state"
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    root.mkdir(parents=True, exist_ok=True)
    link = root / "planted.json"
    link.symlink_to(outside)
    stale = time.time() - 40 * 86400
    os.utime(link, (stale, stale), follow_symlinks=False)
    reaped, _skipped = cleanup_old_filler_state(state_base_dir=str(root))
    assert reaped == 0
    assert link.is_symlink()
    assert outside.exists()


def test_never_rmtrees_directories_in_the_root(tmp_path):
    """A directory inside the state root is not the reaper's to remove:
    it stays untouched, and its stale contents stay with it."""
    root = tmp_path / "pact-filler-state"
    nested = root / "unexpected-dir"
    _touch(nested, "inner.json", age_days=90)
    reaped, _skipped = cleanup_old_filler_state(state_base_dir=str(root))
    assert reaped == 0
    assert (nested / "inner.json").exists()


def test_missing_root_is_a_noop(tmp_path):
    assert cleanup_old_filler_state(
        state_base_dir=str(tmp_path / "nothing-here")
    ) == (0, 0)


def test_default_root_resolves_under_claude_config_dir(
    tmp_path, monkeypatch
):
    """With no override, the reaper reads <CLAUDE_CONFIG_DIR>/
    pact-filler-state — the same call-time env read the hook uses, so a
    redirected config root redirects BOTH the writer and the reaper."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    _touch(tmp_path / "pact-filler-state", "stale.json", age_days=31)
    reaped, _skipped = cleanup_old_filler_state()
    assert reaped == 1
    assert not (tmp_path / "pact-filler-state" / "stale.json").exists()
