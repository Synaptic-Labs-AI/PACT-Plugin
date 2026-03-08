"""
Tests for memory_prompt.py — Stop hook that prompts memory saves after significant work.

Tests cover:
1. detect_pact_agents: agent detection in transcript
2. detect_patterns: regex pattern matching
3. analyze_transcript: combined analysis
4. should_prompt_memory: decision logic
5. format_prompt: message formatting
6. main: stdin parsing, length threshold, output format, error handling
"""
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


# ---------------------------------------------------------------------------
# detect_pact_agents
# ---------------------------------------------------------------------------

class TestDetectPactAgents:
    def test_detects_single_agent(self):
        from memory_prompt import detect_pact_agents
        result = detect_pact_agents("invoked pact-backend-coder for work")
        assert "pact-backend-coder" in result

    def test_detects_multiple_agents(self):
        from memory_prompt import detect_pact_agents
        transcript = "used pact-preparer then pact-architect and pact-backend-coder"
        result = detect_pact_agents(transcript)
        assert len(result) == 3

    def test_no_agents(self):
        from memory_prompt import detect_pact_agents
        assert detect_pact_agents("just a normal conversation") == []

    def test_case_insensitive(self):
        from memory_prompt import detect_pact_agents
        result = detect_pact_agents("PACT-BACKEND-CODER invoked")
        assert len(result) == 1

    def test_all_agents_detectable(self):
        from memory_prompt import PACT_AGENTS, detect_pact_agents
        transcript = " ".join(PACT_AGENTS)
        result = detect_pact_agents(transcript)
        assert len(result) == len(PACT_AGENTS)


# ---------------------------------------------------------------------------
# detect_patterns
# ---------------------------------------------------------------------------

class TestDetectPatterns:
    def test_matches_decision_pattern(self):
        from memory_prompt import detect_patterns, DECISION_PATTERNS
        assert detect_patterns("decided to use factory pattern", DECISION_PATTERNS) is True

    def test_matches_lesson_pattern(self):
        from memory_prompt import detect_patterns, LESSON_PATTERNS
        assert detect_patterns("lessons learned from this session", LESSON_PATTERNS) is True

    def test_matches_blocker_pattern(self):
        from memory_prompt import detect_patterns, BLOCKER_PATTERNS
        assert detect_patterns("hit a problem with auth", BLOCKER_PATTERNS) is True

    def test_no_match(self):
        from memory_prompt import detect_patterns, DECISION_PATTERNS
        assert detect_patterns("normal text with no patterns", DECISION_PATTERNS) is False

    def test_case_insensitive(self):
        from memory_prompt import detect_patterns, DECISION_PATTERNS
        assert detect_patterns("DECIDED TO use factory", DECISION_PATTERNS) is True

    def test_empty_transcript(self):
        from memory_prompt import detect_patterns, DECISION_PATTERNS
        assert detect_patterns("", DECISION_PATTERNS) is False


# ---------------------------------------------------------------------------
# analyze_transcript
# ---------------------------------------------------------------------------

class TestAnalyzeTranscript:
    def test_full_analysis(self):
        from memory_prompt import analyze_transcript
        transcript = (
            "used pact-backend-coder to implement auth. "
            "decided to use JWT tokens. "
            "learned that mocking is essential. "
            "hit a problem with CORS."
        )
        result = analyze_transcript(transcript)
        assert "pact-backend-coder" in result["agents"]
        assert result["has_decisions"] is True
        assert result["has_lessons"] is True
        assert result["has_blockers"] is True

    def test_empty_transcript(self):
        from memory_prompt import analyze_transcript
        result = analyze_transcript("")
        assert result["agents"] == []
        assert result["has_decisions"] is False
        assert result["has_lessons"] is False
        assert result["has_blockers"] is False


# ---------------------------------------------------------------------------
# should_prompt_memory
# ---------------------------------------------------------------------------

