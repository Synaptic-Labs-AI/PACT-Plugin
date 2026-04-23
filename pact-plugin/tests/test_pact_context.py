"""
Tests for shared/pact_context.py — session context module.

Tests cover:

get_pact_context():
1. Returns all fields from a valid context file
2. Returns empty strings when file is missing
3. Returns empty strings when file contains invalid JSON
4. Caches result on second call (module-level _cache)
5. Returns empty strings when file has missing keys

get_team_name():
6. Returns lowercased team_name
7. Returns empty string when context file is missing

get_session_id():
8. Returns session_id from context file
9. Returns empty string when context file is missing

get_project_dir():
10. Returns project_dir from context file

resolve_agent_name():
11. Returns agent_name from input_data when present
12. Splits agent_id on @ and returns name part (step 2)
13. Handles @ split edge cases: @ at start, multiple @, no @ (step 2 edges)
14. Resolves agent_id via team config lookup
15. Falls back to agent_type with pact- prefix stripped
16. Returns empty string when no identity info available
17. Falls back to agent_type when team config missing
18. Falls back to agent_type when agent_id not in members
19. Returns agent_type as-is when it doesn't start with pact-
20. Handles non-list members gracefully

write_context():
21. Creates context file with correct content
22. Uses atomic write (temp file + rename)
23. Sets file permissions to 0o600
24. Creates parent directory if missing
25. Handles write errors gracefully (fail-open)
25b. Skips write when session_id and project_dir are empty

--- Extended Coverage (Test Engineer) ---

get_pact_context() edge cases:
26. Returns empty strings when file is read-protected (+ logs to stderr)
27. Returns empty strings when JSON is a non-dict type (e.g., list)
28. Coerces non-string values to strings
29. Caches error result (no repeated file reads on error)
30. Returns empty strings for empty JSON object

get_team_name() edge cases:
31. Does not transform other accessors (only team_name lowercased)

resolve_agent_name() edge cases:
32. Skips empty string agent_name (falsy)
33. Falls back when agent_id present but no team_name available
34. Handles corrupt team config JSON gracefully
35. Uses default team_name from context file when not provided
36. Handles agent_id without agent_type (no fallback after failed lookup)
37. Prefers agent_name over agent_id even when both present

write_context() edge cases:
38. Writes valid ISO 8601 timestamp in started_at
39. Cleans up temp file on write failure

Integration:
40. write_context → get_pact_context round-trip
41. write_context → get_team_name/get_session_id/get_project_dir round-trip

Category C fallback (memory scripts — shared pact_session module):
42. get_session_id_from_context_file returns session_id from session-scoped context file
43. get_session_id_from_context_file returns empty string when file missing
43b. get_session_id_from_context_file returns empty string with no args
44. get_session_id_from_context_file returns empty string on invalid JSON
45. _detect_session_id returns None without context (no env fallback)
46. _get_embedding_attempted_path falls back to 'unknown' without context

init():
47. Second init() call is a no-op (idempotency guard)
48. Missing session_id leaves _context_path as None (readers return empty context)
48b. session_id present but CLAUDE_PROJECT_DIR absent leaves _context_path as None

Additional write_context():
49. Overwrites existing file
50. No temp files left on success

Additional resolve_agent_name():
51. agent_id found in config skips agent_type check
52. member with missing name field returns empty string

Additional Category C:
53. memory_api imports get_session_id_from_context_file from pact_session
54. memory_init imports get_session_id_from_context_file from pact_session
55. _get_embedding_attempted_path falls back to "unknown" when no context file

Additional write_context():
56. Creates deeply nested parent directories

Concurrent:
57. Concurrent writes produce valid JSON

Uninitialized accessors:
57b. get_pact_context() returns _EMPTY_CONTEXT when _context_path is None
57c. write_context() computes session-scoped path when _context_path is None

Migration completeness (AST-based scanner — os.environ.get, os.getenv, os.environ[]):
58-60. No hook runtime code reads phantom env vars (parametrized x3)
61-63. No skill script runtime code reads phantom env vars (parametrized x3)

--- Fresh Review Tests ---

init() ordering:
64. get_team_name() before init() returns empty; after init() reads session-scoped

Session-scoped path E2E:
65. write_context → init → get_team_name full session-scoped cycle
66. Session-scoped path uses Path(project_dir).name as slug

pact_session.py path:
67. _context_file_path returns session-scoped path when both args provided
68. _context_file_path returns None when args missing

init()-before-reader ordering guard:
69. (parametrized) Every hook that calls a pact_context reader calls init() first
70. session_end.py: init() before get_project_slug() (indirect get_project_dir() call)
73. teachback_check.py: init() before should_warn() (indirect get_session_dir())

Library module init() contract:
71. task_utils.get_task_list() works when init() was called by a prior hook
72. checkpoint_builder.get_session_id() works when init() was called by a prior hook
"""

import json
import os
import stat
import sys
from pathlib import Path

import pytest

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


class TestGetPactContext:
    """Tests for get_pact_context() — read and cache context file."""

    def test_returns_all_fields(self, pact_context):
        """Should return all fields from a valid context file."""
        from shared.pact_context import get_pact_context

        pact_context(
            team_name="pact-abc12345",
            session_id="abc12345-0000-1111-2222-333344445555",
            project_dir="/Users/test/project",
        )

        result = get_pact_context()

        assert result["team_name"] == "pact-abc12345"
        assert result["session_id"] == "abc12345-0000-1111-2222-333344445555"
        assert result["project_dir"] == "/Users/test/project"
        assert result["started_at"] == "2026-01-01T00:00:00Z"

    def test_returns_empty_strings_when_file_missing(self, monkeypatch, tmp_path):
        """Should return empty strings for all keys when context file is missing."""
        import shared.pact_context as ctx_module

        monkeypatch.setattr(ctx_module, "_context_path", tmp_path / "nonexistent.json")
        monkeypatch.setattr(ctx_module, "_cache", None)

        result = ctx_module.get_pact_context()

        assert result["team_name"] == ""
        assert result["session_id"] == ""
        assert result["project_dir"] == ""
        assert result["started_at"] == ""

    def test_returns_empty_strings_on_invalid_json(self, monkeypatch, tmp_path):
        """Should return empty strings when context file contains invalid JSON."""
        import shared.pact_context as ctx_module

        bad_file = tmp_path / "bad-context.json"
        bad_file.write_text("{not valid json!!!", encoding="utf-8")
        monkeypatch.setattr(ctx_module, "_context_path", bad_file)
        monkeypatch.setattr(ctx_module, "_cache", None)

        result = ctx_module.get_pact_context()

        assert result["team_name"] == ""
        assert result["session_id"] == ""

    def test_caches_result(self, pact_context, monkeypatch):
        """Should cache the result after first read (module-level _cache)."""
        import shared.pact_context as ctx_module

        ctx_file = pact_context(team_name="cached-team")

        # First call populates cache
        result1 = ctx_module.get_pact_context()
        assert result1["team_name"] == "cached-team"

        # Modify the file — should NOT affect cached result
        ctx_file.write_text(json.dumps({
            "team_name": "modified-team",
            "session_id": "",
            "project_dir": "",
            "started_at": "",
        }), encoding="utf-8")

        result2 = ctx_module.get_pact_context()
        assert result2["team_name"] == "cached-team"  # Still cached

    def test_handles_missing_keys(self, monkeypatch, tmp_path):
        """Should return empty strings for missing keys in the context file."""
        import shared.pact_context as ctx_module

        partial_file = tmp_path / "partial-context.json"
        partial_file.write_text(json.dumps({"team_name": "only-this"}), encoding="utf-8")
        monkeypatch.setattr(ctx_module, "_context_path", partial_file)
        monkeypatch.setattr(ctx_module, "_cache", None)

        result = ctx_module.get_pact_context()

        assert result["team_name"] == "only-this"
        assert result["session_id"] == ""
        assert result["project_dir"] == ""
        assert result["started_at"] == ""


class TestGetTeamName:
    """Tests for get_team_name() — convenience accessor."""

    def test_returns_lowercased_team_name(self, pact_context):
        """Should return team_name from context, lowercased."""
        from shared.pact_context import get_team_name

        pact_context(team_name="PACT-ABC12345")

        result = get_team_name()

        assert result == "pact-abc12345"

    def test_returns_empty_on_missing_file(self, monkeypatch, tmp_path):
        """Should return empty string when context file is missing."""
        import shared.pact_context as ctx_module

        monkeypatch.setattr(ctx_module, "_context_path", tmp_path / "missing.json")
        monkeypatch.setattr(ctx_module, "_cache", None)

        result = ctx_module.get_team_name()

        assert result == ""


