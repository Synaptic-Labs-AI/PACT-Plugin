"""
Tests for memory_retrieve.py — SubagentStart hook that injects memory retrieval
instructions into PACT work agents via additionalContext.

Tests cover:
1. PACT agent gets retrieval context injected
2. Non-PACT agent gets no injection (exit 0, no output)
3. Domain hint derived correctly from agent type
4. Scope-suffixed agent names still match
5. Empty/missing agent_type handled gracefully
6. main() produces correct JSON output structure
"""
import json
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


class TestIsPactWorkAgent:
    """Tests for memory_retrieve.is_pact_work_agent()."""

    def test_recognizes_backend_coder(self):
        from memory_retrieve import is_pact_work_agent
        assert is_pact_work_agent("pact-backend-coder") is True

    def test_recognizes_test_engineer(self):
        from memory_retrieve import is_pact_work_agent
        assert is_pact_work_agent("pact-test-engineer") is True

    def test_recognizes_scope_suffixed_agent(self):
        from memory_retrieve import is_pact_work_agent
        assert is_pact_work_agent("pact-backend-coder-auth-scope") is True

    def test_rejects_memory_agent(self):
        from memory_retrieve import is_pact_work_agent
        assert is_pact_work_agent("pact-memory-agent") is False

    def test_rejects_non_pact_agent(self):
        from memory_retrieve import is_pact_work_agent
        assert is_pact_work_agent("random-agent") is False

    def test_rejects_empty_string(self):
        from memory_retrieve import is_pact_work_agent
        assert is_pact_work_agent("") is False

    def test_rejects_none(self):
        from memory_retrieve import is_pact_work_agent
        assert is_pact_work_agent(None) is False

    def test_rejects_false_positive_prefix(self):
        from memory_retrieve import is_pact_work_agent
        assert is_pact_work_agent("not-pact-backend-coder") is False


class TestGetDomainHint:
    """Tests for memory_retrieve.get_domain_hint()."""

    def test_backend_coder_hint(self):
        from memory_retrieve import get_domain_hint
        assert get_domain_hint("pact-backend-coder") == "backend implementation"

    def test_frontend_coder_hint(self):
        from memory_retrieve import get_domain_hint
        assert get_domain_hint("pact-frontend-coder") == "frontend implementation UI"

    def test_architect_hint(self):
        from memory_retrieve import get_domain_hint
        assert get_domain_hint("pact-architect") == "architecture design patterns"

    def test_database_engineer_hint(self):
        from memory_retrieve import get_domain_hint
        assert get_domain_hint("pact-database-engineer") == "database schema queries migrations"

    def test_scope_suffixed_agent_gets_base_hint(self):
        from memory_retrieve import get_domain_hint
        assert get_domain_hint("pact-backend-coder-auth-scope") == "backend implementation"

    def test_unknown_agent_gets_general(self):
        from memory_retrieve import get_domain_hint
        assert get_domain_hint("unknown-agent") == "general"

    def test_empty_string_gets_general(self):
        from memory_retrieve import get_domain_hint
        assert get_domain_hint("") == "general"

    def test_none_gets_general(self):
        from memory_retrieve import get_domain_hint
        assert get_domain_hint(None) == "general"


class TestBuildRetrievalContext:
    """Tests for memory_retrieve.build_retrieval_context()."""

    def test_contains_memory_retrieval_header(self):
        from memory_retrieve import build_retrieval_context
        context = build_retrieval_context("pact-backend-coder")
        assert "## Memory Retrieval (Automatic)" in context

    def test_contains_search_command(self):
        from memory_retrieve import build_retrieval_context
        context = build_retrieval_context("pact-backend-coder")
        assert "scripts.cli search" in context

    def test_contains_domain_hint(self):
        from memory_retrieve import build_retrieval_context
        context = build_retrieval_context("pact-backend-coder")
        assert "backend implementation" in context

    def test_contains_memory_report_format(self):
        from memory_retrieve import build_retrieval_context
        context = build_retrieval_context("pact-backend-coder")
        assert "MEMORY REPORT:" in context

    def test_contains_error_handling_guidance(self):
        from memory_retrieve import build_retrieval_context
        context = build_retrieval_context("pact-backend-coder")
        assert "database not initialized" in context

    def test_reasonable_length(self):
        """Injected context should be compact (~20-30 lines)."""
        from memory_retrieve import build_retrieval_context
        context = build_retrieval_context("pact-backend-coder")
        lines = context.strip().split("\n")
        assert 15 <= len(lines) <= 35


class TestMain:
    """Tests for memory_retrieve.main() stdin/stdout/exit behavior."""

    def test_main_outputs_context_for_pact_agent(self, capsys):
        from memory_retrieve import main

        input_data = json.dumps({"agent_type": "pact-backend-coder"})

        with pytest.raises(SystemExit) as exc_info:
            with __import__("unittest.mock", fromlist=["patch"]).patch(
                "sys.stdin", io.StringIO(input_data)
            ):
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "hookSpecificOutput" in output
        assert "additionalContext" in output["hookSpecificOutput"]
        assert "Memory Retrieval" in output["hookSpecificOutput"]["additionalContext"]

    def test_main_no_output_for_non_pact_agent(self, capsys):
        from memory_retrieve import main

        input_data = json.dumps({"agent_type": "random-agent"})

        with pytest.raises(SystemExit) as exc_info:
            with __import__("unittest.mock", fromlist=["patch"]).patch(
                "sys.stdin", io.StringIO(input_data)
            ):
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == ""

    def test_main_exits_0_on_invalid_json(self):
        from memory_retrieve import main

        with pytest.raises(SystemExit) as exc_info:
            with __import__("unittest.mock", fromlist=["patch"]).patch(
                "sys.stdin", io.StringIO("not json")
            ):
                main()

        assert exc_info.value.code == 0

    def test_main_exits_0_on_empty_agent_type(self, capsys):
        from memory_retrieve import main

        input_data = json.dumps({"agent_type": ""})

        with pytest.raises(SystemExit) as exc_info:
            with __import__("unittest.mock", fromlist=["patch"]).patch(
                "sys.stdin", io.StringIO(input_data)
            ):
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == ""

    def test_main_excludes_memory_agent(self, capsys):
        from memory_retrieve import main

        input_data = json.dumps({"agent_type": "pact-memory-agent"})

        with pytest.raises(SystemExit) as exc_info:
            with __import__("unittest.mock", fromlist=["patch"]).patch(
                "sys.stdin", io.StringIO(input_data)
            ):
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == ""
