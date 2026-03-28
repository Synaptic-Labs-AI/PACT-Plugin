"""
Tests for file_size_check.py — PostToolUse hook for file size monitoring.

Tests cover:
1. is_excluded_path: exclusion pattern matching
2. should_check_file: extension filtering
3. count_lines: file reading, error handling
4. format_guidance: warning vs critical messages
5. main: tool filtering, path filtering, threshold logic, output format
"""
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


# ---------------------------------------------------------------------------
# is_excluded_path
# ---------------------------------------------------------------------------

class TestIsExcludedPath:
    def test_excludes_pycache(self):
        from file_size_check import is_excluded_path
        assert is_excluded_path("/project/__pycache__/module.pyc") is True

    def test_excludes_node_modules(self):
        from file_size_check import is_excluded_path
        assert is_excluded_path("/project/node_modules/pkg/index.js") is True

    def test_excludes_git(self):
        from file_size_check import is_excluded_path
        assert is_excluded_path("/project/.git/config") is True

    def test_excludes_venv(self):
        from file_size_check import is_excluded_path
        assert is_excluded_path("/project/.venv/lib/site.py") is True

    def test_allows_normal_path(self):
        from file_size_check import is_excluded_path
        assert is_excluded_path("/project/src/app.py") is False

    def test_excludes_dist(self):
        from file_size_check import is_excluded_path
        assert is_excluded_path("/project/dist/bundle.js") is True


# ---------------------------------------------------------------------------
# should_check_file
# ---------------------------------------------------------------------------

class TestShouldCheckFile:
    def test_checks_python(self):
        from file_size_check import should_check_file
        assert should_check_file("/src/app.py") is True

    def test_checks_typescript(self):
        from file_size_check import should_check_file
        assert should_check_file("/src/app.ts") is True

    def test_checks_tsx(self):
        from file_size_check import should_check_file
        assert should_check_file("/src/App.tsx") is True

    def test_skips_markdown(self):
        from file_size_check import should_check_file
        assert should_check_file("/docs/README.md") is False

    def test_skips_json(self):
        from file_size_check import should_check_file
        assert should_check_file("/config/settings.json") is False

    def test_skips_yaml(self):
        from file_size_check import should_check_file
        assert should_check_file("/config/app.yaml") is False

    def test_case_insensitive_extension(self):
        from file_size_check import should_check_file
        assert should_check_file("/src/App.PY") is True


# ---------------------------------------------------------------------------
# count_lines
# ---------------------------------------------------------------------------

class TestCountLines:
    def test_counts_lines(self, tmp_path):
        from file_size_check import count_lines
        f = tmp_path / "test.py"
        f.write_text("line1\nline2\nline3\n")
        assert count_lines(str(f)) == 3

    def test_returns_zero_for_missing(self):
        from file_size_check import count_lines
        assert count_lines("/nonexistent/file.py") == 0

    def test_empty_file(self, tmp_path):
        from file_size_check import count_lines
        f = tmp_path / "empty.py"
        f.write_text("")
        assert count_lines(str(f)) == 0


# ---------------------------------------------------------------------------
# format_guidance
# ---------------------------------------------------------------------------

class TestFormatGuidance:
    def test_warning_level(self):
        from file_size_check import format_guidance
        msg = format_guidance("/src/app.py", 650)
        assert "FILE SIZE" in msg
        assert "650 lines" in msg
        assert "app.py" in msg

    def test_critical_level(self):
        from file_size_check import format_guidance
        msg = format_guidance("/src/app.py", 850)
        assert "CRITICAL" in msg
        assert "850 lines" in msg

    def test_includes_recommendations(self):
        from file_size_check import format_guidance
        msg = format_guidance("/src/app.py", 650)
        assert "SOLID" in msg
        assert "pact-architect" in msg


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

class TestMain:
    def test_skips_non_edit_write_tools(self, capsys):
        from file_size_check import main
        input_data = {"tool_name": "Read", "tool_input": {"file_path": "/src/app.py"}}
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        assert json.loads(capsys.readouterr().out.strip()) == {"suppressOutput": True}

    def test_skips_excluded_paths(self, capsys):
        from file_size_check import main
        input_data = {"tool_name": "Edit", "tool_input": {"file_path": "/project/node_modules/pkg/index.js"}}
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        assert json.loads(capsys.readouterr().out.strip()) == {"suppressOutput": True}

    def test_skips_non_source_files(self, capsys):
        from file_size_check import main
        input_data = {"tool_name": "Write", "tool_input": {"file_path": "/docs/README.md"}}
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        assert json.loads(capsys.readouterr().out.strip()) == {"suppressOutput": True}

    def test_skips_below_threshold(self, capsys, tmp_path):
        from file_size_check import main
        f = tmp_path / "small.py"
        f.write_text("\n".join(f"# line {i}" for i in range(100)))
        input_data = {"tool_name": "Edit", "tool_input": {"file_path": str(f)}}
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        assert json.loads(capsys.readouterr().out.strip()) == {"suppressOutput": True}

    def test_outputs_warning_at_threshold(self, capsys, tmp_path):
        from file_size_check import main
        f = tmp_path / "large.py"
        f.write_text("\n".join(f"# line {i}" for i in range(650)))
        input_data = {"tool_name": "Write", "tool_input": {"file_path": str(f)}}
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        output = json.loads(capsys.readouterr().out)
        assert "hookSpecificOutput" in output
        assert "additionalContext" in output["hookSpecificOutput"]
        assert "650 lines" in output["hookSpecificOutput"]["additionalContext"]

    def test_outputs_critical_at_high_threshold(self, capsys, tmp_path):
        from file_size_check import main
        f = tmp_path / "huge.py"
        f.write_text("\n".join(f"# line {i}" for i in range(850)))
        input_data = {"tool_name": "Edit", "tool_input": {"file_path": str(f)}}
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        output = json.loads(capsys.readouterr().out)
        assert "CRITICAL" in output["hookSpecificOutput"]["additionalContext"]

    def test_handles_missing_file(self, capsys):
        from file_size_check import main
        input_data = {"tool_name": "Edit", "tool_input": {"file_path": "/nonexistent/file.py"}}
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        assert json.loads(capsys.readouterr().out.strip()) == {"suppressOutput": True}

    def test_handles_missing_file_path(self, capsys):
        from file_size_check import main
        input_data = {"tool_name": "Edit", "tool_input": {}}
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        assert json.loads(capsys.readouterr().out.strip()) == {"suppressOutput": True}

    def test_handles_invalid_json(self):
        from file_size_check import main
        with patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0

    def test_handles_exception(self):
        from file_size_check import main
        with patch("sys.stdin", side_effect=Exception("boom")):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