class TestGetSessionId:
    """Tests for get_session_id() — convenience accessor."""

    def test_returns_session_id(self, pact_context):
        """Should return session_id from context file."""
        from shared.pact_context import get_session_id

        pact_context(session_id="test-session-xyz")

        assert get_session_id() == "test-session-xyz"

    def test_returns_empty_on_missing_file(self, monkeypatch, tmp_path):
        """Should return empty string when context file is missing."""
        import shared.pact_context as ctx_module

        monkeypatch.setattr(ctx_module, "_context_path", tmp_path / "missing.json")
        monkeypatch.setattr(ctx_module, "_cache", None)

        assert ctx_module.get_session_id() == ""


class TestGetProjectDir:
    """Tests for get_project_dir() — convenience accessor."""

    def test_returns_project_dir(self, pact_context):
        """Should return project_dir from context file."""
        from shared.pact_context import get_project_dir

        pact_context(project_dir="/Users/test/my-project")

        assert get_project_dir() == "/Users/test/my-project"


class TestGetPluginRoot:
    """Tests for get_plugin_root() — convenience accessor for installed plugin path."""

    def test_returns_plugin_root_from_context(self, pact_context):
        """Should return plugin_root when set in context file."""
        from shared.pact_context import get_plugin_root

        pact_context(plugin_root="/Users/me/.claude/plugins/cache/PACT/3.17.0")

        assert get_plugin_root() == "/Users/me/.claude/plugins/cache/PACT/3.17.0"

    def test_returns_empty_when_plugin_root_missing(self, pact_context):
        """Should return empty string when plugin_root is not in context."""
        from shared.pact_context import get_plugin_root

        pact_context()  # plugin_root defaults to ""

        assert get_plugin_root() == ""


class TestGetSessionDir:
    """Tests for get_session_dir() — session-scoped directory path."""

    def test_returns_session_dir_path(self, pact_context):
        """Should construct ~/.claude/pact-sessions/{slug}/{session_id}/ path."""
        from shared.pact_context import get_session_dir

        pact_context(
            session_id="abc-123-def",
            project_dir="/Users/test/my-project",
        )

        result = get_session_dir()
        assert result.endswith("pact-sessions/my-project/abc-123-def")
        assert ".claude" in result

    def test_returns_empty_when_no_session_id(self, pact_context):
        """Should return '' when session_id is unavailable."""
        from shared.pact_context import get_session_dir

        pact_context(session_id="", project_dir="/Users/test/my-project")

        assert get_session_dir() == ""

    def test_returns_empty_when_no_project_dir(self, pact_context):
        """Should return '' when project_dir is unavailable."""
        from shared.pact_context import get_session_dir

        pact_context(session_id="abc-123", project_dir="")

        assert get_session_dir() == ""

    def test_returns_empty_when_both_missing(self, pact_context):
        """Should return '' when both session_id and project_dir are missing."""
        from shared.pact_context import get_session_dir

        pact_context(session_id="", project_dir="")

        assert get_session_dir() == ""


class TestInit:
    """Tests for init() — session-scoped path initialization."""

    def test_init_idempotency(self, monkeypatch, tmp_path):
        """Second init() call should be a no-op — _context_path keeps first value."""
        import shared.pact_context as ctx_module

        # Reset _context_path so init() actually runs
        monkeypatch.setattr(ctx_module, "_context_path", None)
        monkeypatch.setattr(ctx_module, "_cache", None)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/project/a")

        ctx_module.init({"session_id": "aaa"})
        first_path = ctx_module._context_path

        # Second call with different data — should be ignored
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/project/b")
        ctx_module.init({"session_id": "bbb"})
        second_path = ctx_module._context_path

        assert first_path == second_path
        assert "aaa" in str(first_path)
        assert "bbb" not in str(second_path)

    def test_init_missing_session_id_leaves_path_none(self, monkeypatch):
        """init() with no session_id should leave _context_path as None.

        When session_id or project_dir is unavailable, init() does NOT set
        _context_path. Readers return _EMPTY_CONTEXT without file I/O when
        _context_path is None.
        """
        import shared.pact_context as ctx_module

        monkeypatch.setattr(ctx_module, "_context_path", None)
        monkeypatch.setattr(ctx_module, "_cache", None)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

        ctx_module.init({})  # No session_id key

        assert ctx_module._context_path is None

    def test_init_session_id_without_project_dir_leaves_path_none(self, monkeypatch):
        """init() with session_id but no CLAUDE_PROJECT_DIR should leave _context_path as None."""
        import shared.pact_context as ctx_module

        monkeypatch.setattr(ctx_module, "_context_path", None)
        monkeypatch.setattr(ctx_module, "_cache", None)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

        ctx_module.init({"session_id": "has-session-but-no-project"})

        assert ctx_module._context_path is None


