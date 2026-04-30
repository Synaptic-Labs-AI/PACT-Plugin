"""
Hook-side test for session_end.cleanup_wake_registry()'s glob behavior.

Per architect §15.4: the helper globs `inbox-wake-state-*.json` (not a
hardcoded single filename) so a single force-termination cleanup pass
removes the lead's STATE_FILE plus every teammate's STATE_FILE that
wasn't reached by their respective ## Shutdown Teardown invocation.

Path-traversal discipline (§9): is_safe_path_component(team_name) +
team_dir.relative_to(teams_root) gate the glob; the glob result inherits
the validation transitively.
"""
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


@pytest.fixture
def teams_root(tmp_path: Path, monkeypatch) -> Path:
    """Point Path.home() at tmp_path and return the teams root directory."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / ".claude" / "teams"
    root.mkdir(parents=True)
    return root


class TestCleanupWakeRegistryGlob:
    """The helper unlinks every per-agent inbox-wake-state sidecar in one pass."""

    def test_unlinks_lead_and_multiple_teammate_sidecars(self, teams_root: Path):
        """Lead + several teammate STATE_FILEs all match the glob and are unlinked."""
        from session_end import cleanup_wake_registry

        team_name = "pact-test01"
        team_dir = teams_root / team_name
        team_dir.mkdir()

        sidecars = [
            team_dir / "inbox-wake-state-team-lead.json",
            team_dir / "inbox-wake-state-architect.json",
            team_dir / "inbox-wake-state-preparer.json",
            team_dir / "inbox-wake-state-test-engineer.json",
        ]
        for s in sidecars:
            s.write_text('{"v": 1, "monitor_task_id": "abc", "armed_at": "2026-04-30T00:00:00Z"}')

        cleanup_wake_registry(team_name)

        for s in sidecars:
            assert not s.exists(), (
                f"cleanup_wake_registry must unlink {s.name} via glob pattern"
            )

    def test_does_not_unlink_unrelated_files_in_team_dir(self, teams_root: Path):
        """Glob is `inbox-wake-state-*.json` only — siblings remain untouched."""
        from session_end import cleanup_wake_registry

        team_name = "pact-test02"
        team_dir = teams_root / team_name
        team_dir.mkdir()

        # Wake-state sidecar (should be unlinked) + unrelated team artifacts.
        wake_state = team_dir / "inbox-wake-state-team-lead.json"
        wake_state.write_text("{}")
        (team_dir / "config.json").write_text("{}")
        (team_dir / "inboxes").mkdir()
        (team_dir / "inboxes" / "team-lead.json").write_text("[]")
        (team_dir / "tasks.json").write_text("[]")

        cleanup_wake_registry(team_name)

        assert not wake_state.exists(), "Wake-state sidecar must be unlinked"
        assert (team_dir / "config.json").exists(), "Glob must not match config.json"
        assert (team_dir / "inboxes" / "team-lead.json").exists(), (
            "Glob must not descend into inboxes/ directory"
        )
        assert (team_dir / "tasks.json").exists(), "Glob must not match tasks.json"

    def test_no_state_files_is_a_no_op(self, teams_root: Path):
        """Empty team dir: glob yields no matches, helper returns cleanly."""
        from session_end import cleanup_wake_registry

        team_name = "pact-test03"
        team_dir = teams_root / team_name
        team_dir.mkdir()

        # Should not raise even when there's nothing to unlink.
        cleanup_wake_registry(team_name)

        # Team dir intact.
        assert team_dir.is_dir()
