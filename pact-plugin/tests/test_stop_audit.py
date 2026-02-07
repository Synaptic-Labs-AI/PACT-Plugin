"""
Tests for stop_audit.py audit functions.

Tests audit_tasks() and audit_team_state() which check for incomplete
workflows and active Agent Teams at session end.

Location: pact-plugin/tests/test_stop_audit.py
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from stop_audit import audit_tasks, audit_team_state


# =============================================================================
# Tests for audit_tasks()
# =============================================================================

class TestAuditTasks:
    """Tests for audit_tasks function."""

    def test_empty_task_list_returns_no_warnings(self):
        """Test empty task list produces no warnings."""
        warnings = audit_tasks([])

        assert warnings == []

    def test_all_completed_no_warnings(self):
        """Test all-completed task list produces no warnings."""
        tasks = [
            {"subject": "Feature: auth", "status": "completed", "metadata": {}},
            {"subject": "CODE: implement", "status": "completed", "metadata": {}},
        ]

        warnings = audit_tasks(tasks)

        assert warnings == []

    def test_detects_orphaned_agent_tasks(self):
        """Test detects orphaned in_progress agent tasks."""
        tasks = [
            {
                "subject": "pact-backend-coder: implement auth",
                "status": "in_progress",
                "metadata": {},
            },
            {
                "subject": "pact-test-engineer: test auth",
                "status": "in_progress",
                "metadata": {},
            },
        ]

        warnings = audit_tasks(tasks)

        # Should have orphaned agents warning + summary
        agent_warning = [w for w in warnings if "Agents still in_progress" in w]
        assert len(agent_warning) == 1
        assert "(2)" in agent_warning[0]
        assert "pact-backend-coder" in agent_warning[0]
        assert "pact-test-engineer" in agent_warning[0]

    def test_detects_unresolved_blockers(self):
        """Test detects unresolved blocker tasks."""
        tasks = [
            {
                "subject": "BLOCKER: auth module not responding",
                "status": "in_progress",
                "metadata": {"type": "blocker"},
            },
        ]

        warnings = audit_tasks(tasks)

        blocker_warning = [w for w in warnings if "Unresolved blockers" in w]
        assert len(blocker_warning) == 1
        assert "(1)" in blocker_warning[0]
        assert "BLOCKER: auth module not responding" in blocker_warning[0]

    def test_detects_unresolved_algedonic_tasks(self):
        """Test detects unresolved algedonic signal tasks."""
        tasks = [
            {
                "subject": "HALT: SECURITY vulnerability found",
                "status": "in_progress",
                "metadata": {"type": "algedonic"},
            },
        ]

        warnings = audit_tasks(tasks)

        blocker_warning = [w for w in warnings if "Unresolved blockers" in w]
        assert len(blocker_warning) == 1
        assert "HALT: SECURITY vulnerability found" in blocker_warning[0]

    def test_task_summary_with_in_progress(self):
        """Test task summary shows when in_progress tasks exist."""
        tasks = [
            {"subject": "Feature: auth", "status": "completed", "metadata": {}},
            {"subject": "pact-backend-coder: impl", "status": "in_progress", "metadata": {}},
            {"subject": "prep work", "status": "pending", "metadata": {}},
        ]

        warnings = audit_tasks(tasks)

        summary_warning = [w for w in warnings if "Task summary" in w]
        assert len(summary_warning) == 1
        assert "1 completed" in summary_warning[0]
        assert "1 in_progress" in summary_warning[0]
        assert "1 pending" in summary_warning[0]

    def test_task_summary_with_pending_only(self):
        """Test task summary shows when only pending tasks exist."""
        tasks = [
            {"subject": "Feature: auth", "status": "completed", "metadata": {}},
            {"subject": "prep work", "status": "pending", "metadata": {}},
        ]

        warnings = audit_tasks(tasks)

        summary_warning = [w for w in warnings if "Task summary" in w]
        assert len(summary_warning) == 1
        assert "1 completed" in summary_warning[0]
        assert "0 in_progress" in summary_warning[0]
        assert "1 pending" in summary_warning[0]

    def test_no_summary_when_all_completed(self):
        """Test no summary when no in_progress or pending tasks exist."""
        tasks = [
            {"subject": "Feature: auth", "status": "completed", "metadata": {}},
        ]

        warnings = audit_tasks(tasks)

        summary_warning = [w for w in warnings if "Task summary" in w]
        assert len(summary_warning) == 0

    def test_truncates_many_blockers(self):
        """Test blockers list truncated at 3 with '+N more' suffix."""
        tasks = [
            {"subject": f"BLOCKER: issue {i}", "status": "in_progress", "metadata": {"type": "blocker"}}
            for i in range(5)
        ]

        warnings = audit_tasks(tasks)

        blocker_warning = [w for w in warnings if "Unresolved blockers" in w]
        assert len(blocker_warning) == 1
        assert "(5)" in blocker_warning[0]
        assert "+2 more" in blocker_warning[0]

    def test_truncates_many_orphaned_agents(self):
        """Test orphaned agents list truncated at 3 with '+N more' suffix."""
        agent_types = [
            "pact-backend-coder",
            "pact-frontend-coder",
            "pact-database-engineer",
            "pact-test-engineer",
            "pact-architect",
        ]
        tasks = [
            {"subject": f"{a}: task {i}", "status": "in_progress", "metadata": {}}
            for i, a in enumerate(agent_types)
        ]

        warnings = audit_tasks(tasks)

        agent_warning = [w for w in warnings if "Agents still in_progress" in w]
        assert len(agent_warning) == 1
        assert "(5)" in agent_warning[0]
        assert "+2 more" in agent_warning[0]

    def test_feature_tasks_not_counted_as_orphaned(self):
        """Test non-agent in_progress tasks are not counted as orphaned agents."""
        tasks = [
            {"subject": "Feature: auth", "status": "in_progress", "metadata": {}},
        ]

        warnings = audit_tasks(tasks)

        agent_warning = [w for w in warnings if "Agents still in_progress" in w]
        assert len(agent_warning) == 0

    def test_handles_missing_metadata(self):
        """Test handles tasks with missing metadata field."""
        tasks = [
            {"subject": "pact-backend-coder: task", "status": "in_progress"},
        ]

        warnings = audit_tasks(tasks)

        # Should still detect the orphaned agent without crashing
        agent_warning = [w for w in warnings if "Agents still in_progress" in w]
        assert len(agent_warning) == 1

    def test_handles_missing_subject(self):
        """Test handles tasks with missing subject field."""
        tasks = [
            {"status": "in_progress", "metadata": {"type": "blocker"}},
        ]

        warnings = audit_tasks(tasks)

        # Should still detect the blocker using metadata
        blocker_warning = [w for w in warnings if "Unresolved blockers" in w]
        assert len(blocker_warning) == 1

    def test_recognizes_all_agent_prefixes(self):
        """Test recognizes all PACT agent task prefixes."""
        agent_prefixes = [
            "pact-preparer:",
            "pact-architect:",
            "pact-backend-coder:",
            "pact-frontend-coder:",
            "pact-database-engineer:",
            "pact-test-engineer:",
            "pact-memory-agent:",
        ]
        tasks = [
            {"subject": f"{prefix} some task", "status": "in_progress", "metadata": {}}
            for prefix in agent_prefixes
        ]

        warnings = audit_tasks(tasks)

        agent_warning = [w for w in warnings if "Agents still in_progress" in w]
        assert len(agent_warning) == 1
        assert f"({len(agent_prefixes)})" in agent_warning[0]


# =============================================================================
# Tests for audit_team_state()
# =============================================================================

class TestAuditTeamState:
    """Tests for audit_team_state function."""

    def test_no_active_teams_returns_empty(self, tmp_path, monkeypatch):
        """Test returns empty list when no active teams exist."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        messages = audit_team_state()

        assert messages == []

    def test_single_team_with_active_members(self, tmp_path, monkeypatch):
        """Test reports single team with active members."""
        # Set up team directory with config
        teams_dir = tmp_path / ".claude" / "teams" / "v3-agent-teams"
        teams_dir.mkdir(parents=True)
        config = {
            "members": [
                {"name": "backend-1", "type": "pact-backend-coder", "status": "active"},
                {"name": "architect-1", "type": "pact-architect", "status": "active"},
            ]
        }
        (teams_dir / "config.json").write_text(json.dumps(config))

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        messages = audit_team_state()

        assert len(messages) == 3  # header + team entry + TeamDelete instruction
        assert "Agent Teams still active" in messages[0]
        assert "v3-agent-teams" in messages[1]
        assert "2 active teammate(s)" in messages[1]
        assert "backend-1" in messages[1]
        assert "architect-1" in messages[1]
        assert "TeamDelete" in messages[2]

    def test_team_with_no_active_members(self, tmp_path, monkeypatch):
        """Test reports team with no active members differently."""
        teams_dir = tmp_path / ".claude" / "teams" / "idle-team"
        teams_dir.mkdir(parents=True)
        config = {
            "members": [
                {"name": "backend-1", "type": "pact-backend-coder", "status": "stopped"},
            ]
        }
        (teams_dir / "config.json").write_text(json.dumps(config))

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        messages = audit_team_state()

        # Team dir exists but no active members -- should NOT generate messages
        # because active list is empty (the member has status "stopped")
        assert messages == []

    def test_team_with_no_members_key(self, tmp_path, monkeypatch):
        """Test handles team with no members in config (empty list returned)."""
        teams_dir = tmp_path / ".claude" / "teams" / "empty-team"
        teams_dir.mkdir(parents=True)
        (teams_dir / "config.json").write_text(json.dumps({"name": "empty-team"}))

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        messages = audit_team_state()

        # get_team_members returns [] for missing members key
        # audit_team_state enters the "else" branch: "no members found"
        assert len(messages) == 3  # header + message + TeamDelete instruction
        assert "no members found" in messages[1]
        assert "TeamDelete" in messages[2]

    def test_team_with_no_config_file(self, tmp_path, monkeypatch):
        """Test handles team directory without config.json."""
        teams_dir = tmp_path / ".claude" / "teams" / "no-config"
        teams_dir.mkdir(parents=True)
        # No config.json created

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        messages = audit_team_state()

        # get_team_members returns [] (no config file)
        # audit_team_state enters "else" branch: "no members found"
        assert len(messages) == 3  # header + message + TeamDelete instruction
        assert "no members found" in messages[1]
        assert "TeamDelete" in messages[2]

    def test_multiple_teams(self, tmp_path, monkeypatch):
        """Test reports multiple active teams."""
        for team_name in ["team-alpha", "team-beta"]:
            teams_dir = tmp_path / ".claude" / "teams" / team_name
            teams_dir.mkdir(parents=True)
            config = {
                "members": [
                    {"name": "coder-1", "type": "pact-backend-coder", "status": "active"},
                ]
            }
            (teams_dir / "config.json").write_text(json.dumps(config))

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        messages = audit_team_state()

        # header + 2 team entries + TeamDelete instruction
        assert len(messages) == 4
        assert "Agent Teams still active" in messages[0]
        team_entries = [m for m in messages[1:] if "Team '" in m]
        assert len(team_entries) == 2
        assert "TeamDelete" in messages[-1]

    def test_truncates_many_active_members(self, tmp_path, monkeypatch):
        """Test truncates member list at 5 with '+N more' suffix."""
        teams_dir = tmp_path / ".claude" / "teams" / "big-team"
        teams_dir.mkdir(parents=True)
        config = {
            "members": [
                {"name": f"member-{i}", "type": "pact-backend-coder", "status": "active"}
                for i in range(7)
            ]
        }
        (teams_dir / "config.json").write_text(json.dumps(config))

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        messages = audit_team_state()

        team_entry = [m for m in messages if "big-team" in m][0]
        assert "7 active teammate(s)" in team_entry
        assert "+2 more" in team_entry

    def test_exactly_five_members_no_truncation(self, tmp_path, monkeypatch):
        """Test exactly 5 members shows all without truncation."""
        teams_dir = tmp_path / ".claude" / "teams" / "medium-team"
        teams_dir.mkdir(parents=True)
        config = {
            "members": [
                {"name": f"member-{i}", "type": "pact-backend-coder", "status": "active"}
                for i in range(5)
            ]
        }
        (teams_dir / "config.json").write_text(json.dumps(config))

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        messages = audit_team_state()

        team_entry = [m for m in messages if "medium-team" in m][0]
        assert "5 active teammate(s)" in team_entry
        assert "+0 more" not in team_entry
        assert "more)" not in team_entry

    def test_mixed_active_and_stopped_members(self, tmp_path, monkeypatch):
        """Test only counts active members, ignoring stopped ones."""
        teams_dir = tmp_path / ".claude" / "teams" / "mixed-team"
        teams_dir.mkdir(parents=True)
        config = {
            "members": [
                {"name": "active-1", "type": "pact-backend-coder", "status": "active"},
                {"name": "stopped-1", "type": "pact-architect", "status": "stopped"},
                {"name": "active-2", "type": "pact-test-engineer", "status": "active"},
            ]
        }
        (teams_dir / "config.json").write_text(json.dumps(config))

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        messages = audit_team_state()

        team_entry = [m for m in messages if "mixed-team" in m][0]
        assert "2 active teammate(s)" in team_entry
        assert "active-1" in team_entry
        assert "active-2" in team_entry
        assert "stopped-1" not in team_entry

    def test_teams_dir_missing_returns_empty(self, tmp_path, monkeypatch):
        """Test returns empty when ~/.claude/teams/ directory doesn't exist."""
        # tmp_path has no .claude/teams/ at all
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        messages = audit_team_state()

        assert messages == []

    def test_member_missing_name_uses_unknown(self, tmp_path, monkeypatch):
        """Test handles member entries missing the 'name' field."""
        teams_dir = tmp_path / ".claude" / "teams" / "unnamed-team"
        teams_dir.mkdir(parents=True)
        config = {
            "members": [
                {"type": "pact-backend-coder", "status": "active"},
            ]
        }
        (teams_dir / "config.json").write_text(json.dumps(config))

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        messages = audit_team_state()

        team_entry = [m for m in messages if "unnamed-team" in m][0]
        assert "unknown" in team_entry
