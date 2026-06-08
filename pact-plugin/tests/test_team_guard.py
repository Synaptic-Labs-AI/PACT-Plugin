"""
Tests for team_guard.py — PreToolUse hook that blocks Task dispatch
if team_name is specified but team doesn't exist.

Tests cover:
1. Task call with team_name and team exists -> allow (exit 0)
2. Task call with team_name and team doesn't exist -> block (exit 2)
3. Task call without team_name -> allow (always, no check needed)
4. Non-Task tool call -> allow (hook shouldn't even fire, but graceful no-op)
5. Missing team context -> allow (no team name available)
6. main() entry point: stdin JSON parsing, exit codes, output format
"""
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


class TestTeamGuard:
    """Tests for team_guard.check_team_exists()."""

    def test_allows_when_team_exists(self, tmp_path):
        from team_guard import check_team_exists

        team_dir = tmp_path / "teams" / "pact-abc12345"
        team_dir.mkdir(parents=True)
        config = team_dir / "config.json"
        config.write_text('{"members": []}')

        result = check_team_exists(
            tool_input={"team_name": "pact-abc12345"},
            teams_dir=str(tmp_path / "teams")
        )

        assert result is None  # None means allow

    def test_blocks_when_team_missing(self, tmp_path):
        from team_guard import check_team_exists

        result = check_team_exists(
            tool_input={"team_name": "pact-abc12345"},
            teams_dir=str(tmp_path / "teams")
        )

        assert result is not None
        assert "does not exist" in result

    def test_allows_when_no_team_name(self, tmp_path):
        from team_guard import check_team_exists

        result = check_team_exists(
            tool_input={"prompt": "explore the codebase"},
            teams_dir=str(tmp_path / "teams")
        )

        assert result is None

    def test_allows_when_empty_tool_input(self, tmp_path):
        from team_guard import check_team_exists

        result = check_team_exists(
            tool_input={},
            teams_dir=str(tmp_path / "teams")
        )

        assert result is None

    def test_normalizes_uppercase_team_name(self, tmp_path):
        """Uppercase team_name should be normalized to lowercase for directory lookup."""
        from team_guard import check_team_exists

        # Create team directory with lowercase name (as TeamCreate does)
        team_dir = tmp_path / "teams" / "pact-abc12345"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text('{"members": []}')

        # Pass uppercase team_name — should still find the lowercase directory
        result = check_team_exists(
            tool_input={"team_name": "PACT-abc12345"},
            teams_dir=str(tmp_path / "teams")
        )

        assert result is None  # None means allow (found the team)


class TestMainEntryPoint:
    """Tests for team_guard.main() stdin/stdout/exit behavior."""

    def test_main_exits_0_when_team_exists(self, tmp_path, capsys):
        from team_guard import main

        # Create team directory with config
        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text('{"members": []}')

        input_data = json.dumps({
            "tool_input": {"team_name": "pact-test"}
        })

        with patch("team_guard.check_team_exists", return_value=None), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"suppressOutput": True}

    def test_main_exits_2_when_team_missing(self, capsys):
        from team_guard import main

        input_data = json.dumps({
            "tool_input": {"team_name": "pact-nonexistent"}
        })

        error_msg = "Team 'pact-nonexistent' does not exist yet."
        with patch("team_guard.check_team_exists", return_value=error_msg), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_main_exits_0_on_invalid_json(self):
        from team_guard import main

        with patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_0_when_no_team_name(self):
        from team_guard import main

        input_data = json.dumps({
            "tool_input": {"prompt": "explore codebase"}
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0


class TestFailClosedModuleLoad:
    """C5b (#926 / PR #660 discipline): a raise from team_guard's cross-package
    import must DENY (exit 2 + permissionDecision='deny'), NOT crash-and-fail-open.
    A crashed PreToolUse hook (exit 1) is treated by the platform as non-blocking,
    so the Task would PROCEED and the team-existence gate would silently fail-open.
    Mirrors test_dispatch_gate_smoke.test_fail_closed_module_load: subprocess
    isolation, a sabotaged copy of shared/ (never pop shared.* from sys.modules in
    the test process)."""

    def test_fail_closed_module_load(self, tmp_path):
        import os
        import shutil
        import subprocess

        repo_hooks = Path(__file__).parent.parent / "hooks"
        sabotage_root = tmp_path / "sabotage"
        sabotage_shared = sabotage_root / "shared"
        # Copy the real shared/ tree so every OTHER submodule imports normally.
        shutil.copytree(repo_hooks / "shared", sabotage_shared)
        # Overwrite ONLY paths.py — team_guard's sole shared import — with a raise
        # so the wrapped `from shared.paths import ...` fires the except branch.
        (sabotage_shared / "paths.py").write_text(
            "raise ImportError('sabotage: forced module-load failure')\n",
            encoding="utf-8",
        )

        env = os.environ.copy()
        # Sabotage dir FIRST so its `shared` package wins. team_guard's
        # conditional `if str(_hooks_dir) not in sys.path` insert is skipped here
        # (repo_hooks is already on PYTHONPATH), preserving this ordering.
        env["PYTHONPATH"] = os.pathsep.join([str(sabotage_root), str(repo_hooks)])
        env["PYTHONSAFEPATH"] = "1"  # disable script-dir auto-insert → PYTHONPATH wins

        proc = subprocess.run(
            [sys.executable, str(repo_hooks / "team_guard.py")],
            input=json.dumps({
                "hook_event_name": "PreToolUse",
                "session_id": "test",
                "tool_name": "Task",
                "tool_input": {"team_name": "pact-x"},
            }),
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        assert proc.returncode == 2, (
            f"expected exit 2 (fail-closed deny), got {proc.returncode}. "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
        out = json.loads(proc.stdout.strip())
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        assert (
            "load failure" in hso["permissionDecisionReason"].lower()
            or "module imports" in hso["permissionDecisionReason"].lower()
        )


class TestTeamsDirResolverDerivationUnderConfigDir:
    """F1 (#926 review): the teams_dir=None resolver path
    (get_claude_config_dir() / "teams") must resolve under a non-default
    CLAUDE_CONFIG_DIR. Every other team_guard test injects teams_dir= (DI seam,
    bypasses the resolver) or mocks check_team_exists, so this DISPATCH-CRITICAL
    path -- a wrong teams dir -> 'team does not exist' -> deny -> blocks Task
    dispatch (the #926 symptom for team_guard) -- is otherwise unexercised in-CI.

    NON-VACUITY: Path.home is redirected to a SEPARATE EMPTY dir. If the resolver
    call were reverted to Path.home()/".claude"/"teams", it would read the empty
    home -> team 'missing' -> deny-reason -> the allow assertion FAILS
    behaviorally (verified: source-neuter of team_guard's resolver line -> {1 failed}).
    """

    def test_allows_existing_team_under_relocated_config(self, tmp_path, monkeypatch):
        from team_guard import check_team_exists

        config_dir = tmp_path / "config"
        team_dir = config_dir / "teams" / "pact-relocated"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text('{"members": []}')
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))

        # teams_dir=None -> exercises the get_claude_config_dir()/"teams" path.
        assert check_team_exists({"team_name": "pact-relocated"}) is None

    def test_blocks_absent_team_under_relocated_config(self, tmp_path, monkeypatch):
        # Complement so the allow-assertion above is not trivially always-None:
        # a genuinely absent team under $CONFIG/teams -> deny-reason (not None).
        from team_guard import check_team_exists

        config_dir = tmp_path / "config"
        (config_dir / "teams").mkdir(parents=True)
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))

        assert check_team_exists({"team_name": "pact-missing"}) is not None
