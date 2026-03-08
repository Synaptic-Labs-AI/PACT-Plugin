"""
Tests for session_init.py — SessionStart hook.

Tests cover:
generate_team_name():
1. Happy path: session_id from input_data -> "pact-{first 8 chars}"
2. Env var fallback: CLAUDE_SESSION_ID used when input_data has no session_id
3. Random fallback: random hex suffix when neither source available
4. Short session_id: less than 8 chars used as-is
5. Empty session_id: treated as falsy, falls back to env or random
6. Input_data session_id takes precedence over env var
7. Output format validation (pact- prefix, hex suffix)
8. None session_id: treated as falsy, falls back to random

Note: restore_last_session() and check_resumption_context() are tested
in test_session_resume.py (canonical location).
"""

import re
import sys
from pathlib import Path

import pytest

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


class TestGenerateTeamName:
    """Tests for generate_team_name() -- session-unique team name generation."""

    def test_uses_session_id_from_input_data(self):
        """Should return pact- followed by first 8 chars of session_id."""
        from session_init import generate_team_name

        result = generate_team_name({"session_id": "0001639f-a74f-41c4-bd0b-93d9d206e7f7"})

        assert result == "pact-0001639f"

    def test_truncates_session_id_to_8_chars(self):
        """Should use only the first 8 characters of a long session_id."""
        from session_init import generate_team_name

        result = generate_team_name({"session_id": "abcdef1234567890"})

        assert result == "pact-abcdef12"

    def test_env_var_fallback_when_no_session_id_in_input(self, monkeypatch):
        """Should fall back to CLAUDE_SESSION_ID env var when input_data lacks session_id."""
        from session_init import generate_team_name

        monkeypatch.setenv("CLAUDE_SESSION_ID", "deadbeef-1234-5678-9abc-def012345678")

        result = generate_team_name({})

        assert result == "pact-deadbeef"

    def test_env_var_fallback_when_session_id_key_missing(self, monkeypatch):
        """Should fall back to env var when session_id key is absent from input_data."""
        from session_init import generate_team_name

        monkeypatch.setenv("CLAUDE_SESSION_ID", "cafebabe-0000-1111-2222-333344445555")

        result = generate_team_name({"other_key": "value"})

        assert result == "pact-cafebabe"

    def test_random_fallback_when_no_session_id_anywhere(self, monkeypatch):
        """Should generate random hex suffix when neither source provides session_id."""
        from session_init import generate_team_name

        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)

        result = generate_team_name({})

        assert result.startswith("pact-")
        suffix = result[len("pact-"):]
        assert len(suffix) == 8
        assert re.fullmatch(r"[a-f0-9]{8}", suffix), f"Expected hex suffix, got: {suffix}"

    def test_random_fallback_produces_different_values(self, monkeypatch):
        """Random fallback should produce different names across calls (probabilistic)."""
        from session_init import generate_team_name

        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)

        results = {generate_team_name({}) for _ in range(10)}

        assert len(results) > 1, "Expected different random names across 10 calls"

    def test_short_session_id_used_as_is(self):
        """Should use the full session_id when shorter than 8 chars."""
        from session_init import generate_team_name

        result = generate_team_name({"session_id": "abc"})

        assert result == "pact-abc"

    def test_empty_session_id_falls_back_to_env(self, monkeypatch):
        """Empty string session_id should be treated as falsy, falling back to env var."""
        from session_init import generate_team_name

        monkeypatch.setenv("CLAUDE_SESSION_ID", "feedface-0000-1111-2222-333344445555")

        result = generate_team_name({"session_id": ""})

        assert result == "pact-feedface"

    def test_empty_session_id_falls_back_to_random(self, monkeypatch):
        """Empty string session_id with no env var should fall back to random."""
        from session_init import generate_team_name

        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)

        result = generate_team_name({"session_id": ""})

        assert result.startswith("pact-")
        suffix = result[len("pact-"):]
        assert len(suffix) == 8
        assert re.fullmatch(r"[a-f0-9]{8}", suffix)

    def test_input_data_takes_precedence_over_env_var(self, monkeypatch):
        """session_id from input_data should take priority over CLAUDE_SESSION_ID env var."""
        from session_init import generate_team_name

        monkeypatch.setenv("CLAUDE_SESSION_ID", "envenvev-0000-1111-2222-333344445555")

        result = generate_team_name({"session_id": "inputinp-aaaa-bbbb-cccc-ddddeeeeffff"})

        assert result == "pact-inputinp"

    def test_exactly_8_char_session_id(self):
        """Should handle a session_id that is exactly 8 characters."""
        from session_init import generate_team_name

        result = generate_team_name({"session_id": "a1b2c3d4"})

        assert result == "pact-a1b2c3d4"

    def test_none_session_id_falls_to_random(self, monkeypatch):
        """None session_id in input_data should fall back to random."""
        from session_init import generate_team_name

        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)

        result = generate_team_name({"session_id": None})

        assert result.startswith("pact-")
        suffix = result[len("pact-"):]
        assert len(suffix) == 8
        assert re.fullmatch(r"[a-f0-9]{8}", suffix)

    def test_return_type_is_string(self):
        """Should always return a string."""
        from session_init import generate_team_name

        result = generate_team_name({"session_id": "test1234"})

        assert isinstance(result, str)
