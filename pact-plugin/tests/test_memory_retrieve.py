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
7. All PACT_WORK_AGENTS produce correct domain-specific hints
8. Malformed stdin JSON handling
9. Output JSON structure matches hookSpecificOutput.additionalContext format
10. CLI search command in injected context is syntactically correct
11. MEMORY REPORT format completeness
"""
import json
import io
import sys
from pathlib import Path
from unittest.mock import patch

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


# =============================================================================
# Comprehensive tests for all agent types
# =============================================================================

class TestAllAgentTypesGetContext:
    """Every PACT work agent should receive retrieval context with correct domain hint."""

    @pytest.mark.parametrize("agent_type,expected_hint", [
        ("pact-preparer", "requirements preparation research"),
        ("pact-architect", "architecture design patterns"),
        ("pact-backend-coder", "backend implementation"),
        ("pact-frontend-coder", "frontend implementation UI"),
        ("pact-database-engineer", "database schema queries migrations"),
        ("pact-devops-engineer", "devops CI/CD infrastructure"),
        ("pact-n8n", "n8n workflow automation"),
        ("pact-test-engineer", "testing quality assurance"),
        ("pact-security-engineer", "security review vulnerabilities"),
        ("pact-qa-engineer", "QA runtime verification"),
    ])
    def test_agent_recognized_and_gets_hint(self, agent_type, expected_hint):
        from memory_retrieve import is_pact_work_agent, get_domain_hint
        assert is_pact_work_agent(agent_type) is True
        assert get_domain_hint(agent_type) == expected_hint

    @pytest.mark.parametrize("agent_type,expected_hint", [
        ("pact-preparer", "requirements preparation research"),
        ("pact-architect", "architecture design patterns"),
        ("pact-backend-coder", "backend implementation"),
        ("pact-frontend-coder", "frontend implementation UI"),
        ("pact-database-engineer", "database schema queries migrations"),
        ("pact-devops-engineer", "devops CI/CD infrastructure"),
        ("pact-n8n", "n8n workflow automation"),
        ("pact-test-engineer", "testing quality assurance"),
        ("pact-security-engineer", "security review vulnerabilities"),
        ("pact-qa-engineer", "QA runtime verification"),
    ])
    def test_main_injects_context_for_all_agents(self, agent_type, expected_hint, capsys):
        from memory_retrieve import main

        input_data = json.dumps({"agent_type": agent_type})

        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.stdin", io.StringIO(input_data)):
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        context = output["hookSpecificOutput"]["additionalContext"]
        assert expected_hint in context


class TestScopeSuffixedAgents:
    """Agents with scope suffixes (e.g., pact-backend-coder-auth-scope) must match."""

    @pytest.mark.parametrize("agent_type", [
        "pact-backend-coder-auth-scope",
        "pact-frontend-coder-dashboard",
        "pact-database-engineer-migration",
        "pact-test-engineer-unit",
        "pact-preparer-research",
        "pact-architect-review",
        "pact-devops-engineer-ci",
        "pact-n8n-workflow-builder",
        "pact-security-engineer-audit",
        "pact-qa-engineer-smoke",
    ])
    def test_scope_suffixed_agent_recognized(self, agent_type):
        from memory_retrieve import is_pact_work_agent
        assert is_pact_work_agent(agent_type) is True

    @pytest.mark.parametrize("agent_type,expected_hint", [
        ("pact-backend-coder-auth-scope", "backend implementation"),
        ("pact-frontend-coder-dashboard", "frontend implementation UI"),
        ("pact-database-engineer-migration", "database schema queries migrations"),
        ("pact-n8n-workflow-builder", "n8n workflow automation"),
    ])
    def test_scope_suffixed_agent_gets_base_domain_hint(self, agent_type, expected_hint):
        from memory_retrieve import get_domain_hint
        assert get_domain_hint(agent_type) == expected_hint


class TestCaseInsensitivity:
    """Agent type matching should be case-insensitive."""

    @pytest.mark.parametrize("agent_type", [
        "PACT-BACKEND-CODER",
        "Pact-Backend-Coder",
        "pact-BACKEND-coder",
        "PACT-TEST-ENGINEER",
    ])
    def test_case_insensitive_matching(self, agent_type):
        from memory_retrieve import is_pact_work_agent
        assert is_pact_work_agent(agent_type) is True


class TestMalformedStdinHandling:
    """main() should handle all forms of malformed stdin gracefully (exit 0)."""

    def test_empty_stdin(self):
        from memory_retrieve import main

        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.stdin", io.StringIO("")):
                main()
        assert exc_info.value.code == 0

    def test_empty_json_object(self, capsys):
        from memory_retrieve import main

        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.stdin", io.StringIO("{}")):
                main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == ""

    def test_json_array_instead_of_object(self, capsys):
        """JSON array input should exit 0 gracefully (non-dict guard)."""
        from memory_retrieve import main

        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.stdin", io.StringIO("[]")):
                main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == ""

    def test_missing_agent_type_field(self, capsys):
        from memory_retrieve import main

        input_data = json.dumps({"agent_name": "some-name", "other_field": 123})
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.stdin", io.StringIO(input_data)):
                main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == ""

    def test_agent_type_is_integer(self, capsys):
        """Non-string agent_type should exit 0 gracefully (isinstance guard)."""
        from memory_retrieve import main

        input_data = json.dumps({"agent_type": 42})
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.stdin", io.StringIO(input_data)):
                main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == ""

    def test_agent_type_is_null(self, capsys):
        from memory_retrieve import main

        input_data = json.dumps({"agent_type": None})
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.stdin", io.StringIO(input_data)):
                main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == ""


class TestOutputJsonStructure:
    """Verify the output JSON matches the hookSpecificOutput.additionalContext contract."""

    def test_output_has_exact_structure(self, capsys):
        from memory_retrieve import main

        input_data = json.dumps({"agent_type": "pact-backend-coder"})

        with pytest.raises(SystemExit):
            with patch("sys.stdin", io.StringIO(input_data)):
                main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        # Must have exactly hookSpecificOutput at top level
        assert set(output.keys()) == {"hookSpecificOutput"}
        assert set(output["hookSpecificOutput"].keys()) == {"additionalContext"}
        assert isinstance(output["hookSpecificOutput"]["additionalContext"], str)

    def test_output_is_valid_single_line_json(self, capsys):
        """Output must be a single line of JSON (no embedded newlines in JSON structure)."""
        from memory_retrieve import main

        input_data = json.dumps({"agent_type": "pact-backend-coder"})

        with pytest.raises(SystemExit):
            with patch("sys.stdin", io.StringIO(input_data)):
                main()

        captured = capsys.readouterr()
        # The JSON output itself should be on one line (the context value contains newlines,
        # but the JSON structure serializes them as \n)
        json_lines = captured.out.strip().split("\n")
        assert len(json_lines) == 1
        # Verify it round-trips
        parsed = json.loads(json_lines[0])
        assert "hookSpecificOutput" in parsed


class TestCliSearchCommandInContext:
    """The injected CLI search command must be syntactically correct."""

    def test_contains_cd_and_python_command(self):
        from memory_retrieve import build_retrieval_context
        context = build_retrieval_context("pact-backend-coder")
        assert "cd ~/.claude/pact-memory" in context
        assert "python -m scripts.cli search" in context

    def test_command_has_placeholder_and_domain(self):
        from memory_retrieve import build_retrieval_context
        context = build_retrieval_context("pact-backend-coder")
        # Must contain the user-fillable placeholder
        assert "{your task topic}" in context
        # Must contain the domain hint in the command
        assert "backend implementation" in context

    def test_command_is_in_code_block(self):
        from memory_retrieve import build_retrieval_context
        context = build_retrieval_context("pact-backend-coder")
        # Command should be inside a bash code fence
        assert "```bash" in context
        assert "```" in context


class TestMemoryReportFormat:
    """The injected MEMORY REPORT template must be complete and consistent."""

    def test_report_has_searched_for_field(self):
        from memory_retrieve import build_retrieval_context
        context = build_retrieval_context("pact-backend-coder")
        assert "Searched for:" in context

    def test_report_has_found_field(self):
        from memory_retrieve import build_retrieval_context
        context = build_retrieval_context("pact-backend-coder")
        assert "Found:" in context

    def test_report_has_key_context_field(self):
        from memory_retrieve import build_retrieval_context
        context = build_retrieval_context("pact-backend-coder")
        assert "Key context:" in context

    def test_report_has_error_fallback(self):
        from memory_retrieve import build_retrieval_context
        context = build_retrieval_context("pact-backend-coder")
        # Error handling section
        assert "database not initialized" in context
        assert "starting fresh" in context

    def test_report_has_domain_hint_footer(self):
        from memory_retrieve import build_retrieval_context
        context = build_retrieval_context("pact-test-engineer")
        assert "Domain hint for your role: testing quality assurance" in context


class TestNonPactAgentRejection:
    """Agents that should NOT receive context must be properly excluded."""

    @pytest.mark.parametrize("agent_type", [
        "pact-memory-agent",
        "random-agent",
        "explorer",
        "team-lead",
        "pact-",
        "pact",
        "not-pact-backend-coder",
        "backend-coder",  # missing pact- prefix
    ])
    def test_non_pact_agent_rejected(self, agent_type):
        from memory_retrieve import is_pact_work_agent
        assert is_pact_work_agent(agent_type) is False
