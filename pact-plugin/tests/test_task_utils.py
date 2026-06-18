"""
Tests for shared/task_utils.py — Task system integration utilities.

Tests cover:
1. get_task_list: filesystem reading, session ID resolution, error handling
2. find_feature_task: top-level task identification, phase prefix exclusion
3. find_current_phase: active phase detection
4. find_active_agents: agent task filtering by prefix and status
5. find_blockers: blocker/algedonic task detection
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks" / "shared"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_task(tasks_dir, task_id, task_data):
    """Write a task JSON file to the tasks directory."""
    task_file = tasks_dir / f"{task_id}.json"
    task_data.setdefault("id", task_id)
    task_file.write_text(json.dumps(task_data), encoding="utf-8")


# ---------------------------------------------------------------------------
# get_task_list
# ---------------------------------------------------------------------------

class TestGetTaskList:
    """Tests for get_task_list() — filesystem-based task reading."""

    def test_returns_none_when_no_session_id(self, monkeypatch, pact_context):
        # SOLO context (team_name="") so this exercises the REAL solo no-id
        # guard, not a vacuous pass via the team-branch short-circuit.
        from task_utils import get_task_list
        pact_context(session_id="", team_name="")  # No session ID, no team
        monkeypatch.delenv("CLAUDE_CODE_TASK_LIST_ID", raising=False)
        result = get_task_list()
        assert result is None

    def test_returns_none_when_tasks_dir_missing(self, tmp_path, monkeypatch, pact_context):
        from task_utils import get_task_list
        pact_context(session_id="test-session")
        monkeypatch.delenv("CLAUDE_CODE_TASK_LIST_ID", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = get_task_list()
        assert result is None

    def test_reads_tasks_from_filesystem(self, tmp_path, monkeypatch, pact_context):
        # TEAM branch: the pact_context fixture defaults team_name="test-team",
        # so a team session resolves tasks under {team_name}, not {session_id}.
        from task_utils import get_task_list
        pact_context(session_id="test-session")
        monkeypatch.delenv("CLAUDE_CODE_TASK_LIST_ID", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        tasks_dir = tmp_path / ".claude" / "tasks" / "test-team"
        tasks_dir.mkdir(parents=True)
        write_task(tasks_dir, "1", {"subject": "Test task", "status": "pending"})
        write_task(tasks_dir, "2", {"subject": "Another task", "status": "in_progress"})

        result = get_task_list()
        assert result is not None
        assert len(result) == 2

    def test_prefers_task_list_id_over_session_id(self, tmp_path, monkeypatch, pact_context):
        # SOLO context (team_name=""): CLAUDE_CODE_TASK_LIST_ID is honored only
        # in the solo branch (the team branch resolves by team_name and ignores
        # the env var). Verifies the preserved solo env-var precedence + gives
        # the solo branch positive real-resolver coverage.
        from task_utils import get_task_list
        pact_context(session_id="session-id", team_name="")
        monkeypatch.setenv("CLAUDE_CODE_TASK_LIST_ID", "task-list-id")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Create tasks under task-list-id, not session-id
        tasks_dir = tmp_path / ".claude" / "tasks" / "task-list-id"
        tasks_dir.mkdir(parents=True)
        write_task(tasks_dir, "1", {"subject": "Task", "status": "pending"})

        result = get_task_list()
        assert result is not None
        assert len(result) == 1

    def test_returns_none_for_empty_dir(self, tmp_path, monkeypatch, pact_context):
        # SOLO context (team_name="") so the empty-dir->None path is exercised
        # through the REAL solo resolver (session_id dir), not vacuously via the
        # team-branch short-circuit.
        from task_utils import get_task_list
        pact_context(session_id="test-session", team_name="")
        monkeypatch.delenv("CLAUDE_CODE_TASK_LIST_ID", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        tasks_dir = tmp_path / ".claude" / "tasks" / "test-session"
        tasks_dir.mkdir(parents=True)

        result = get_task_list()
        assert result is None

    def test_skips_invalid_json_files(self, tmp_path, monkeypatch, pact_context):
        # TEAM branch (default team_name="test-team"). iter_team_task_jsons
        # skips the unparseable file the same way the solo glob did.
        from task_utils import get_task_list
        pact_context(session_id="test-session")
        monkeypatch.delenv("CLAUDE_CODE_TASK_LIST_ID", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        tasks_dir = tmp_path / ".claude" / "tasks" / "test-team"
        tasks_dir.mkdir(parents=True)
        (tasks_dir / "bad.json").write_text("not json", encoding="utf-8")
        write_task(tasks_dir, "1", {"subject": "Good task", "status": "pending"})

        result = get_task_list()
        assert result is not None
        assert len(result) == 1

    def test_returns_none_on_exception(self, tmp_path, monkeypatch, pact_context):
        # SOLO context (team_name="") so the glob raises inside the solo
        # branch's own try/except — the path this test targets. Under a team
        # context the team branch would short-circuit before the patched glob,
        # making the assertion vacuous.
        from task_utils import get_task_list
        pact_context(session_id="test-session", team_name="")
        monkeypatch.delenv("CLAUDE_CODE_TASK_LIST_ID", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        tasks_dir = tmp_path / ".claude" / "tasks" / "test-session"
        tasks_dir.mkdir(parents=True)

        with patch.object(Path, "glob", side_effect=PermissionError("denied")):
            result = get_task_list()
        assert result is None

    def test_solo_resolves_tasks_by_session_id(self, tmp_path, monkeypatch, pact_context):
        # Positive real-resolver coverage of the SOLO branch's bare-session_id
        # path (no CLAUDE_CODE_TASK_LIST_ID): team_name="" routes to the solo
        # branch, which must resolve ~/.claude/tasks/{session_id}/ and read it.
        # Guards the preserved solo path the team-dir fix must NOT regress, and
        # is non-vacuous (a team context would short-circuit before this path).
        from task_utils import get_task_list
        pact_context(session_id="solo-session", team_name="")
        monkeypatch.delenv("CLAUDE_CODE_TASK_LIST_ID", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        tasks_dir = tmp_path / ".claude" / "tasks" / "solo-session"
        tasks_dir.mkdir(parents=True)
        write_task(tasks_dir, "1", {"subject": "Solo task", "status": "in_progress"})

        result = get_task_list()
        assert result is not None
        assert len(result) == 1
        assert result[0]["subject"] == "Solo task"

    def test_solo_rejects_traversal_task_list_id(self, tmp_path, monkeypatch, pact_context):
        # F2 (security): a user-controlled CLAUDE_CODE_TASK_LIST_ID that is not a
        # safe single path component must be rejected (-> None) BEFORE the
        # path-join, so it cannot escape the tasks base and read *.json outside.
        # Solo context (team_name="") — the env var is consulted only there.
        # NON-VACUOUS: a *.json is planted at the traversal target, so WITHOUT
        # the is_safe_path_component guard get_task_list would resolve
        # base/"../escape" -> the planted dir and return the LEAKED task
        # (non-None). The guard makes it None. (Revert the guard -> this FAILS.)
        from task_utils import get_task_list
        pact_context(session_id="session-id", team_name="")
        base = tmp_path / "tasks"
        base.mkdir(parents=True)
        escape = tmp_path / "escape"
        escape.mkdir()
        (escape / "secret.json").write_text(
            json.dumps({"id": "x", "subject": "LEAKED"}), encoding="utf-8"
        )
        for hostile in ("../escape", "../../etc", "..", "a/b", "foo/../bar"):
            monkeypatch.setenv("CLAUDE_CODE_TASK_LIST_ID", hostile)
            assert get_task_list(tasks_base_dir=str(base)) is None, (
                f"unsafe task_list_id {hostile!r} must be rejected before the "
                f"path-join — must NOT resolve out-of-base and read the planted "
                f"secret (that is the F2 traversal-read vector)"
            )

    def test_solo_accepts_legit_task_list_id(self, tmp_path, monkeypatch, pact_context):
        # Guardrail: a legitimate single-component task-list id (pact-<slug> /
        # UUID / multi-session pointer) still passes is_safe_path_component and
        # resolves — proves the F2 validation adds NO solo regression.
        from task_utils import get_task_list
        pact_context(session_id="session-id", team_name="")
        monkeypatch.setenv("CLAUDE_CODE_TASK_LIST_ID", "pact-abcd1234")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        tasks_dir = tmp_path / ".claude" / "tasks" / "pact-abcd1234"
        tasks_dir.mkdir(parents=True)
        write_task(tasks_dir, "1", {"subject": "Legit", "status": "in_progress"})
        result = get_task_list()
        assert result is not None and len(result) == 1
        assert result[0]["subject"] == "Legit"

    def test_solo_skips_symlinked_json_file(self, tmp_path, monkeypatch, pact_context):
        # R3 (security): a *.json INSIDE the (legit) tasks dir that is a SYMLINK
        # to a file OUTSIDE the base must be SKIPPED, not followed-and-read.
        # NON-VACUOUS: the symlink targets a planted out-of-base secret; WITHOUT
        # the per-file is_symlink skip the glob follows it and reads LEAKED. The
        # defense skips it; only the real (regular-file) task is returned.
        from task_utils import get_task_list
        pact_context(session_id="session-id", team_name="")
        base = tmp_path / "tasks"
        tdir = base / "solo-x"
        tdir.mkdir(parents=True)
        secret = tmp_path / "secret.json"  # OUTSIDE base
        secret.write_text(json.dumps({"id": "leak", "subject": "LEAKED"}), encoding="utf-8")
        write_task(tdir, "1", {"subject": "Real", "status": "in_progress"})
        (tdir / "leak.json").symlink_to(secret)
        monkeypatch.setenv("CLAUDE_CODE_TASK_LIST_ID", "solo-x")
        result = get_task_list(tasks_base_dir=str(base))
        subjects = {t["subject"] for t in (result or [])}
        assert "LEAKED" not in subjects, (
            "a symlinked *.json pointing outside the base must be SKIPPED, not "
            "read — without the is_symlink skip the glob follows it -> LEAKED"
        )
        assert subjects == {"Real"}

    def test_solo_rejects_symlink_dir_escaping_base(self, tmp_path, monkeypatch, pact_context):
        # R3 (security): a {task_list_id} dir that is itself a SYMLINK escaping
        # the base must yield None — the resolve/relative_to anchor rejects it.
        # The NAME passes is_safe_path_component (safe component); the escape is
        # via the symlink TARGET. NON-VACUOUS: without the anchor, exists()
        # follows the symlink and the glob reads the planted out-of-base secret.
        from task_utils import get_task_list
        pact_context(session_id="session-id", team_name="")
        base = tmp_path / "tasks"
        base.mkdir(parents=True)
        outside = tmp_path / "escapedir"  # OUTSIDE base
        outside.mkdir()
        (outside / "secret.json").write_text(
            json.dumps({"id": "leak", "subject": "LEAKED"}), encoding="utf-8"
        )
        (base / "evil-link").symlink_to(outside, target_is_directory=True)
        monkeypatch.setenv("CLAUDE_CODE_TASK_LIST_ID", "evil-link")
        result = get_task_list(tasks_base_dir=str(base))
        assert result is None, (
            "a {task_list_id} dir that is a symlink escaping the base must be "
            "rejected by the resolve/relative_to anchor -> None (without it, "
            "exists() follows the symlink and the glob reads the out-of-base secret)"
        )

    def test_solo_skips_dotfile_json(self, tmp_path, monkeypatch, pact_context):
        # R4 (content-hygiene parity): a dotfile-prefixed *.json in the solo
        # tasks dir must be SKIPPED, mirroring the team branch. NON-VACUOUS:
        # pathlib glob('*.json') INCLUDES '.evil.json', so WITHOUT the
        # dotfile-skip get_task_list reads it (count inflation). (Revert the
        # dotfile-skip -> DOTFILE-INJECTED appears -> this FAILS.)
        from task_utils import get_task_list
        pact_context(session_id="session-id", team_name="")
        base = tmp_path / "tasks"
        tdir = base / "solo-x"
        tdir.mkdir(parents=True)
        write_task(tdir, "1", {"subject": "Real", "status": "in_progress"})
        (tdir / ".evil.json").write_text(
            json.dumps({"id": "e", "subject": "DOTFILE-INJECTED"}), encoding="utf-8"
        )
        monkeypatch.setenv("CLAUDE_CODE_TASK_LIST_ID", "solo-x")
        result = get_task_list(tasks_base_dir=str(base))
        subjects = {t["subject"] for t in (result or [])}
        assert "DOTFILE-INJECTED" not in subjects, (
            "a dotfile-prefixed *.json must be skipped (pathlib glob includes "
            "it; without the dotfile-skip it is read)"
        )
        assert subjects == {"Real"}

    def test_solo_skips_non_dict_json(self, tmp_path, monkeypatch, pact_context):
        # R4 (content-hygiene parity): a malformed-but-valid JSON that parses to
        # a NON-dict (list/int/str) must be SKIPPED, not appended, mirroring the
        # team branch's isinstance(dict) guard. NON-VACUOUS: without the guard
        # the non-dict is appended AND a downstream reader (find_feature_task)
        # calls .get() on it -> AttributeError. (Revert the guard -> this FAILS,
        # either the all-dict assert or the find_feature_task crash.)
        from task_utils import get_task_list, find_feature_task
        pact_context(session_id="session-id", team_name="")
        base = tmp_path / "tasks"
        tdir = base / "solo-x"
        tdir.mkdir(parents=True)
        write_task(tdir, "1", {"subject": "Real", "status": "in_progress"})
        (tdir / "bad.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")  # valid JSON, non-dict
        monkeypatch.setenv("CLAUDE_CODE_TASK_LIST_ID", "solo-x")
        result = get_task_list(tasks_base_dir=str(base))
        assert result is not None
        assert all(isinstance(t, dict) for t in result), (
            "a non-dict parse ([1,2,3]) must be skipped, not appended"
        )
        assert {t["subject"] for t in result} == {"Real"}
        # Downstream consumer must not crash on the returned list.
        find_feature_task(result)  # raises AttributeError if a non-dict leaked through

    def test_solo_r1_unique_nonallowlisted_in_base_name(self, tmp_path, monkeypatch, pact_context):
        # R2-F3 (R1-UNIQUENESS pin, spec'd by test-engineer): a non-allowlisted
        # but IN-BASE task_list_id must be rejected by is_safe_path_component
        # (R1) BEFORE the path-join. "a b" (space ∉ [A-Za-z0-9_-]) is rejected by
        # R1, but base/"a b" resolves UNDER base so R3's resolve/relative_to
        # anchor PASSES it — only R1 catches it. NON-VACUOUS vs R1 ALONE: revert
        # ONLY the is_safe_path_component guard (R3 intact) -> base/"a b" passes
        # the anchor + exists -> the planted task is read -> non-None -> this
        # FAILS. (NUL / "../escape" would NOT prove this — R3 masks them:
        # resolve() raises ValueError / escapes base, so both R1+R3 catch them.)
        from task_utils import get_task_list
        pact_context(session_id="session-id", team_name="")
        base = tmp_path / "tasks"
        d = base / "a b"
        d.mkdir(parents=True)
        (d / "1.json").write_text(
            json.dumps({"id": "1", "subject": "NONALLOWLISTED"}), encoding="utf-8"
        )
        monkeypatch.setenv("CLAUDE_CODE_TASK_LIST_ID", "a b")
        assert get_task_list(tasks_base_dir=str(base)) is None, (
            "a non-allowlisted in-base task_list_id must be rejected by "
            "is_safe_path_component (R1) BEFORE the path-join — R3's anchor "
            "passes it (in-base), so this is the R1-UNIQUE case proving the "
            "guard is load-bearing"
        )


# ---------------------------------------------------------------------------
# find_feature_task
# ---------------------------------------------------------------------------

class TestFindFeatureTask:
    """Tests for find_feature_task() — top-level task identification."""

    def test_finds_feature_task(self):
        from task_utils import find_feature_task
        tasks = [
            {"id": "1", "subject": "Implement auth system", "status": "in_progress"},
            {"id": "2", "subject": "PREPARE: auth", "status": "completed",
             "blockedBy": ["1"]},
        ]
        result = find_feature_task(tasks)
        assert result is not None
        assert result["id"] == "1"

    def test_skips_phase_tasks(self):
        from task_utils import find_feature_task
        tasks = [
            {"id": "1", "subject": "PREPARE: feature", "status": "in_progress"},
            {"id": "2", "subject": "CODE: feature", "status": "pending",
             "blockedBy": ["1"]},
        ]
        result = find_feature_task(tasks)
        assert result is None

    def test_skips_blocked_tasks(self):
        from task_utils import find_feature_task
        tasks = [
            {"id": "1", "subject": "Feature A", "status": "in_progress",
             "blockedBy": ["99"]},
        ]
        result = find_feature_task(tasks)
        assert result is None

    def test_returns_none_for_empty_list(self):
        from task_utils import find_feature_task
        result = find_feature_task([])
        assert result is None

    def test_skips_completed_tasks(self):
        from task_utils import find_feature_task
        tasks = [
            {"id": "1", "subject": "Old feature", "status": "completed"},
        ]
        result = find_feature_task(tasks)
        assert result is None

    def test_finds_pending_feature_task(self):
        from task_utils import find_feature_task
        tasks = [
            {"id": "1", "subject": "New feature", "status": "pending"},
        ]
        result = find_feature_task(tasks)
        assert result is not None
        assert result["id"] == "1"

    def test_skips_review_phase_tasks(self):
        from task_utils import find_feature_task
        tasks = [
            {"id": "1", "subject": "Review: auth PR", "status": "in_progress"},
        ]
        result = find_feature_task(tasks)
        assert result is None

    def test_handles_missing_id(self):
        from task_utils import find_feature_task
        tasks = [
            {"subject": "No ID task", "status": "in_progress"},
        ]
        result = find_feature_task(tasks)
        assert result is None


# ---------------------------------------------------------------------------
# find_current_phase
# ---------------------------------------------------------------------------

class TestFindCurrentPhase:
    """Tests for find_current_phase() — active phase detection."""

    def test_finds_active_phase(self):
        from task_utils import find_current_phase
        tasks = [
            {"id": "1", "subject": "Feature", "status": "in_progress"},
            {"id": "2", "subject": "PREPARE: feature", "status": "completed"},
            {"id": "3", "subject": "CODE: feature", "status": "in_progress"},
        ]
        result = find_current_phase(tasks)
        assert result is not None
        assert result["subject"] == "CODE: feature"

    def test_returns_none_when_no_active_phase(self):
        from task_utils import find_current_phase
        tasks = [
            {"id": "1", "subject": "PREPARE: feature", "status": "completed"},
            {"id": "2", "subject": "CODE: feature", "status": "pending"},
        ]
        result = find_current_phase(tasks)
        assert result is None

    def test_returns_none_for_empty_list(self):
        from task_utils import find_current_phase
        result = find_current_phase([])
        assert result is None

    def test_detects_all_phase_types(self):
        from task_utils import find_current_phase
        for phase in ["PREPARE:", "ARCHITECT:", "CODE:", "TEST:"]:
            tasks = [{"id": "1", "subject": f"{phase} feature", "status": "in_progress"}]
            result = find_current_phase(tasks)
            assert result is not None, f"Failed to detect {phase} phase"

    def test_ignores_non_phase_tasks(self):
        from task_utils import find_current_phase
        tasks = [
            {"id": "1", "subject": "Implement auth", "status": "in_progress"},
        ]
        result = find_current_phase(tasks)
        assert result is None


# ---------------------------------------------------------------------------
# find_active_agents
# ---------------------------------------------------------------------------

class TestFindActiveAgents:
    """Tests for find_active_agents() — agent task filtering."""

    def test_finds_active_agents(self):
        from task_utils import find_active_agents
        tasks = [
            {"id": "1", "subject": "backend-coder: implement auth",
             "status": "in_progress"},
            {"id": "2", "subject": "test-engineer: write tests",
             "status": "in_progress"},
        ]
        result = find_active_agents(tasks)
        assert len(result) == 2

    def test_excludes_completed_agents(self):
        from task_utils import find_active_agents
        tasks = [
            {"id": "1", "subject": "backend-coder: implement auth",
             "status": "completed"},
        ]
        result = find_active_agents(tasks)
        assert result == []

    def test_excludes_non_agent_tasks(self):
        from task_utils import find_active_agents
        tasks = [
            {"id": "1", "subject": "Feature task", "status": "in_progress"},
            {"id": "2", "subject": "CODE: feature", "status": "in_progress"},
        ]
        result = find_active_agents(tasks)
        assert result == []

    def test_returns_empty_for_empty_list(self):
        from task_utils import find_active_agents
        result = find_active_agents([])
        assert result == []

    def test_detects_all_agent_types(self):
        from task_utils import find_active_agents
        agent_types = [
            "preparer", "architect", "backend-coder",
            "frontend-coder", "database-engineer",
            "devops-engineer", "n8n", "test-engineer",
            "security-engineer", "qa-engineer", "secretary",
        ]
        tasks = [
            {"id": str(i), "subject": f"{agent}: task {i}",
             "status": "in_progress"}
            for i, agent in enumerate(agent_types)
        ]
        result = find_active_agents(tasks)
        assert len(result) == len(agent_types)

    def test_case_insensitive_matching(self):
        from task_utils import find_active_agents
        tasks = [
            {"id": "1", "subject": "Backend-Coder: task",
             "status": "in_progress"},
        ]
        result = find_active_agents(tasks)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# find_blockers
# ---------------------------------------------------------------------------

class TestFindBlockers:
    """Tests for find_blockers() — blocker/algedonic task detection."""

    def test_finds_blocker_tasks(self):
        from task_utils import find_blockers
        tasks = [
            {"id": "1", "subject": "BLOCKER: missing API",
             "status": "pending",
             "metadata": {"type": "blocker"}},
        ]
        result = find_blockers(tasks)
        assert len(result) == 1

    def test_finds_algedonic_tasks(self):
        from task_utils import find_blockers
        tasks = [
            {"id": "1", "subject": "HALT: security issue",
             "status": "pending",
             "metadata": {"type": "algedonic"}},
        ]
        result = find_blockers(tasks)
        assert len(result) == 1

    def test_excludes_completed_blockers(self):
        from task_utils import find_blockers
        tasks = [
            {"id": "1", "subject": "BLOCKER: resolved",
             "status": "completed",
             "metadata": {"type": "blocker"}},
        ]
        result = find_blockers(tasks)
        assert result == []

    def test_excludes_normal_tasks(self):
        from task_utils import find_blockers
        tasks = [
            {"id": "1", "subject": "Regular task", "status": "pending",
             "metadata": {}},
        ]
        result = find_blockers(tasks)
        assert result == []

    def test_handles_missing_metadata(self):
        from task_utils import find_blockers
        tasks = [
            {"id": "1", "subject": "Task without metadata", "status": "pending"},
        ]
        result = find_blockers(tasks)
        assert result == []

    def test_returns_empty_for_empty_list(self):
        from task_utils import find_blockers
        result = find_blockers([])
        assert result == []

    def test_multiple_blockers(self):
        from task_utils import find_blockers
        tasks = [
            {"id": "1", "subject": "BLOCKER: A", "status": "pending",
             "metadata": {"type": "blocker"}},
            {"id": "2", "subject": "HALT: B", "status": "in_progress",
             "metadata": {"type": "algedonic"}},
            {"id": "3", "subject": "BLOCKER: resolved", "status": "completed",
             "metadata": {"type": "blocker"}},
        ]
        result = find_blockers(tasks)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# read_task_json — M2 NUL-byte advisory-suppression DoS regression
# ---------------------------------------------------------------------------

class TestReadTaskJsonNulByteSafety:
    """M2 (security #38): read_task_json must NOT propagate a ValueError from the
    task-file stat (exists() raises 'embedded null byte' on a NUL-containing
    path). Uncaught, it propagates to a caller's catch-all — in the lifecycle
    gate it skips rule enforcement for the turn (advisory-suppression DoS). The
    fix catches ValueError and degrades to the fail-open {}.

    NOTE: exists()'s NUL behavior is Python-VERSION-DEPENDENT — CPython 3.14's
    exists() returns False on a NUL path (open()/read_text() is the raiser
    there), while other/platform Pythons raise ValueError from exists() itself.
    The monkeypatch test FORCES the raising behavior so the catch is exercised
    DETERMINISTICALLY regardless of the runner's Python; the plain-input test is
    version-tolerant (never-raises either way).
    """

    def test_value_error_from_stat_degrades_to_empty(self, tmp_path, monkeypatch):
        from task_utils import read_task_json
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Force the vulnerable-platform behavior, but CONDITIONALLY — raise only
        # for the target task file (path contains the sentinel task_id), leaving
        # pytest's own Path.exists() calls intact. A global always-raise breaks
        # the test runner itself; a clean RED-on-revert needs this narrow scope.
        _real_exists = Path.exists

        def _raise_for_task_file(self):
            if "m2-sentinel-task" in str(self):
                raise ValueError("embedded null byte")
            return _real_exists(self)

        monkeypatch.setattr(Path, "exists", _raise_for_task_file)
        result = read_task_json("m2-sentinel-task", "test-team")
        assert result == {}, (
            "a ValueError from the task-file stat must degrade to {} (fail-open) "
            "rather than propagate — else the lifecycle gate's rule enforcement "
            "is suppressed for the turn (advisory-suppression DoS)"
        )

    def test_nul_byte_task_id_never_raises(self, tmp_path, monkeypatch):
        # Version-tolerant smoke: on a Python whose exists() raises on NUL the
        # catch returns {}; on 3.14 (exists() returns False) the loop falls
        # through to {} naturally. Either way read_task_json never raises.
        from task_utils import read_task_json
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert read_task_json("12\x00bad", "test-team") == {}
        assert read_task_json("\x00", "team", tasks_base_dir=str(tmp_path)) == {}


# ---------------------------------------------------------------------------
# read_task_json — bounded containment parity (team_name guards 1+2)
# ---------------------------------------------------------------------------

class TestReadTaskJsonContainmentParity:
    """read_task_json gains the 2-of-5 sibling-reader guards that apply to a
    single-named-file reader: (1) is_safe_path_component(team_name) and (2)
    resolve()+relative_to(base) containment. The glob-result hygiene guards
    (per-file is_symlink / dotfile skip / isinstance(dict)) are correctly
    ABSENT — read_task_json reads ONE named file, it never globs.
    """

    def test_legit_team_dir_still_read(self, tmp_path):
        # Happy path preserved: a legit team_name + real dir is read unchanged.
        from task_utils import read_task_json
        base = tmp_path / "tasks"
        tdir = base / "pact-legit-team"
        tdir.mkdir(parents=True)
        (tdir / "5.json").write_text(
            json.dumps({"id": "5", "subject": "Real"}), encoding="utf-8"
        )
        result = read_task_json("5", "pact-legit-team", tasks_base_dir=str(base))
        assert result.get("subject") == "Real"

    def test_symlink_team_dir_escaping_base_rejected(self, tmp_path):
        # Guard (2): a safe-NAMED team dir that is a SYMLINK escaping the base
        # must be rejected by resolve()/relative_to -> falls through to bare
        # base -> {}. NON-VACUOUS: without the guard, exists() follows the
        # symlink and reads the planted out-of-base secret.
        from task_utils import read_task_json
        base = tmp_path / "tasks"
        base.mkdir(parents=True)
        outside = tmp_path / "escapedir"
        outside.mkdir()
        (outside / "7.json").write_text(
            json.dumps({"id": "7", "subject": "LEAKED"}), encoding="utf-8"
        )
        (base / "evil-team").symlink_to(outside, target_is_directory=True)
        result = read_task_json("7", "evil-team", tasks_base_dir=str(base))
        assert result == {}, (
            "a team dir that is a symlink escaping the base must be rejected by "
            "the resolve()/relative_to containment (without it, exists() follows "
            "the symlink and reads the out-of-base secret -> LEAKED)"
        )

    def test_unsafe_team_name_falls_through_to_bare_base(self, tmp_path):
        # Guard (1): an is_safe_path_component-failing team_name (here a slash
        # name that stays UNDER base, so guard (2) alone would NOT catch it)
        # must skip the team dir and fall through to the bare-base read.
        # NON-VACUOUS for guard (1) specifically: without it, base/"sub/evil" is
        # under base (guard 2 passes) and read_task_json reads NESTED.
        from task_utils import read_task_json
        base = tmp_path / "tasks"
        nested = base / "sub" / "evil"
        nested.mkdir(parents=True)
        (nested / "3.json").write_text(
            json.dumps({"id": "3", "subject": "NESTED"}), encoding="utf-8"
        )
        # bare base has no 3.json -> guard (1) skip -> fall-through yields {}
        result = read_task_json("3", "sub/evil", tasks_base_dir=str(base))
        assert result == {}, (
            "an unsafe (multi-segment) team_name must be rejected by "
            "is_safe_path_component and skip the team dir -> bare-base fall-through"
        )

    def test_mixed_case_team_name_resolves_under_lowercased_dir(self, tmp_path):
        # Case-fold convergence: read_task_json lowercases team_name BEFORE the
        # safe-path check + path join, so a mixed-case team_name resolves the
        # task under the LOWERCASED team dir — matching the marker SSOT
        # (agent_handoff_marker._resolve_marker_target), which also lowercases.
        # The on-disk dir is the lowercased name; the lookup uses the mixed-case
        # form and must still find the task.
        #
        # NON-VACUITY: without the .lower() the lookup targets the mixed-case dir
        # name, which does not exist on a case-sensitive filesystem -> the team
        # dir is skipped -> fall-through to the bare base (no task file there) ->
        # {}. Reverting the case-fold flips this RED. (Verified by revert.)
        #
        # CASE-SENSITIVITY GUARD: on a case-INSENSITIVE filesystem (macOS APFS
        # default), the OS itself bridges "Session-..." -> "session-..." on disk,
        # so the regression is unobservable there (the test would false-green
        # even with the .lower() removed). Skip unless the tmp filesystem is
        # genuinely case-sensitive, so the assertion is coupled to the case-fold
        # rather than to OS path semantics. Linux CI is case-sensitive.
        from task_utils import read_task_json
        probe_lower = tmp_path / "casefs_probe"
        probe_lower.mkdir()
        if (tmp_path / "CASEFS_PROBE").exists():
            pytest.skip(
                "case-insensitive filesystem (e.g. macOS APFS) cannot observe "
                "the case-fold regression — the OS bridges the case difference"
            )
        base = tmp_path / "tasks"
        lowered_dir = base / "session-deadbeef"
        lowered_dir.mkdir(parents=True)
        (lowered_dir / "5.json").write_text(
            json.dumps({"id": "5", "subject": "CaseFolded"}), encoding="utf-8"
        )
        result = read_task_json(
            "5", "Session-DEADBEEF", tasks_base_dir=str(base)
        )
        assert result.get("subject") == "CaseFolded", (
            "a mixed-case team_name must resolve under the lowercased team dir "
            "(read_task_json .lower()s before the join, converging with the "
            "marker SSOT). Without the case-fold the lookup targets the "
            "mixed-case dir name, misses on a case-sensitive FS, and falls "
            "through to the bare base -> {}."
        )


class TestReadTaskJsonResolverEnvSet:
    """#926 remediation (was F2/Future, folded in): read_task_json's
    tasks_base_dir=None resolver-derivation must resolve under a non-default
    CLAUDE_CONFIG_DIR (get_claude_config_dir()/"tasks"). The containment-parity
    tests inject tasks_base_dir= (correct -- they test the defense); the existing
    no-base tests run env-UNSET. This covers the env-SET resolver path.

    NON-VACUITY: Path.home is redirected to a SEPARATE EMPTY dir, so a revert of
    the resolver call (back to Path.home()/".claude"/"tasks") reads the empty home
    -> file absent -> {} -> the positive assertion FAILS behaviorally.
    """

    def test_resolves_task_under_relocated_config(self, tmp_path, monkeypatch):
        from task_utils import read_task_json

        config_dir = tmp_path / "config"
        team_dir = config_dir / "tasks" / "pact-relocated"
        team_dir.mkdir(parents=True)
        (team_dir / "7.json").write_text(
            json.dumps({"id": "7", "subject": "relocated task"}), encoding="utf-8")
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))

        # tasks_base_dir=None -> exercises get_claude_config_dir()/"tasks".
        assert read_task_json("7", "pact-relocated").get("subject") == "relocated task"

    def test_absent_task_under_relocated_config_returns_empty(self, tmp_path, monkeypatch):
        # Complement so the positive isn't trivially always-truthy.
        from task_utils import read_task_json

        config_dir = tmp_path / "config"
        (config_dir / "tasks" / "pact-relocated").mkdir(parents=True)
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))

        assert read_task_json("999", "pact-relocated") == {}