class TestResolveAgentName:
    """Tests for resolve_agent_name() — agent identity resolution chain."""

    def test_returns_agent_name_when_present(self):
        """Step 1: Should return agent_name directly from input_data."""
        from shared.pact_context import resolve_agent_name

        result = resolve_agent_name({"agent_name": "backend-coder"})

        assert result == "backend-coder"

    def test_resolve_agent_name_at_split(self):
        """Step 2: Should split agent_id on @ and return the name part."""
        from shared.pact_context import resolve_agent_name

        result = resolve_agent_name({"agent_id": "backend-coder@pact-team"})

        assert result == "backend-coder"

    @pytest.mark.parametrize("agent_id, expected", [
        ("@team", ""),                   # @ at start → empty name part
        ("name@team@extra", "name"),     # multiple @ → takes before first @
        ("plain-id", None),              # no @ → falls through to step 3/4/5
    ])
    def test_resolve_agent_name_at_split_edge_cases(self, agent_id, expected):
        """Step 2 edge cases: @ at start, multiple @, no @ (falls through)."""
        from shared.pact_context import resolve_agent_name

        if expected is not None:
            # Cases where @ split produces a result (even empty string)
            result = resolve_agent_name({"agent_id": agent_id})
            assert result == expected
        else:
            # "plain-id" has no @ → skips step 2, no team config → no agent_type
            # → returns "" from step 5
            result = resolve_agent_name({"agent_id": agent_id})
            assert result == ""

    def test_resolves_agent_id_via_team_config(self, tmp_path):
        """Step 3: Should look up agent_id in team config members."""
        from shared.pact_context import resolve_agent_name

        # Create team config with members
        team_dir = tmp_path / "pact-test1234"
        team_dir.mkdir(parents=True)
        config = {
            "members": [
                {"id": "agent-uuid-123", "name": "backend-coder"},
                {"id": "agent-uuid-456", "name": "test-engineer"},
            ]
        }
        (team_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

        result = resolve_agent_name(
            {"agent_id": "agent-uuid-123"},
            team_name="pact-test1234",
            teams_dir=str(tmp_path),
        )

        assert result == "backend-coder"

    def test_falls_back_to_agent_type(self):
        """Step 3: Should strip pact- prefix from agent_type."""
        from shared.pact_context import resolve_agent_name

        result = resolve_agent_name({"agent_type": "pact-backend-coder"})

        assert result == "backend-coder"

    def test_returns_empty_for_no_identity(self):
        """Step 4: Should return empty string when no identity info available."""
        from shared.pact_context import resolve_agent_name

        result = resolve_agent_name({})

        assert result == ""

    def test_falls_back_to_type_when_config_missing(self, tmp_path):
        """Should fall back to agent_type when team config file is missing."""
        from shared.pact_context import resolve_agent_name

        result = resolve_agent_name(
            {"agent_id": "agent-uuid-123", "agent_type": "pact-test-engineer"},
            team_name="nonexistent-team",
            teams_dir=str(tmp_path),
        )

        assert result == "test-engineer"

    def test_falls_back_to_type_when_id_not_in_members(self, tmp_path):
        """Should fall back to agent_type when agent_id is not found in members."""
        from shared.pact_context import resolve_agent_name

        team_dir = tmp_path / "pact-test1234"
        team_dir.mkdir(parents=True)
        config = {"members": [{"id": "other-uuid", "name": "other-agent"}]}
        (team_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

        result = resolve_agent_name(
            {"agent_id": "not-found-uuid", "agent_type": "pact-frontend-coder"},
            team_name="pact-test1234",
            teams_dir=str(tmp_path),
        )

        assert result == "frontend-coder"

    def test_agent_type_without_pact_prefix(self):
        """Should return agent_type as-is when it doesn't start with pact-."""
        from shared.pact_context import resolve_agent_name

        result = resolve_agent_name({"agent_type": "custom-agent"})

        assert result == "custom-agent"

    def test_handles_non_list_members(self, tmp_path):
        """Should handle non-list members array gracefully."""
        from shared.pact_context import resolve_agent_name

        team_dir = tmp_path / "pact-test1234"
        team_dir.mkdir(parents=True)
        config = {"members": "not-a-list"}
        (team_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

        result = resolve_agent_name(
            {"agent_id": "some-uuid", "agent_type": "pact-backend-coder"},
            team_name="pact-test1234",
            teams_dir=str(tmp_path),
        )

        assert result == "backend-coder"


class TestWriteContext:
    """Tests for write_context() — atomic context file writer."""

    def test_creates_context_file_with_correct_content(self, monkeypatch, tmp_path):
        """Should create context file with all required fields."""
        import shared.pact_context as ctx_module

        ctx_file = tmp_path / "pact-session-context.json"
        monkeypatch.setattr(ctx_module, "_context_path", ctx_file)

        ctx_module.write_context(
            team_name="pact-abc12345",
            session_id="abc12345-0000-1111-2222-333344445555",
            project_dir="/Users/test/project",
        )

        assert ctx_file.exists()
        data = json.loads(ctx_file.read_text(encoding="utf-8"))
        assert data["team_name"] == "pact-abc12345"
        assert data["session_id"] == "abc12345-0000-1111-2222-333344445555"
        assert data["project_dir"] == "/Users/test/project"
        assert "started_at" in data  # ISO timestamp, not empty

    def test_writes_plugin_root_when_provided(self, monkeypatch, tmp_path):
        """Should include plugin_root in context file when passed."""
        import shared.pact_context as ctx_module

        ctx_file = tmp_path / "pact-session-context.json"
        monkeypatch.setattr(ctx_module, "_context_path", ctx_file)
        monkeypatch.setattr(ctx_module, "_cache", None)

        ctx_module.write_context(
            team_name="pact-pr1",
            session_id="pr1-session",
            project_dir="/test/proj",
            plugin_root="/Users/me/.claude/plugins/cache/PACT/3.17.0",
        )

        data = json.loads(ctx_file.read_text(encoding="utf-8"))
        assert data["plugin_root"] == "/Users/me/.claude/plugins/cache/PACT/3.17.0"

    def test_plugin_root_defaults_to_empty(self, monkeypatch, tmp_path):
        """Should write empty plugin_root when not provided."""
        import shared.pact_context as ctx_module

        ctx_file = tmp_path / "pact-session-context.json"
        monkeypatch.setattr(ctx_module, "_context_path", ctx_file)
        monkeypatch.setattr(ctx_module, "_cache", None)

        ctx_module.write_context(
            team_name="pact-pr2",
            session_id="pr2-session",
            project_dir="/test/proj",
        )

        data = json.loads(ctx_file.read_text(encoding="utf-8"))
        assert data["plugin_root"] == ""

    def test_sets_file_permissions_0600(self, monkeypatch, tmp_path):
        """Should set file permissions to 0o600 (user-only read/write)."""
        import shared.pact_context as ctx_module

        ctx_file = tmp_path / "pact-session-context.json"
        monkeypatch.setattr(ctx_module, "_context_path", ctx_file)

        ctx_module.write_context(
            team_name="test-team",
            session_id="test-session",
            project_dir="/test",
        )

        file_mode = oct(ctx_file.stat().st_mode & 0o777)
        assert file_mode == "0o600"

    def test_creates_parent_directory(self, monkeypatch, tmp_path):
        """Should create parent directory if it doesn't exist."""
        import shared.pact_context as ctx_module

        ctx_file = tmp_path / "new-dir" / "pact-session-context.json"
        monkeypatch.setattr(ctx_module, "_context_path", ctx_file)

        ctx_module.write_context(
            team_name="test-team",
            session_id="test-session",
            project_dir="/test",
        )

        assert ctx_file.exists()

    def test_overwrites_existing_file(self, monkeypatch, tmp_path):
        """Should overwrite an existing context file."""
        import shared.pact_context as ctx_module

        ctx_file = tmp_path / "pact-session-context.json"
        ctx_file.write_text(json.dumps({"team_name": "old-team"}), encoding="utf-8")
        monkeypatch.setattr(ctx_module, "_context_path", ctx_file)

        ctx_module.write_context(
            team_name="new-team",
            session_id="new-session",
            project_dir="/new",
        )

        data = json.loads(ctx_file.read_text(encoding="utf-8"))
        assert data["team_name"] == "new-team"

    def test_skip_write_when_all_args_empty(self, monkeypatch, capsys):
        """Should skip writing and log warning when session_id and project_dir are empty."""
        import shared.pact_context as ctx_module

        # Reset _context_path so the empty-args branch is reached
        monkeypatch.setattr(ctx_module, "_context_path", None)
        monkeypatch.setattr(ctx_module, "_cache", None)

        ctx_module.write_context(
            team_name="",
            session_id="",
            project_dir="",
        )

        # No file should be written — _context_path should stay None
        assert ctx_module._context_path is None

        # Warning logged to stderr
        captured = capsys.readouterr()
        assert "skipping write" in captured.err

    def test_handles_write_error_gracefully(self, monkeypatch, tmp_path, capsys):
        """Should log to stderr and not raise on write errors."""
        import shared.pact_context as ctx_module

        # Point to a path where we can't write (non-existent parent with no perms)
        ctx_file = tmp_path / "readonly" / "pact-session-context.json"
        (tmp_path / "readonly").mkdir()
        (tmp_path / "readonly").chmod(0o000)
        monkeypatch.setattr(ctx_module, "_context_path", ctx_file)

        # Should not raise
        ctx_module.write_context(
            team_name="test-team",
            session_id="test-session",
            project_dir="/test",
        )

        # Restore permissions for cleanup
        (tmp_path / "readonly").chmod(0o755)

        # Should have logged to stderr
        captured = capsys.readouterr()
        assert "could not write context file" in captured.err

    def test_no_temp_files_left_on_success(self, monkeypatch, tmp_path):
        """Should not leave temp files after successful write."""
        import shared.pact_context as ctx_module

        ctx_file = tmp_path / "pact-session-context.json"
        monkeypatch.setattr(ctx_module, "_context_path", ctx_file)

        ctx_module.write_context(
            team_name="test-team",
            session_id="test-session",
            project_dir="/test",
        )

        # Only the context file should exist, no .tmp files
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name == "pact-session-context.json"


# =============================================================================
# Extended Coverage — Edge Cases and Integration (Test Engineer)
# =============================================================================


class TestGetPactContextEdgeCases:
    """Additional edge case tests for get_pact_context()."""

    def test_returns_empty_on_read_protected_file(self, monkeypatch, tmp_path, capsys):
        """Should return empty strings when context file has no read permission."""
        import shared.pact_context as ctx_module

        ctx_file = tmp_path / "pact-session-context.json"
        ctx_file.write_text(json.dumps({"team_name": "secret"}), encoding="utf-8")
        ctx_file.chmod(0o000)
        monkeypatch.setattr(ctx_module, "_context_path", ctx_file)
        monkeypatch.setattr(ctx_module, "_cache", None)

        result = ctx_module.get_pact_context()

        # Restore permissions for cleanup
        ctx_file.chmod(0o644)

        assert result["team_name"] == ""
        assert result["session_id"] == ""
        captured = capsys.readouterr()
        assert "could not read context file" in captured.err

    def test_returns_empty_on_non_dict_json(self, monkeypatch, tmp_path):
        """Should return empty strings when JSON is a list or other non-dict type."""
        import shared.pact_context as ctx_module

        ctx_file = tmp_path / "pact-session-context.json"
        ctx_file.write_text("[1, 2, 3]", encoding="utf-8")
        monkeypatch.setattr(ctx_module, "_context_path", ctx_file)
        monkeypatch.setattr(ctx_module, "_cache", None)

        result = ctx_module.get_pact_context()

        assert result["team_name"] == ""
        assert result["session_id"] == ""

    def test_coerces_non_string_values(self, monkeypatch, tmp_path):
        """Should coerce non-string values (int, bool, None) to strings."""
        import shared.pact_context as ctx_module

        ctx_file = tmp_path / "pact-session-context.json"
        ctx_file.write_text(json.dumps({
            "team_name": 12345,
            "session_id": True,
            "project_dir": None,
            "started_at": 99.9,
        }), encoding="utf-8")
        monkeypatch.setattr(ctx_module, "_context_path", ctx_file)
        monkeypatch.setattr(ctx_module, "_cache", None)

        result = ctx_module.get_pact_context()

        assert result["team_name"] == "12345"
        assert result["session_id"] == "True"
        assert result["project_dir"] == "None"
        assert result["started_at"] == "99.9"

    def test_caches_error_result(self, monkeypatch, tmp_path):
        """Should cache the error result so repeated calls don't re-read the file."""
        import shared.pact_context as ctx_module

        monkeypatch.setattr(ctx_module, "_context_path", tmp_path / "missing.json")
        monkeypatch.setattr(ctx_module, "_cache", None)

        result1 = ctx_module.get_pact_context()
        assert result1["team_name"] == ""

        # Now create the file — cache should prevent re-read
        (tmp_path / "missing.json").write_text(
            json.dumps({"team_name": "late-arrival"}), encoding="utf-8"
        )

        result2 = ctx_module.get_pact_context()
        assert result2["team_name"] == ""  # Still cached empty

    def test_returns_empty_for_empty_json_object(self, monkeypatch, tmp_path):
        """Should return empty strings for all keys when JSON is {}."""
        import shared.pact_context as ctx_module

        ctx_file = tmp_path / "pact-session-context.json"
        ctx_file.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(ctx_module, "_context_path", ctx_file)
        monkeypatch.setattr(ctx_module, "_cache", None)

        result = ctx_module.get_pact_context()

        assert result["team_name"] == ""
        assert result["session_id"] == ""
        assert result["project_dir"] == ""
        assert result["started_at"] == ""


class TestGetTeamNameEdgeCases:
    """Additional edge case tests for get_team_name()."""

    def test_only_team_name_is_lowercased(self, pact_context):
        """get_team_name() lowercases, but get_session_id() preserves case."""
        import shared.pact_context as ctx_module

        pact_context(
            team_name="PACT-UPPER",
            session_id="SESSION-UPPER",
            project_dir="/UPPER/PATH",
        )

        assert ctx_module.get_team_name() == "pact-upper"
        assert ctx_module.get_session_id() == "SESSION-UPPER"
        assert ctx_module.get_project_dir() == "/UPPER/PATH"


class TestResolveAgentNameEdgeCases:
    """Additional edge case tests for resolve_agent_name()."""

    def test_skips_empty_string_agent_name(self):
        """Should skip agent_name if it's an empty string (falsy)."""
        from shared.pact_context import resolve_agent_name

        result = resolve_agent_name({
            "agent_name": "",
            "agent_type": "pact-backend-coder",
        })

        # Empty string is falsy → falls through to agent_type
        assert result == "backend-coder"

    def test_agent_id_without_team_name_or_type(self, monkeypatch, tmp_path):
        """Should return empty string when agent_id present but no team and no agent_type."""
        import shared.pact_context as ctx_module

        # Ensure get_team_name() returns empty (no context file)
        monkeypatch.setattr(ctx_module, "_context_path", tmp_path / "missing.json")
        monkeypatch.setattr(ctx_module, "_cache", None)

        result = ctx_module.resolve_agent_name(
            {"agent_id": "some-uuid"},
            # No team_name override, no agent_type fallback
        )

        assert result == ""

    def test_handles_corrupt_team_config_json(self, tmp_path):
        """Should fall through gracefully when team config is corrupt JSON."""
        from shared.pact_context import resolve_agent_name

        team_dir = tmp_path / "pact-test1234"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text("not json!", encoding="utf-8")

        result = resolve_agent_name(
            {"agent_id": "some-uuid", "agent_type": "pact-architect"},
            team_name="pact-test1234",
            teams_dir=str(tmp_path),
        )

        assert result == "architect"

    def test_uses_context_file_team_name_by_default(self, pact_context, tmp_path):
        """Should read team_name from context file when not explicitly provided."""
        from shared.pact_context import resolve_agent_name

        pact_context(team_name="pact-from-context")

        # Create team config at the real home path (using teams_dir override for testing)
        team_dir = tmp_path / "teams" / "pact-from-context"
        team_dir.mkdir(parents=True)
        config = {"members": [{"id": "uuid-abc", "name": "my-agent"}]}
        (team_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

        result = resolve_agent_name(
            {"agent_id": "uuid-abc"},
            # team_name not provided → reads from context file
            teams_dir=str(tmp_path / "teams"),
        )

        assert result == "my-agent"

    def test_prefers_agent_name_over_agent_id(self, tmp_path):
        """Should use agent_name directly, not look up agent_id."""
        from shared.pact_context import resolve_agent_name

        # Create team config — should NOT be consulted
        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        config = {"members": [{"id": "uuid-123", "name": "looked-up-name"}]}
        (team_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

        result = resolve_agent_name(
            {"agent_name": "direct-name", "agent_id": "uuid-123"},
            team_name="pact-test",
            teams_dir=str(tmp_path),
        )

        assert result == "direct-name"

    def test_agent_id_found_returns_without_checking_type(self, tmp_path):
        """When agent_id resolves successfully, agent_type is not used."""
        from shared.pact_context import resolve_agent_name

        team_dir = tmp_path / "pact-team"
        team_dir.mkdir(parents=True)
        config = {"members": [{"id": "uuid-found", "name": "resolved-name"}]}
        (team_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

        result = resolve_agent_name(
            {
                "agent_id": "uuid-found",
                "agent_type": "pact-should-not-use",
            },
            team_name="pact-team",
            teams_dir=str(tmp_path),
        )

        assert result == "resolved-name"

    def test_member_with_missing_name_returns_empty(self, tmp_path):
        """Should return empty string from lookup when member has no name field."""
        from shared.pact_context import resolve_agent_name

        team_dir = tmp_path / "pact-team"
        team_dir.mkdir(parents=True)
        config = {"members": [{"id": "uuid-no-name"}]}  # No "name" key
        (team_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

        result = resolve_agent_name(
            {"agent_id": "uuid-no-name"},
            team_name="pact-team",
            teams_dir=str(tmp_path),
        )

        # member.get("name", "") → "" → falsy → fall through
        assert result == ""


class TestWriteContextEdgeCases:
    """Additional edge case tests for write_context()."""

    def test_started_at_is_valid_iso_timestamp(self, monkeypatch, tmp_path):
        """Should write a valid ISO 8601 timestamp in started_at."""
        import shared.pact_context as ctx_module
        from datetime import datetime

        ctx_file = tmp_path / "pact-session-context.json"
        monkeypatch.setattr(ctx_module, "_context_path", ctx_file)

        ctx_module.write_context(
            team_name="test-team",
            session_id="test-session",
            project_dir="/test",
        )

        data = json.loads(ctx_file.read_text(encoding="utf-8"))
        # Should parse without error as ISO 8601
        parsed = datetime.fromisoformat(data["started_at"])
        assert parsed.year >= 2026

    def test_temp_file_cleaned_up_on_write_failure(self, monkeypatch, tmp_path, capsys):
        """Should not leave temp files after a write failure."""
        import shared.pact_context as ctx_module

        ctx_file = tmp_path / "pact-session-context.json"
        monkeypatch.setattr(ctx_module, "_context_path", ctx_file)

        # Mock os.rename to fail after temp file is created
        original_rename = os.rename

        def failing_rename(src, dst):
            raise OSError("simulated rename failure")

        monkeypatch.setattr(os, "rename", failing_rename)

        ctx_module.write_context(
            team_name="test",
            session_id="test",
            project_dir="/test",
        )

        # No temp files should remain
        tmp_files = [f for f in tmp_path.iterdir() if ".tmp" in f.name or f.name.startswith(".pact-session-context-")]
        assert len(tmp_files) == 0

        captured = capsys.readouterr()
        assert "could not write context file" in captured.err


class TestWriteReadRoundTrip:
    """Integration tests: write_context → get_* accessors."""

    def test_write_then_read_round_trip(self, monkeypatch, tmp_path):
        """write_context() output should be readable by get_pact_context()."""
        import shared.pact_context as ctx_module

        ctx_file = tmp_path / "pact-session-context.json"
        monkeypatch.setattr(ctx_module, "_context_path", ctx_file)
        monkeypatch.setattr(ctx_module, "_cache", None)

        ctx_module.write_context(
            team_name="pact-roundtrip",
            session_id="roundtrip-session-123",
            project_dir="/Users/test/roundtrip",
        )

        result = ctx_module.get_pact_context()

        assert result["team_name"] == "pact-roundtrip"
        assert result["session_id"] == "roundtrip-session-123"
        assert result["project_dir"] == "/Users/test/roundtrip"
        assert result["started_at"] != ""

    def test_write_then_convenience_accessors(self, monkeypatch, tmp_path):
        """write_context() should be accessible via all convenience functions."""
        import shared.pact_context as ctx_module

        ctx_file = tmp_path / "pact-session-context.json"
        monkeypatch.setattr(ctx_module, "_context_path", ctx_file)
        monkeypatch.setattr(ctx_module, "_cache", None)

        ctx_module.write_context(
            team_name="PACT-UpperCase",
            session_id="session-abc",
            project_dir="/my/project",
            plugin_root="/plugins/PACT/3.17.0",
        )

        assert ctx_module.get_team_name() == "pact-uppercase"  # lowercased
        # Must clear cache for each independent accessor test
        monkeypatch.setattr(ctx_module, "_cache", None)
        assert ctx_module.get_session_id() == "session-abc"
        monkeypatch.setattr(ctx_module, "_cache", None)
        assert ctx_module.get_project_dir() == "/my/project"
        monkeypatch.setattr(ctx_module, "_cache", None)
        assert ctx_module.get_plugin_root() == "/plugins/PACT/3.17.0"


class TestCategoryC_MemoryScriptFallback:
    """Tests for shared pact_session module used by Category C skill scripts."""

    def test_pact_session_reads_from_context_file(self, monkeypatch, tmp_path):
        """get_session_id_from_context_file should read session-scoped context file."""
        from scripts.pact_session import get_session_id_from_context_file

        session_id = "init-session-xyz"
        project_dir = "/Users/test/my-project"
        slug = Path(project_dir).name
        ctx_dir = tmp_path / ".claude" / "pact-sessions" / slug / session_id
        ctx_dir.mkdir(parents=True)
        ctx_file = ctx_dir / "pact-session-context.json"
        ctx_file.write_text(json.dumps({
            "session_id": session_id,
            "team_name": "test",
        }), encoding="utf-8")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        result = get_session_id_from_context_file(
            session_id=session_id,
            project_dir=project_dir,
        )

        assert result == session_id

    def test_pact_session_returns_empty_when_file_missing(self, monkeypatch, tmp_path):
        """get_session_id_from_context_file should return empty string when file missing."""
        from scripts.pact_session import get_session_id_from_context_file

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        result = get_session_id_from_context_file(
            session_id="nonexistent-session",
            project_dir="/Users/test/project",
        )

        assert result == ""

    def test_pact_session_returns_empty_when_no_args(self):
        """get_session_id_from_context_file should return empty string with no args."""
        from scripts.pact_session import get_session_id_from_context_file

        result = get_session_id_from_context_file()

        assert result == ""

    def test_pact_session_returns_empty_on_invalid_json(self, monkeypatch, tmp_path):
        """get_session_id_from_context_file should return empty string on corrupt JSON."""
        from scripts.pact_session import get_session_id_from_context_file

        session_id = "corrupt-session"
        project_dir = "/Users/test/project"
        slug = Path(project_dir).name
        ctx_dir = tmp_path / ".claude" / "pact-sessions" / slug / session_id
        ctx_dir.mkdir(parents=True)
        ctx_file = ctx_dir / "pact-session-context.json"
        ctx_file.write_text("not json", encoding="utf-8")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        result = get_session_id_from_context_file(
            session_id=session_id,
            project_dir=project_dir,
        )

        assert result == ""

    def test_memory_api_imports_from_pact_session(self):
        """memory_api should import get_session_id_from_context_file from pact_session."""
        from scripts import memory_api
        from scripts.pact_session import get_session_id_from_context_file

        assert memory_api.get_session_id_from_context_file is get_session_id_from_context_file

    def test_memory_init_imports_from_pact_session(self):
        """memory_init should import get_session_id_from_context_file from pact_session."""
        from scripts import memory_init
        from scripts.pact_session import get_session_id_from_context_file

        assert memory_init.get_session_id_from_context_file is get_session_id_from_context_file

    def test_memory_init_embedding_path_falls_back_to_unknown_without_args(self, monkeypatch, tmp_path):
        """_get_embedding_attempted_path falls back to 'unknown' when called without context.

        _get_embedding_attempted_path() calls get_session_id_from_context_file()
        with no args. Without both session_id and project_dir, the function
        returns empty string, and the path uses 'unknown' as fallback.
        """
        from scripts import memory_init

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        result = memory_init._get_embedding_attempted_path()

        assert "unknown" in str(result)

    def test_memory_init_embedding_path_falls_back_to_unknown(self, monkeypatch, tmp_path):
        """_get_embedding_attempted_path should fall back to 'unknown' when no context file."""
        from scripts import memory_init

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)

        result = memory_init._get_embedding_attempted_path()

        assert "unknown" in str(result)


class TestPathSync:
    """Verify pact_context and pact_session produce identical session-scoped paths."""

    def test_session_scoped_paths_match(self, monkeypatch, tmp_path):
        """pact_context and pact_session should compute the same path for same inputs."""
        from scripts.pact_session import _context_file_path as session_path

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        session_id = "abc-12345"
        project_dir = "/Users/test/PACT-prompt"
        slug = Path(project_dir).name

        expected = (
            tmp_path / ".claude" / "pact-sessions"
            / slug / session_id / "pact-session-context.json"
        )

        assert session_path(session_id, project_dir) == expected


class TestWriteContextDirCreation:
    """Verify write_context creates nested parent directories and writes valid content."""

    def test_creates_deeply_nested_parent_and_writes_valid_content(self, monkeypatch, tmp_path):
        """write_context should create multi-level parent dirs and write valid JSON."""
        import shared.pact_context as ctx_module

        ctx_file = tmp_path / "a" / "b" / "c" / "pact-session-context.json"
        monkeypatch.setattr(ctx_module, "_context_path", ctx_file)

        ctx_module.write_context(
            team_name="pact-nested",
            session_id="nested-session",
            project_dir="/nested/project",
        )

        assert ctx_file.exists()
        assert (tmp_path / "a" / "b" / "c").is_dir()
        data = json.loads(ctx_file.read_text(encoding="utf-8"))
        assert data["team_name"] == "pact-nested"
        assert data["session_id"] == "nested-session"
        assert data["project_dir"] == "/nested/project"
        assert data["started_at"] != ""


    def test_creates_parent_dir_with_0o700_mode(self, monkeypatch, tmp_path):
        """write_context must create parent directories with 0o700 permissions."""
        import shared.pact_context as ctx_module

        ctx_file = tmp_path / "new-dir" / "pact-session-context.json"
        monkeypatch.setattr(ctx_module, "_context_path", ctx_file)

        ctx_module.write_context(
            team_name="pact-mode",
            session_id="mode-test",
            project_dir="/mode/project",
        )

        parent = ctx_file.parent
        assert parent.is_dir()
        actual_mode = parent.stat().st_mode & 0o777
        assert actual_mode == 0o700, (
            f"Expected 0o700, got {oct(actual_mode)} for {parent}"
        )


class TestDetectSessionIdPriority:
    """Verify _detect_session_id uses context file only (no env var fallback)."""

    def test_detect_session_id_returns_none_without_context(self, monkeypatch, tmp_path):
        """_detect_session_id should return None when no context available.

        _detect_session_id() calls get_session_id_from_context_file() with
        no args — without both session_id and project_dir, there's no
        session-scoped path to read, so it returns None.
        """
        from scripts.memory_api import PACTMemory

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Even with CLAUDE_SESSION_ID set, _detect_session_id should NOT use it
        monkeypatch.setenv("CLAUDE_SESSION_ID", "env-should-be-ignored")

        result = PACTMemory._detect_session_id()

        assert result is None


class TestConcurrentWriteContext:
    """Verify write_context handles concurrent writes safely."""

    def test_concurrent_writes_produce_valid_json(self, monkeypatch, tmp_path):
        """Multiple threads calling write_context simultaneously should not corrupt the file."""
        import threading
        import shared.pact_context as ctx_module

        ctx_file = tmp_path / "pact-session-context.json"
        monkeypatch.setattr(ctx_module, "_context_path", ctx_file)

        errors = []

        def writer(index):
            try:
                ctx_module.write_context(
                    team_name=f"pact-thread-{index}",
                    session_id=f"session-{index}",
                    project_dir=f"/project-{index}",
                )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent writes raised errors: {errors}"

        # File should exist and contain valid JSON
        assert ctx_file.exists()
        data = json.loads(ctx_file.read_text(encoding="utf-8"))
        # Last writer wins — value should be from one of the threads
        assert data["team_name"].startswith("pact-thread-")
        assert data["session_id"].startswith("session-")
        assert data["project_dir"].startswith("/project-")

        # No temp files should remain after all threads complete
        tmp_files = list(tmp_path.glob(".pact-session-context-*.tmp"))
        assert tmp_files == [], f"Leftover temp files: {tmp_files}"


class TestUninitializedAccessors:
    """Verify accessor behavior when init() has not been called."""

    def test_get_pact_context_returns_empty_when_context_path_none(self, monkeypatch):
        """get_pact_context() should return _EMPTY_CONTEXT when _context_path is None."""
        import shared.pact_context as ctx_module

        monkeypatch.setattr(ctx_module, "_context_path", None)
        monkeypatch.setattr(ctx_module, "_cache", None)

        result = ctx_module.get_pact_context()

        assert result == ctx_module._EMPTY_CONTEXT
        assert result["team_name"] == ""
        assert result["session_id"] == ""
        assert result["project_dir"] == ""
        assert result["started_at"] == ""

    def test_write_context_computes_path_when_context_path_none(self, monkeypatch, tmp_path):
        """write_context() should compute session-scoped path from args when _context_path is None."""
        import shared.pact_context as ctx_module

        monkeypatch.setattr(ctx_module, "_context_path", None)
        monkeypatch.setattr(ctx_module, "_cache", None)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        ctx_module.write_context(
            team_name="pact-test",
            session_id="sess-123",
            project_dir="/my/project",
        )

        # Path should be: {home}/.claude/pact-sessions/{slug}/{session_id}/pact-session-context.json
        expected_path = (
            tmp_path / ".claude" / "pact-sessions"
            / "project" / "sess-123" / "pact-session-context.json"
        )
        assert expected_path.exists()

        data = json.loads(expected_path.read_text(encoding="utf-8"))
        assert data["team_name"] == "pact-test"
        assert data["session_id"] == "sess-123"
        assert data["project_dir"] == "/my/project"

        # Module state should now point to the computed path
        assert ctx_module._context_path == expected_path


class TestInitOrdering:
    """Verify get_team_name() behavior before and after init()."""

    def test_get_team_name_before_vs_after_init(self, monkeypatch, tmp_path):
        """get_team_name() should return empty before init(); correct value after."""
        import shared.pact_context as ctx_module

        # Reset module state to simulate a fresh hook process
        monkeypatch.setattr(ctx_module, "_context_path", None)
        monkeypatch.setattr(ctx_module, "_cache", None)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/test/my-project")

        # Write context file at the session-scoped path
        session_id = "sess-abc12345"
        slug = "my-project"
        scoped_dir = tmp_path / ".claude" / "pact-sessions" / slug / session_id
        scoped_dir.mkdir(parents=True)
        ctx_file = scoped_dir / "pact-session-context.json"
        ctx_file.write_text(json.dumps({
            "team_name": "pact-abc12345",
            "session_id": session_id,
            "project_dir": "/Users/test/my-project",
            "started_at": "2026-01-01T00:00:00Z",
        }), encoding="utf-8")

        # BEFORE init(): _context_path is None → returns _EMPTY_CONTEXT
        result_before = ctx_module.get_team_name()
        assert result_before == ""

        # Clear cache so the next read is fresh
        monkeypatch.setattr(ctx_module, "_cache", None)

        # Call init() with matching session_id → sets _context_path to session-scoped path
        ctx_module.init({"session_id": session_id})

        # AFTER init(): reads from session-scoped path → returns the team_name
        result_after = ctx_module.get_team_name()
        assert result_after == "pact-abc12345"


class TestSessionScopedPathE2E:
    """End-to-end tests for the session-scoped write→init→read cycle."""

    def test_write_init_read_session_scoped_cycle(self, monkeypatch, tmp_path):
        """Full cycle: write_context → init → get_team_name with session-scoped path."""
        import shared.pact_context as ctx_module

        monkeypatch.setattr(ctx_module, "_context_path", None)
        monkeypatch.setattr(ctx_module, "_cache", None)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        session_id = "e2e-session-42"
        project_dir = "/Users/test/PACT-prompt"

        # write_context with _context_path=None computes session-scoped path
        ctx_module.write_context(
            team_name="pact-e2etest",
            session_id=session_id,
            project_dir=project_dir,
        )

        # Verify file exists at expected session-scoped path
        slug = "PACT-prompt"  # Path(project_dir).name
        expected_path = (
            tmp_path / ".claude" / "pact-sessions"
            / slug / session_id / "pact-session-context.json"
        )
        assert expected_path.exists()

        # Verify _context_path was set by write_context
        assert ctx_module._context_path == expected_path

        # Read back via convenience accessor
        result = ctx_module.get_team_name()
        assert result == "pact-e2etest"

        # Also verify other accessors
        monkeypatch.setattr(ctx_module, "_cache", None)
        assert ctx_module.get_session_id() == session_id
        monkeypatch.setattr(ctx_module, "_cache", None)
        assert ctx_module.get_project_dir() == project_dir

    def test_session_scoped_path_uses_project_dir_name_as_slug(self, monkeypatch, tmp_path):
        """Slug should be Path(project_dir).name, not the full path."""
        import shared.pact_context as ctx_module

        monkeypatch.setattr(ctx_module, "_context_path", None)
        monkeypatch.setattr(ctx_module, "_cache", None)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # project_dir with nested path — slug should be just the final component
        ctx_module.write_context(
            team_name="pact-slugtest",
            session_id="slug-session",
            project_dir="/deeply/nested/path/MyProject",
        )

        expected_path = (
            tmp_path / ".claude" / "pact-sessions"
            / "MyProject" / "slug-session" / "pact-session-context.json"
        )
        assert expected_path.exists()
        assert ctx_module._context_path == expected_path


class TestPactSessionPath:
    """Tests for pact_session.py _context_file_path() path construction."""

    def test_session_scoped_path_when_both_args_provided(self, monkeypatch, tmp_path):
        """_context_file_path(session_id, project_dir) should return session-scoped path."""
        from scripts.pact_session import _context_file_path

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        result = _context_file_path(
            session_id="abc-session-123",
            project_dir="/Users/test/PACT-prompt",
        )

        expected = (
            tmp_path / ".claude" / "pact-sessions"
            / "PACT-prompt" / "abc-session-123" / "pact-session-context.json"
        )
        assert result == expected

    def test_returns_none_when_args_missing(self, monkeypatch, tmp_path):
        """_context_file_path with missing args should return None."""
        from scripts.pact_session import _context_file_path

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        assert _context_file_path("", "") is None
        assert _context_file_path("session-id", "") is None
        assert _context_file_path("", "/project/dir") is None


class TestMigrationCompleteness:
    """Verify no hook or skill runtime code references the replaced env vars.

    Uses AST-based scanning to find os.environ.get() / os.getenv() /
    os.environ[] calls with phantom env var names as the first argument.
    This is more reliable than string matching because it ignores comments,
    docstrings, and string literals that aren't used as env var lookups.
    """

    # Phantom env var names that should not appear in any runtime env lookup
    PHANTOM_ENV_VARS = [
        "CLAUDE_CODE_TEAM_NAME",
        "CLAUDE_CODE_AGENT_NAME",
        "CLAUDE_SESSION_ID",
    ]

    @staticmethod
    def _find_env_var_references(source: str, env_var: str) -> list[int]:
        """Find line numbers where os.environ.get/os.getenv/os.environ[] appears.

        Scans AST for:
        - os.environ.get("ENV_VAR", ...) — Call node with Attribute func
        - os.getenv("ENV_VAR", ...) — Call node with Attribute func
        - os.environ["ENV_VAR"] — Subscript node with string slice

        Returns list of line numbers with violations.
        """
        import ast

        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []

        violations = []

        for node in ast.walk(tree):
            # Match os.environ.get("ENV_VAR", ...)
            if isinstance(node, ast.Call):
                func = node.func
                if (isinstance(func, ast.Attribute)
                        and func.attr == "get"
                        and isinstance(func.value, ast.Attribute)
                        and func.value.attr == "environ"
                        and isinstance(func.value.value, ast.Name)
                        and func.value.value.id == "os"
                        and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value == env_var):
                    violations.append(node.lineno)

            # Match os.getenv("ENV_VAR", ...)
            if isinstance(node, ast.Call):
                func = node.func
                if (isinstance(func, ast.Attribute)
                        and func.attr == "getenv"
                        and isinstance(func.value, ast.Name)
                        and func.value.id == "os"
                        and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value == env_var):
                    violations.append(node.lineno)

            # Match os.environ["ENV_VAR"]
            if isinstance(node, ast.Subscript):
                value = node.value
                if (isinstance(value, ast.Attribute)
                        and value.attr == "environ"
                        and isinstance(value.value, ast.Name)
                        and value.value.id == "os"
                        and isinstance(node.slice, ast.Constant)
                        and node.slice.value == env_var):
                    violations.append(node.lineno)

        return violations

    @pytest.mark.parametrize("env_var", PHANTOM_ENV_VARS, ids=PHANTOM_ENV_VARS)
    def test_no_phantom_env_var_in_hooks(self, env_var):
        """No hook should read phantom env vars at runtime (AST-verified)."""
        hooks_dir = Path(__file__).parent.parent / "hooks"
        violations = []

        for py_file in sorted(hooks_dir.rglob("*.py")):
            source = py_file.read_text(encoding="utf-8")
            lines = self._find_env_var_references(source, env_var)
            for line in lines:
                violations.append(f"{py_file.relative_to(hooks_dir)}:{line}")

        assert violations == [], f"Hooks reading {env_var}: {violations}"

    @pytest.mark.parametrize("env_var", PHANTOM_ENV_VARS, ids=PHANTOM_ENV_VARS)
    def test_no_phantom_env_var_in_skills(self, env_var):
        """No skill script should read phantom env vars at runtime (AST-verified)."""
        skills_dir = Path(__file__).parent.parent / "skills"
        violations = []

        for py_file in sorted(skills_dir.rglob("*.py")):
            source = py_file.read_text(encoding="utf-8")
            lines = self._find_env_var_references(source, env_var)
            for line in lines:
                violations.append(f"{py_file.relative_to(skills_dir)}:{line}")

        assert violations == [], f"Skill scripts reading {env_var}: {violations}"


class TestInitBeforeReaderOrdering:
    """Verify every hook that uses a pact_context reader calls init() first.

    Uses the ast module to find call sites in each hook's main() function
    and checks that pact_context.init() appears at an earlier line than
    any reader call (get_team_name, get_session_id, resolve_agent_name).
    """

    # Hooks that import pact_context AND call a reader in their main().
    # Each tuple: (module filename, reader function names used)
    HOOKS_WITH_READERS = [
        ("agent_handoff_emitter.py", {"get_team_name"}),
        ("teachback_check.py", {"get_team_name", "resolve_agent_name"}),
        ("peer_inject.py", {"get_team_name"}),
        ("file_tracker.py", {"get_team_name", "resolve_agent_name"}),
        ("track_files.py", {"get_session_id"}),
        ("merge_guard_pre.py", {"get_session_id"}),
        ("merge_guard_post.py", {"get_session_id"}),
        ("teammate_idle.py", {"get_team_name"}),
    ]

    @pytest.mark.parametrize(
        "hook_file, reader_names",
        HOOKS_WITH_READERS,
        ids=[h[0].replace(".py", "") for h in HOOKS_WITH_READERS],
    )
    def test_init_appears_before_readers(self, hook_file, reader_names):
        """pact_context.init() must appear before any reader call in main()."""
        import ast

        hooks_dir = Path(__file__).parent.parent / "hooks"
        source = (hooks_dir / hook_file).read_text(encoding="utf-8")
        tree = ast.parse(source)

        # Find the main() function
        main_func = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "main":
                main_func = node
                break

        assert main_func is not None, f"{hook_file} has no main() function"

        # Find line numbers of pact_context.init() and reader calls
        init_line = None
        reader_lines = []

        for node in ast.walk(main_func):
            if not isinstance(node, ast.Call):
                continue

            # Match pact_context.init(...)
            func = node.func
            if (isinstance(func, ast.Attribute)
                    and func.attr == "init"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "pact_context"):
                if init_line is None:
                    init_line = node.lineno

            # Match reader calls: get_team_name(), get_session_id(), resolve_agent_name()
            if isinstance(func, ast.Name) and func.id in reader_names:
                reader_lines.append((func.id, node.lineno))

        assert init_line is not None, (
            f"{hook_file}: main() calls readers {reader_names} but never calls pact_context.init()"
        )

        for reader_name, reader_line in reader_lines:
            assert init_line < reader_line, (
                f"{hook_file}: {reader_name}() at line {reader_line} "
                f"appears before pact_context.init() at line {init_line}"
            )

    def test_session_end_init_before_indirect_reader(self):
        """session_end.py: init() must appear before get_project_slug() (which calls get_project_dir()).

        session_end.py calls get_project_dir() indirectly through its
        get_project_slug() wrapper. The parametrized test above only catches
        direct reader calls, so this test verifies the indirect case.
        """
        import ast

        hooks_dir = Path(__file__).parent.parent / "hooks"
        source = (hooks_dir / "session_end.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        # Verify get_project_slug() calls get_project_dir()
        slug_func = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "get_project_slug":
                slug_func = node
                break
        assert slug_func is not None, "session_end.py has no get_project_slug() function"

        calls_get_project_dir = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "get_project_dir"
            for n in ast.walk(slug_func)
        )
        assert calls_get_project_dir, (
            "session_end.py: get_project_slug() does not call get_project_dir()"
        )

        # Verify init() appears before get_project_slug() in main()
        main_func = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "main":
                main_func = node
                break
        assert main_func is not None, "session_end.py has no main() function"

        init_line = None
        slug_line = None
        for node in ast.walk(main_func):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr == "init"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "pact_context"):
                if init_line is None:
                    init_line = node.lineno
            if isinstance(func, ast.Name) and func.id == "get_project_slug":
                if slug_line is None:
                    slug_line = node.lineno

        assert init_line is not None, (
            "session_end.py: main() never calls pact_context.init()"
        )
        assert slug_line is not None, (
            "session_end.py: main() never calls get_project_slug()"
        )
        assert init_line < slug_line, (
            f"session_end.py: get_project_slug() at line {slug_line} "
            f"appears before pact_context.init() at line {init_line}"
        )

    def test_teachback_check_init_before_indirect_reader(self):
        """teachback_check.py: init() must appear before should_warn() (which calls _get_marker_path() → get_session_dir()).

        teachback_check.py calls get_session_dir() indirectly through
        _get_marker_path(), which is called from should_warn(). The
        parametrized test covers the direct readers (get_team_name,
        resolve_agent_name), so this test verifies the indirect case.
        """
        import ast

        hooks_dir = Path(__file__).parent.parent / "hooks"
        source = (hooks_dir / "teachback_check.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        # Verify _get_marker_path() calls get_session_dir()
        marker_func = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_get_marker_path":
                marker_func = node
                break
        assert marker_func is not None, "teachback_check.py has no _get_marker_path() function"

        calls_get_session_dir = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "get_session_dir"
            for n in ast.walk(marker_func)
        )
        assert calls_get_session_dir, (
            "teachback_check.py: _get_marker_path() does not call get_session_dir()"
        )

        # Verify init() appears before should_warn() in main()
        main_func = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "main":
                main_func = node
                break
        assert main_func is not None, "teachback_check.py has no main() function"

        init_line = None
        warn_line = None
        for node in ast.walk(main_func):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr == "init"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "pact_context"):
                if init_line is None:
                    init_line = node.lineno
            if isinstance(func, ast.Name) and func.id == "should_warn":
                if warn_line is None:
                    warn_line = node.lineno

        assert init_line is not None, (
            "teachback_check.py: main() never calls pact_context.init()"
        )
        assert warn_line is not None, (
            "teachback_check.py: main() never calls should_warn()"
        )
        assert init_line < warn_line, (
            f"teachback_check.py: should_warn() at line {warn_line} "
            f"appears before pact_context.init() at line {init_line}"
        )


class TestLibraryModuleInitContract:
    """Verify library modules that call pact_context readers work when init() was called first.

    task_utils.py and checkpoint_builder.py call pact_context readers (get_session_id)
    without calling init() themselves — they rely on the calling hook to have initialized
    the module. These tests verify the transitive contract works correctly.
    """

    def test_task_utils_get_task_list_after_init(self, pact_context, monkeypatch, tmp_path):
        """task_utils.get_task_list() should read session_id via get_session_id() after init()."""
        from shared.task_utils import get_task_list

        session_id = "contract-test-session"
        pact_context(session_id=session_id)

        # Create a task file at the expected path so get_task_list finds it
        tasks_dir = tmp_path / ".claude" / "tasks" / session_id
        tasks_dir.mkdir(parents=True)
        task_file = tasks_dir / "1.json"
        task_file.write_text(json.dumps({
            "id": "1",
            "subject": "test task",
            "status": "pending",
        }), encoding="utf-8")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Ensure CLAUDE_CODE_TASK_LIST_ID doesn't override
        monkeypatch.delenv("CLAUDE_CODE_TASK_LIST_ID", raising=False)

        result = get_task_list()

        assert result is not None
        assert len(result) == 1
        assert result[0]["subject"] == "test task"

    def test_checkpoint_builder_session_id_dependency_after_init(self, pact_context):
        """checkpoint_builder depends on shared.pact_context.get_session_id after init().

        checkpoint_builder.py imports get_session_id as _pact_get_session_id and
        wraps it: `_pact_get_session_id() or "unknown"`. This test verifies the
        underlying dependency returns the correct value when init() was called
        by a prior hook. (checkpoint_builder can't be imported directly in tests
        due to relative imports from the refresh package.)
        """
        from shared.pact_context import get_session_id

        pact_context(session_id="builder-session-xyz")

        result = get_session_id()

        assert result == "builder-session-xyz"


# =============================================================================
# get_session_dir() — Adversarial Edge Cases (Test Engineer)
# =============================================================================

class TestGetSessionDirAdversarial:
    """Adversarial tests for get_session_dir() — edge case inputs.

    get_session_dir() constructs paths from user-controlled values (project_dir
    name, session_id). These tests verify the function handles unusual but valid
    inputs without crashing or producing incorrect paths.
    """

    def test_slug_with_dots(self, pact_context):
        """Project dirs with dots in the name (e.g., 'my.app') should work."""
        from shared.pact_context import get_session_dir

        pact_context(session_id="abc-123", project_dir="/Users/dev/my.app.v2")

        result = get_session_dir()
        assert "my.app.v2" in result
        assert result.endswith("pact-sessions/my.app.v2/abc-123")

    def test_slug_with_spaces(self, pact_context):
        """Project dirs with spaces should produce valid paths."""
        from shared.pact_context import get_session_dir

        pact_context(session_id="abc-123", project_dir="/Users/dev/My Project")

        result = get_session_dir()
        assert "My Project" in result
        assert result.endswith("pact-sessions/My Project/abc-123")

    def test_slug_with_unicode(self, pact_context):
        """Unicode characters in project name should pass through."""
        from shared.pact_context import get_session_dir

        pact_context(session_id="abc-123", project_dir="/Users/dev/プロジェクト")

        result = get_session_dir()
        assert "プロジェクト" in result

    def test_deeply_nested_project_dir(self, pact_context):
        """Deeply nested project paths should use only the basename as slug."""
        from shared.pact_context import get_session_dir

        pact_context(
            session_id="abc-123",
            project_dir="/a/very/deeply/nested/project/my-app",
        )

        result = get_session_dir()
        # Only the basename "my-app" should appear as slug, not the full path
        assert "my-app/abc-123" in result
        assert "/a/very/deeply" not in result

    def test_session_id_with_special_characters(self, pact_context):
        """Session IDs with unusual characters should be preserved as-is."""
        from shared.pact_context import get_session_dir

        # Real session IDs are UUIDs, but get_session_dir() doesn't validate
        pact_context(session_id="not-a-uuid-but-valid", project_dir="/test/proj")

        result = get_session_dir()
        assert "not-a-uuid-but-valid" in result

    def test_project_dir_trailing_slash(self, pact_context):
        """Trailing slash in project_dir should not affect slug extraction."""
        from shared.pact_context import get_session_dir

        # Path("foo/bar/").name returns "bar" in Python — trailing slash is stripped
        pact_context(session_id="abc", project_dir="/Users/dev/my-app/")

        result = get_session_dir()
        assert result.endswith("pact-sessions/my-app/abc")

    def test_returns_string_not_path(self, pact_context):
        """get_session_dir() should return a str, not a Path object."""
        from shared.pact_context import get_session_dir

        pact_context(session_id="abc-123", project_dir="/test/proj")

        result = get_session_dir()
        assert isinstance(result, str)

    def test_path_contains_home_dir(self, pact_context):
        """Returned path should include .claude/pact-sessions/ under home."""
        from shared.pact_context import get_session_dir

        pact_context(session_id="abc-123", project_dir="/test/proj")

        result = get_session_dir()
        assert ".claude/pact-sessions/" in result

    def test_path_traversal_session_id_neutralized(self, monkeypatch, tmp_path):
        """A session_id containing '../' path traversal components must NOT
        resolve outside ~/.claude/pact-sessions/.

        The _build_session_path guard resolves the candidate path and checks
        it stays under the sessions root. If traversal is detected, it falls
        back to using just the basename of the session_id, neutralizing the
        traversal.
        """
        from shared.pact_context import _build_session_path

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        sessions_root = tmp_path / ".claude" / "pact-sessions"
        sessions_root.mkdir(parents=True, exist_ok=True)

        result = _build_session_path("my-project", "../../etc/passwd")
        resolved = result.resolve()

        sessions_root_resolved = sessions_root.resolve()
        assert str(resolved).startswith(str(sessions_root_resolved)), (
            f"Path traversal not neutralized: _build_session_path returned "
            f"{result} which resolves to {resolved}, outside "
            f"{sessions_root_resolved}"
        )


    def test_sibling_directory_prefix_collision(self, monkeypatch, tmp_path):
        """A session_id that traverses into a sibling directory whose name
        shares the 'pact-sessions' prefix must be caught by the guard.

        Without the trailing os.sep in the prefix check, a resolved path like
        /home/user/.claude/pact-sessions-evil/foo would pass the
        startswith('/home/user/.claude/pact-sessions') guard because the
        string prefix matches without requiring a path separator boundary.
        """
        from shared.pact_context import _build_session_path

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        sessions_root = tmp_path / ".claude" / "pact-sessions"
        sessions_root.mkdir(parents=True, exist_ok=True)
        evil_sibling = tmp_path / ".claude" / "pact-sessions-evil"
        evil_sibling.mkdir(parents=True, exist_ok=True)

        # session_id traverses up out of {slug}/ into the sibling dir
        result = _build_session_path("my-project", "../../pact-sessions-evil/payload")
        resolved = str(result.resolve())

        sessions_prefix = str(sessions_root.resolve()) + "/"
        assert resolved.startswith(sessions_prefix), (
            f"Sibling directory prefix collision not caught: "
            f"_build_session_path resolved to {resolved}, which is outside "
            f"{sessions_prefix}"
        )

    def test_dotdot_only_session_id_returns_safe_path(self, monkeypatch, tmp_path):
        """session_id='../..' produces a path whose basename() is '..',
        which the guard must reject. The result must not contain '..'
        path components.
        """
        from shared.pact_context import _build_session_path

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        sessions_root = tmp_path / ".claude" / "pact-sessions"
        sessions_root.mkdir(parents=True, exist_ok=True)

        result = _build_session_path("my-project", "../..")
        resolved = result.resolve()

        sessions_root_resolved = sessions_root.resolve()
        assert (
            resolved == sessions_root_resolved
            or sessions_root_resolved in resolved.parents
        ), (
            f"'../..' session_id escaped containment: resolved to "
            f"{resolved}, outside {sessions_root_resolved}"
        )
        assert ".." not in result.parts, (
            f"Result path contains '..' component: {result}"
        )

    def test_exception_in_validation_returns_fail_closed(self, monkeypatch, tmp_path):
        """When resolve() raises, the guard returns a slug-only path
        (fail-closed) rather than the unvalidated candidate.
        """
        from shared.pact_context import _build_session_path

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        sessions_root = tmp_path / ".claude" / "pact-sessions"
        sessions_root.mkdir(parents=True, exist_ok=True)

        original_resolve = Path.resolve

        def exploding_resolve(self, strict=False):
            if "malicious" in str(self):
                raise OSError("simulated resolve failure")
            return original_resolve(self, strict=strict)

        monkeypatch.setattr(Path, "resolve", exploding_resolve)

        result = _build_session_path("my-project", "malicious-id")
        expected_slug_only = sessions_root / "my-project"
        assert result == expected_slug_only, (
            f"Expected fail-closed slug-only path {expected_slug_only}, "
            f"got {result}"
        )


# =============================================================================
# Parallel Session Isolation — Core Fix Verification (Test Engineer)
# =============================================================================

class TestParallelSessionIsolation:
    """Verify that two concurrent sessions on the same project get isolated paths.

    This is the CORE behavioral change of issue #345 — the entire point of
    session-scoping. Two sessions with different session_ids must produce
    different get_session_dir() values, ensuring teachback markers, context
    files, and other artifacts don't interfere.
    """

    def test_different_sessions_produce_different_dirs(self, monkeypatch, tmp_path):
        """Two sessions with different IDs must get different directories."""
        import shared.pact_context as ctx

        session_a = "aaaaaaaa-0000-0000-0000-aaaaaaaaaaaa"
        session_b = "bbbbbbbb-0000-0000-0000-bbbbbbbbbbbb"

        # Session A
        monkeypatch.setattr(ctx, "_context_path", None)
        monkeypatch.setattr(ctx, "_cache", None)
        ctx._cache = {
            "team_name": "pact-test",
            "session_id": session_a,
            "project_dir": "/test/my-project",
            "started_at": "2026-01-01T00:00:00Z",
        }
        dir_a = ctx.get_session_dir()

        # Session B
        ctx._cache = {
            "team_name": "pact-test",
            "session_id": session_b,
            "project_dir": "/test/my-project",
            "started_at": "2026-01-01T00:00:00Z",
        }
        dir_b = ctx.get_session_dir()

        assert dir_a != dir_b
        assert session_a in dir_a
        assert session_b in dir_b
        # Both should share the same slug prefix
        assert "my-project" in dir_a
        assert "my-project" in dir_b

    def test_same_session_same_project_same_dir(self, pact_context):
        """Same session_id + project_dir must produce identical paths."""
        from shared.pact_context import get_session_dir

        pact_context(session_id="abc-123", project_dir="/test/proj")

        result1 = get_session_dir()
        result2 = get_session_dir()

        assert result1 == result2

    def test_same_session_different_project_different_dir(self, monkeypatch, tmp_path):
        """Same session_id on different projects must produce different dirs."""
        import shared.pact_context as ctx

        monkeypatch.setattr(ctx, "_context_path", None)
        monkeypatch.setattr(ctx, "_cache", None)

        ctx._cache = {
            "team_name": "t", "session_id": "same-id",
            "project_dir": "/test/project-a", "started_at": "",
        }
        dir_a = ctx.get_session_dir()

        ctx._cache = {
            "team_name": "t", "session_id": "same-id",
            "project_dir": "/test/project-b", "started_at": "",
        }
        dir_b = ctx.get_session_dir()

        assert dir_a != dir_b
        assert "project-a" in dir_a
        assert "project-b" in dir_b