class TestShouldPromptMemory:
    def test_agents_triggers_prompt(self):
        from memory_prompt import should_prompt_memory
        assert should_prompt_memory({"agents": ["pact-backend-coder"], "has_decisions": False, "has_lessons": False, "has_blockers": False}) is True

    def test_decisions_triggers_prompt(self):
        from memory_prompt import should_prompt_memory
        assert should_prompt_memory({"agents": [], "has_decisions": True, "has_lessons": False, "has_blockers": False}) is True

    def test_lessons_triggers_prompt(self):
        from memory_prompt import should_prompt_memory
        assert should_prompt_memory({"agents": [], "has_decisions": False, "has_lessons": True, "has_blockers": False}) is True

    def test_blockers_triggers_prompt(self):
        from memory_prompt import should_prompt_memory
        assert should_prompt_memory({"agents": [], "has_decisions": False, "has_lessons": False, "has_blockers": True}) is True

    def test_nothing_returns_false(self):
        from memory_prompt import should_prompt_memory
        assert should_prompt_memory({"agents": [], "has_decisions": False, "has_lessons": False, "has_blockers": False}) is False


# ---------------------------------------------------------------------------
# format_prompt
# ---------------------------------------------------------------------------

class TestFormatPrompt:
    def test_includes_mandatory_language(self):
        from memory_prompt import format_prompt
        msg = format_prompt({"agents": ["pact-backend-coder"], "has_decisions": False, "has_lessons": False, "has_blockers": False})
        assert "MANDATORY" in msg
        assert "pact-memory-agent" in msg

    def test_includes_agent_list(self):
        from memory_prompt import format_prompt
        msg = format_prompt({"agents": ["pact-backend-coder", "pact-test-engineer"], "has_decisions": False, "has_lessons": False, "has_blockers": False})
        assert "pact-backend-coder" in msg
        assert "pact-test-engineer" in msg

    def test_includes_decision_line(self):
        from memory_prompt import format_prompt
        msg = format_prompt({"agents": [], "has_decisions": True, "has_lessons": False, "has_blockers": False})
        assert "Decisions" in msg

    def test_includes_lessons_line(self):
        from memory_prompt import format_prompt
        msg = format_prompt({"agents": [], "has_decisions": False, "has_lessons": True, "has_blockers": False})
        assert "Lessons" in msg

    def test_includes_blockers_line(self):
        from memory_prompt import format_prompt
        msg = format_prompt({"agents": [], "has_decisions": False, "has_lessons": False, "has_blockers": True})
        assert "Blockers" in msg

    def test_includes_not_optional_warning(self):
        from memory_prompt import format_prompt
        msg = format_prompt({"agents": ["x"], "has_decisions": False, "has_lessons": False, "has_blockers": False})
        assert "NOT optional" in msg


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

class TestMain:
    def test_skips_short_transcript(self, capsys):
        from memory_prompt import main
        input_data = {"transcript": "short"}
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        assert capsys.readouterr().out == ""

    def test_skips_empty_transcript(self, capsys):
        from memory_prompt import main
        input_data = {"transcript": ""}
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        assert capsys.readouterr().out == ""

    def test_outputs_prompt_for_significant_work(self, capsys):
        from memory_prompt import main
        transcript = "x" * 500 + " used pact-backend-coder to implement feature"
        input_data = {"transcript": transcript}
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        output = json.loads(capsys.readouterr().out)
        assert "systemMessage" in output
        assert "MANDATORY" in output["systemMessage"]

    def test_no_output_for_no_patterns(self, capsys):
        from memory_prompt import main
        transcript = "x" * 600  # Long but no patterns
        input_data = {"transcript": transcript}
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        assert capsys.readouterr().out == ""

    def test_handles_invalid_json(self):
        from memory_prompt import main
        with patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0

    def test_handles_exception(self):
        from memory_prompt import main
        with patch("sys.stdin", side_effect=Exception("boom")):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0

    def test_threshold_exactly_500(self, capsys):
        from memory_prompt import main
        transcript = "x" * 499 + " pact-backend-coder"
        input_data = {"transcript": transcript}
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        # 499 + len(" pact-backend-coder") = 518 > 500, should output
        output = json.loads(capsys.readouterr().out)
        assert "systemMessage" in output
