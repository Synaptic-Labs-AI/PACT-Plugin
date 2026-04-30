"""
Behavioral invariants for session_end.cleanup_wake_registry.

Pins the lead-only single-file unlink contract (NOT glob), the
path-traversal defense layers (is_safe_path_component +
resolve+relative_to), and Path.unlink missing_ok=True wrapped in
try/except OSError. Asserts the helper never raises across 9 negative
inputs (closes #602 reviewer-flagged coverage gap).
"""

import os
from pathlib import Path

import pytest

# Hooks dir is on sys.path via conftest.
import session_end
from session_end import cleanup_wake_registry

SESSION_END_PATH = (
    Path(__file__).resolve().parent.parent / "hooks" / "session_end.py"
)


@pytest.fixture(scope="module")
def src() -> str:
    return SESSION_END_PATH.read_text(encoding="utf-8")


# ---------- Source-level structural invariants ----------

def test_cleanup_wake_registry_targets_single_fixed_filename(src):
    """Lead-only scope: NO glob, single fixed-name STATE_FILE."""
    # Locate the function body to check just its scope.
    start = src.find("def cleanup_wake_registry(")
    assert start >= 0
    end = src.find("\ndef ", start + 1)
    body = src[start:end if end > 0 else len(src)]
    assert "inbox-wake-state.json" in body
    # The function must NOT use glob — single-file unlink only.
    assert ".glob(" not in body


def test_cleanup_wake_registry_uses_safe_path_component(src):
    start = src.find("def cleanup_wake_registry(")
    end = src.find("\ndef ", start + 1)
    body = src[start:end if end > 0 else len(src)]
    assert "is_safe_path_component" in body


def test_cleanup_wake_registry_uses_resolve_and_relative_to(src):
    """Symlink-escape defense layer."""
    start = src.find("def cleanup_wake_registry(")
    end = src.find("\ndef ", start + 1)
    body = src[start:end if end > 0 else len(src)]
    assert ".resolve()" in body
    assert ".relative_to(" in body


def test_cleanup_wake_registry_uses_missing_ok(src):
    start = src.find("def cleanup_wake_registry(")
    end = src.find("\ndef ", start + 1)
    body = src[start:end if end > 0 else len(src)]
    assert "missing_ok=True" in body


def test_cleanup_wake_registry_wraps_unlink_in_try_except_oserror(src):
    start = src.find("def cleanup_wake_registry(")
    end = src.find("\ndef ", start + 1)
    body = src[start:end if end > 0 else len(src)]
    assert "except OSError" in body


# ---------- Behavioral: never-raises across negative inputs ----------

@pytest.fixture
def home_root(tmp_path, monkeypatch):
    """Redirect Path.home() and shadow the resolved teams root."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


@pytest.mark.parametrize("bad_team", [
    "",
    "..",
    "../escape",
    "team/with-slash",
    "team\\with-backslash",
    ".",
    "team\x00null",
    "team\nwith-newline",
    "team with space",
])
def test_cleanup_wake_registry_rejects_bad_team_name(bad_team, home_root):
    """Each invalid team_name must early-return without touching disk
    and without raising."""
    cleanup_wake_registry(bad_team)  # returns None on rejection


def test_cleanup_wake_registry_handles_missing_team_dir(home_root):
    """team_name passes the safe-component gate but the team dir does
    not exist on disk — should be a quiet no-op."""
    cleanup_wake_registry("ghost-team")  # never raises


def test_cleanup_wake_registry_handles_present_state_file(home_root):
    team = "real-team"
    team_dir = home_root / ".claude" / "teams" / team
    team_dir.mkdir(parents=True)
    state_file = team_dir / "inbox-wake-state.json"
    state_file.write_text('{"v":1,"monitor_task_id":"abc"}', encoding="utf-8")
    assert state_file.exists()

    cleanup_wake_registry(team)

    assert not state_file.exists()


def test_cleanup_wake_registry_handles_state_file_already_gone(home_root):
    team = "team-no-state"
    (home_root / ".claude" / "teams" / team).mkdir(parents=True)
    cleanup_wake_registry(team)  # missing_ok=True


def test_cleanup_wake_registry_does_not_touch_unrelated_files(home_root):
    """Selective unlink — only inbox-wake-state.json."""
    team = "selective"
    team_dir = home_root / ".claude" / "teams" / team
    team_dir.mkdir(parents=True)
    other = team_dir / "config.json"
    other.write_text("{}", encoding="utf-8")
    state = team_dir / "inbox-wake-state.json"
    state.write_text("{}", encoding="utf-8")

    cleanup_wake_registry(team)

    assert other.exists()
    assert not state.exists()


def test_cleanup_wake_registry_resists_symlink_escape(home_root):
    """If team_dir resolves outside teams_root (symlink escape), the
    function must not unlink anything."""
    teams_root = home_root / ".claude" / "teams"
    teams_root.mkdir(parents=True)
    # Create a real "victim" directory outside the teams root that holds
    # a state file we don't want touched.
    victim_dir = home_root / "outside"
    victim_dir.mkdir()
    victim_state = victim_dir / "inbox-wake-state.json"
    victim_state.write_text("{}", encoding="utf-8")

    # Make a symlinked "team" that points at the victim dir.
    link = teams_root / "evil"
    try:
        os.symlink(victim_dir, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this filesystem")

    cleanup_wake_registry("evil")

    # The escape must have been refused — the victim file is intact.
    assert victim_state.exists()


def test_cleanup_wake_registry_call_site_in_session_end(src):
    """Belt-and-suspenders force-termination cleanup path: confirm
    cleanup_wake_registry is wired into the main session_end flow with
    the current team_name."""
    assert "cleanup_wake_registry(current_team_name)" in src
