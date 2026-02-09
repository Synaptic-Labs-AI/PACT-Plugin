"""
Tests for the transcript_parser module.

Tests JSONL parsing, turn extraction, and helper functions.
"""

import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from refresh.transcript_parser import (
    Turn,
    ToolCall,
    parse_line,
    parse_transcript,
    read_last_n_lines,
    find_turns_by_type,
    find_turns_with_content,
    find_last_user_message,
    find_task_calls_to_agent,
    find_send_message_calls,
    find_team_management_calls,
    find_trigger_turn_index,
)
from refresh.constants import LARGE_FILE_THRESHOLD_BYTES


class TestTurnDataclass:
    """Tests for the Turn dataclass and its methods."""

    def test_turn_is_user(self):
        """Test Turn.is_user property."""
        user_turn = Turn(turn_type="user", content="Hello")
        assert user_turn.is_user is True

        assistant_turn = Turn(turn_type="assistant", content="Hi")
        assert assistant_turn.is_user is False

    def test_turn_is_assistant(self):
        """Test Turn.is_assistant property."""
        assistant_turn = Turn(turn_type="assistant", content="Hello")
        assert assistant_turn.is_assistant is True

        user_turn = Turn(turn_type="user", content="Hi")
        assert user_turn.is_assistant is False

    def test_turn_has_tool_calls(self):
        """Test Turn.has_tool_calls property."""
        turn_with_tools = Turn(
            turn_type="assistant",
            tool_calls=[ToolCall(name="Read", input_data={"file_path": "/test"})],
        )
        assert turn_with_tools.has_tool_calls is True

        turn_without_tools = Turn(turn_type="assistant", content="Hello")
        assert turn_without_tools.has_tool_calls is False

    def test_turn_get_tool_call(self):
        """Test Turn.get_tool_call method."""
        read_call = ToolCall(name="Read", input_data={"file_path": "/test"})
        write_call = ToolCall(name="Write", input_data={"file_path": "/out"})
        turn = Turn(turn_type="assistant", tool_calls=[read_call, write_call])

        assert turn.get_tool_call("Read") == read_call
        assert turn.get_tool_call("Write") == write_call
        assert turn.get_tool_call("NonExistent") is None

    def test_turn_has_task_to_pact_agent_subagent_model(self):
        """Test Turn.has_task_to_pact_agent with subagent_type dispatch."""
        pact_task = ToolCall(
            name="Task",
            input_data={"subagent_type": "pact-backend-coder", "prompt": "test"},
        )
        turn_with_pact = Turn(turn_type="assistant", tool_calls=[pact_task])
        assert turn_with_pact.has_task_to_pact_agent() is True

        non_pact_task = ToolCall(
            name="Task",
            input_data={"subagent_type": "other-agent", "prompt": "test"},
        )
        turn_non_pact = Turn(turn_type="assistant", tool_calls=[non_pact_task])
        assert turn_non_pact.has_task_to_pact_agent() is False

        turn_no_task = Turn(turn_type="assistant", content="No tools")
        assert turn_no_task.has_task_to_pact_agent() is False

    def test_turn_has_task_to_pact_agent_teams_model(self):
        """Test Turn.has_task_to_pact_agent with Agent Teams team_name dispatch."""
        pact_team_task = ToolCall(
            name="Task",
            input_data={"team_name": "pact-auth-feature", "name": "backend-1", "prompt": "test"},
        )
        turn_with_pact_team = Turn(turn_type="assistant", tool_calls=[pact_team_task])
        assert turn_with_pact_team.has_task_to_pact_agent() is True

    def test_turn_has_task_to_pact_agent_teams_non_pact_team(self):
        """Test Turn.has_task_to_pact_agent returns False for non-PACT team names."""
        non_pact_team_task = ToolCall(
            name="Task",
            input_data={"team_name": "other-team", "name": "agent-1", "prompt": "test"},
        )
        turn_non_pact_team = Turn(turn_type="assistant", tool_calls=[non_pact_team_task])
        assert turn_non_pact_team.has_task_to_pact_agent() is False

    def test_turn_has_task_to_pact_agent_teams_case_insensitive(self):
        """Test Turn.has_task_to_pact_agent is case insensitive on team_name."""
        upper_pact_task = ToolCall(
            name="Task",
            input_data={"team_name": "PACT-Feature-Team", "name": "agent-1", "prompt": "test"},
        )
        turn = Turn(turn_type="assistant", tool_calls=[upper_pact_task])
        assert turn.has_task_to_pact_agent() is True

    def test_turn_has_send_message(self):
        """Test Turn.has_send_message detects SendMessage calls."""
        send_msg = ToolCall(
            name="SendMessage",
            input_data={"type": "message", "recipient": "agent-1", "content": "Done"},
        )
        turn_with_msg = Turn(turn_type="assistant", tool_calls=[send_msg])
        assert turn_with_msg.has_send_message() is True

        turn_without_msg = Turn(turn_type="assistant", content="No tools")
        assert turn_without_msg.has_send_message() is False

    def test_turn_has_send_message_mixed_tools(self):
        """Test Turn.has_send_message with mixed tool calls."""
        read_call = ToolCall(name="Read", input_data={"file_path": "/test"})
        send_msg = ToolCall(name="SendMessage", input_data={"content": "Done"})
        turn = Turn(turn_type="assistant", tool_calls=[read_call, send_msg])
        assert turn.has_send_message() is True

    def test_turn_has_send_message_no_match_similar_names(self):
        """Test Turn.has_send_message does not match similar tool names."""
        other_tool = ToolCall(name="SendEmail", input_data={"to": "user"})
        turn = Turn(turn_type="assistant", tool_calls=[other_tool])
        assert turn.has_send_message() is False

    def test_turn_has_team_management_create(self):
        """Test Turn.has_team_management detects TeamCreate calls."""
        team_create = ToolCall(
            name="TeamCreate",
            input_data={"name": "pact-feature"},
        )
        turn = Turn(turn_type="assistant", tool_calls=[team_create])
        assert turn.has_team_management() is True

    def test_turn_has_team_management_delete(self):
        """Test Turn.has_team_management detects TeamDelete calls."""
        team_delete = ToolCall(
            name="TeamDelete",
            input_data={"name": "pact-feature"},
        )
        turn = Turn(turn_type="assistant", tool_calls=[team_delete])
        assert turn.has_team_management() is True

    def test_turn_has_team_management_no_match(self):
        """Test Turn.has_team_management returns False without team management calls."""
        task_call = ToolCall(name="Task", input_data={"subagent_type": "pact-backend"})
        turn = Turn(turn_type="assistant", tool_calls=[task_call])
        assert turn.has_team_management() is False

        empty_turn = Turn(turn_type="assistant", content="No tools")
        assert empty_turn.has_team_management() is False

    def test_turn_has_team_management_no_match_similar_names(self):
        """Test Turn.has_team_management does not match similar names."""
        other_tool = ToolCall(name="TeamUpdate", input_data={"name": "my-team"})
        turn = Turn(turn_type="assistant", tool_calls=[other_tool])
        assert turn.has_team_management() is False


class TestParseLine:
    """Tests for the parse_line function."""

    def test_parse_valid_user_message(self):
        """Test parsing a valid user message."""
        line = json.dumps({
            "type": "user",
            "message": {"role": "user", "content": "Hello"},
            "timestamp": "2025-01-22T12:00:00Z",
        })

        turn = parse_line(line, 1)

        assert turn is not None
        assert turn.turn_type == "user"
        assert turn.content == "Hello"
        assert turn.timestamp == "2025-01-22T12:00:00Z"
        assert turn.line_number == 1

    def test_parse_valid_assistant_message_string_content(self):
        """Test parsing assistant message with string content."""
        line = json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "content": "Hi there"},
            "timestamp": "2025-01-22T12:00:05Z",
        })

        turn = parse_line(line, 2)

        assert turn is not None
        assert turn.turn_type == "assistant"
        assert turn.content == "Hi there"

    def test_parse_assistant_message_with_content_blocks(self):
        """Test parsing assistant message with list of content blocks."""
        line = json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Starting task..."},
                    {"type": "text", "text": "More text."},
                ],
            },
        })

        turn = parse_line(line, 1)

        assert turn is not None
        assert "Starting task..." in turn.content
        assert "More text." in turn.content

    def test_parse_assistant_message_with_tool_use(self):
        """Test parsing assistant message containing tool_use blocks."""
        line = json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Reading file..."},
                    {
                        "type": "tool_use",
                        "id": "tool-123",
                        "name": "Read",
                        "input": {"file_path": "/test/file.py"},
                    },
                ],
            },
        })

        turn = parse_line(line, 1)

        assert turn is not None
        assert "Reading file..." in turn.content
        assert len(turn.tool_calls) == 1
        assert turn.tool_calls[0].name == "Read"
        assert turn.tool_calls[0].input_data == {"file_path": "/test/file.py"}
        assert turn.tool_calls[0].tool_use_id == "tool-123"

    def test_parse_empty_line(self):
        """Test that empty lines return None."""
        assert parse_line("", 1) is None
        assert parse_line("   ", 1) is None
        assert parse_line("\n", 1) is None

    def test_parse_malformed_json(self, capsys):
        """Test that malformed JSON logs warning and returns None."""
        result = parse_line("{ invalid json }", 5)

        assert result is None
        captured = capsys.readouterr()
        assert "Malformed JSON at line 5" in captured.err

    def test_parse_missing_type_field(self):
        """Test that lines without 'type' field return None."""
        line = json.dumps({"message": {"content": "test"}})
        assert parse_line(line, 1) is None

    def test_parse_preserves_raw_data(self):
        """Test that raw_data field contains original parsed JSON."""
        original = {
            "type": "user",
            "message": {"role": "user", "content": "test"},
            "custom_field": "preserved",
        }
        line = json.dumps(original)

        turn = parse_line(line, 1)

        assert turn.raw_data == original


class TestReadLastNLines:
    """Tests for the read_last_n_lines function."""

    def test_read_small_file(self, tmp_path: Path):
        """Test reading a file smaller than limit."""
        file_path = tmp_path / "small.txt"
        content = "line1\nline2\nline3"
        file_path.write_text(content)

        lines, total = read_last_n_lines(file_path, 10)

        assert len(lines) == 3
        assert "line1" in lines[0]
        assert "line3" in lines[2]
        assert total == 3

    def test_read_large_file_truncated(self, tmp_path: Path):
        """Test reading only last N lines from large file."""
        file_path = tmp_path / "large.txt"
        all_lines = [f"line{i}" for i in range(100)]
        file_path.write_text("\n".join(all_lines))

        lines, total = read_last_n_lines(file_path, 10)

        assert len(lines) == 10
        # Should get lines 90-99
        assert "line90" in lines[0]
        assert "line99" in lines[9]
        assert total == 100

    def test_read_nonexistent_file(self, capsys):
        """Test handling of nonexistent file."""
        lines, total = read_last_n_lines(Path("/nonexistent/file.txt"), 10)

        assert lines == []
        assert total == 0
        captured = capsys.readouterr()
        assert "Could not read transcript" in captured.err


class TestParseTranscript:
    """Tests for the parse_transcript function."""

    def test_parse_valid_transcript(self, tmp_transcript, peer_review_mid_workflow_transcript):
        """Test parsing a valid transcript file."""
        path = tmp_transcript(peer_review_mid_workflow_transcript)

        turns = parse_transcript(path)

        assert len(turns) > 0
        # First turn should be user with /PACT:peer-review
        user_turns = [t for t in turns if t.is_user]
        assert any("/PACT:peer-review" in t.content for t in user_turns)

    def test_parse_nonexistent_file(self, capsys):
        """Test parsing nonexistent file returns empty list."""
        turns = parse_transcript(Path("/nonexistent/session.jsonl"))

        assert turns == []
        captured = capsys.readouterr()
        assert "Transcript not found" in captured.err

    def test_parse_transcript_respects_max_lines(self, tmp_path: Path):
        """Test that max_lines parameter limits lines read."""
        # Create large transcript
        file_path = tmp_path / "large.jsonl"
        lines = []
        for i in range(100):
            lines.append(json.dumps({
                "type": "user",
                "message": {"content": f"message {i}"},
            }))
        file_path.write_text("\n".join(lines))

        turns = parse_transcript(file_path, max_lines=20)

        assert len(turns) <= 20

    def test_parse_transcript_skips_malformed_lines(self, tmp_path: Path):
        """Test that malformed lines are skipped gracefully."""
        file_path = tmp_path / "mixed.jsonl"
        content = [
            json.dumps({"type": "user", "message": {"content": "valid1"}}),
            "invalid json",
            json.dumps({"type": "user", "message": {"content": "valid2"}}),
        ]
        file_path.write_text("\n".join(content))

        turns = parse_transcript(file_path)

        assert len(turns) == 2


class TestFindFunctions:
    """Tests for find_* helper functions."""

    @pytest.fixture
    def sample_turns(self) -> list[Turn]:
        """Create sample turns for testing find functions."""
        return [
            Turn(turn_type="user", content="Hello", line_number=1),
            Turn(turn_type="assistant", content="Hi there", line_number=2),
            Turn(turn_type="user", content="/PACT:peer-review", line_number=3),
            Turn(
                turn_type="assistant",
                content="Starting workflow",
                tool_calls=[
                    ToolCall(
                        name="Task",
                        input_data={"subagent_type": "pact-architect"},
                    ),
                ],
                line_number=4,
            ),
            Turn(turn_type="assistant", content="Completed", line_number=5),
        ]

    def test_find_turns_by_type(self, sample_turns):
        """Test filtering turns by type."""
        user_turns = find_turns_by_type(sample_turns, "user")
        assert len(user_turns) == 2

        assistant_turns = find_turns_by_type(sample_turns, "assistant")
        assert len(assistant_turns) == 3

    def test_find_turns_with_content(self, sample_turns):
        """Test finding turns containing specific content."""
        pact_turns = find_turns_with_content(sample_turns, "PACT:")
        assert len(pact_turns) == 1
        assert "/PACT:peer-review" in pact_turns[0].content

        # Case insensitive
        pact_turns_lower = find_turns_with_content(sample_turns, "pact:")
        assert len(pact_turns_lower) == 1

    def test_find_last_user_message(self, sample_turns):
        """Test finding the most recent user message."""
        last_user = find_last_user_message(sample_turns)

        assert last_user is not None
        assert last_user.content == "/PACT:peer-review"
        assert last_user.line_number == 3

    def test_find_last_user_message_empty(self):
        """Test finding user message in empty list."""
        assert find_last_user_message([]) is None

    def test_find_task_calls_to_agent(self, sample_turns):
        """Test finding Task calls to specific agents."""
        results = find_task_calls_to_agent(sample_turns, "pact-")

        assert len(results) == 1
        turn, tool_call = results[0]
        assert turn.line_number == 4
        assert tool_call.name == "Task"
        assert "pact-architect" in tool_call.input_data.get("subagent_type", "")

    def test_find_task_calls_no_matches(self, sample_turns):
        """Test finding Task calls with no matches."""
        results = find_task_calls_to_agent(sample_turns, "nonexistent-")
        assert results == []


class TestFindSendMessageCalls:
    """Tests for find_send_message_calls function (Agent Teams)."""

    def test_find_send_message_calls_basic(self):
        """Test finding SendMessage calls in turns."""
        turns = [
            Turn(turn_type="user", content="Hello"),
            Turn(
                turn_type="assistant",
                content="Sending message",
                tool_calls=[
                    ToolCall(name="SendMessage", input_data={"recipient": "agent-1", "content": "Done"}),
                ],
            ),
            Turn(turn_type="assistant", content="Finished"),
        ]

        results = find_send_message_calls(turns)

        assert len(results) == 1
        turn, tc = results[0]
        assert tc.name == "SendMessage"
        assert tc.input_data["recipient"] == "agent-1"

    def test_find_send_message_calls_multiple(self):
        """Test finding multiple SendMessage calls across turns."""
        turns = [
            Turn(
                turn_type="assistant",
                tool_calls=[
                    ToolCall(name="SendMessage", input_data={"recipient": "agent-1"}),
                ],
            ),
            Turn(
                turn_type="assistant",
                tool_calls=[
                    ToolCall(name="SendMessage", input_data={"recipient": "agent-2"}),
                    ToolCall(name="SendMessage", input_data={"recipient": "agent-3"}),
                ],
            ),
        ]

        results = find_send_message_calls(turns)

        assert len(results) == 3

    def test_find_send_message_calls_none_found(self):
        """Test finding SendMessage when none exist."""
        turns = [
            Turn(turn_type="user", content="Hello"),
            Turn(
                turn_type="assistant",
                tool_calls=[ToolCall(name="Task", input_data={"subagent_type": "pact-backend"})],
            ),
        ]

        results = find_send_message_calls(turns)

        assert results == []

    def test_find_send_message_calls_empty_turns(self):
        """Test finding SendMessage with empty turns list."""
        assert find_send_message_calls([]) == []

    def test_find_send_message_calls_mixed_tools(self):
        """Test finding SendMessage among mixed tool calls."""
        turns = [
            Turn(
                turn_type="assistant",
                tool_calls=[
                    ToolCall(name="Read", input_data={"file_path": "/test"}),
                    ToolCall(name="SendMessage", input_data={"recipient": "lead"}),
                    ToolCall(name="Write", input_data={"file_path": "/out"}),
                ],
            ),
        ]

        results = find_send_message_calls(turns)

        assert len(results) == 1
        assert results[0][1].input_data["recipient"] == "lead"


class TestFindTeamManagementCalls:
    """Tests for find_team_management_calls function (Agent Teams)."""

    def test_find_team_create(self):
        """Test finding TeamCreate calls."""
        turns = [
            Turn(
                turn_type="assistant",
                tool_calls=[
                    ToolCall(name="TeamCreate", input_data={"name": "pact-feature"}),
                ],
            ),
        ]

        results = find_team_management_calls(turns)

        assert len(results) == 1
        assert results[0][1].name == "TeamCreate"

    def test_find_team_delete(self):
        """Test finding TeamDelete calls."""
        turns = [
            Turn(
                turn_type="assistant",
                tool_calls=[
                    ToolCall(name="TeamDelete", input_data={"name": "pact-feature"}),
                ],
            ),
        ]

        results = find_team_management_calls(turns)

        assert len(results) == 1
        assert results[0][1].name == "TeamDelete"

    def test_find_both_create_and_delete(self):
        """Test finding both TeamCreate and TeamDelete across turns."""
        turns = [
            Turn(
                turn_type="assistant",
                tool_calls=[
                    ToolCall(name="TeamCreate", input_data={"name": "pact-feature"}),
                ],
            ),
            Turn(turn_type="assistant", content="Work done"),
            Turn(
                turn_type="assistant",
                tool_calls=[
                    ToolCall(name="TeamDelete", input_data={"name": "pact-feature"}),
                ],
            ),
        ]

        results = find_team_management_calls(turns)

        assert len(results) == 2
        assert results[0][1].name == "TeamCreate"
        assert results[1][1].name == "TeamDelete"

    def test_find_team_management_none_found(self):
        """Test finding team management when none exist."""
        turns = [
            Turn(
                turn_type="assistant",
                tool_calls=[ToolCall(name="Task", input_data={"subagent_type": "pact-backend"})],
            ),
        ]

        results = find_team_management_calls(turns)

        assert results == []

    def test_find_team_management_empty_turns(self):
        """Test finding team management with empty turns list."""
        assert find_team_management_calls([]) == []

    def test_find_team_management_excludes_other_tools(self):
        """Test that non-team-management tools are excluded."""
        turns = [
            Turn(
                turn_type="assistant",
                tool_calls=[
                    ToolCall(name="SendMessage", input_data={"content": "test"}),
                    ToolCall(name="Task", input_data={"team_name": "my-team"}),
                    ToolCall(name="TeamCreate", input_data={"name": "pact-feature"}),
                ],
            ),
        ]

        results = find_team_management_calls(turns)

        assert len(results) == 1
        assert results[0][1].name == "TeamCreate"


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_transcript(self, tmp_path: Path):
        """Test handling empty transcript file."""
        file_path = tmp_path / "empty.jsonl"
        file_path.write_text("")

        turns = parse_transcript(file_path)

        assert turns == []

    def test_transcript_with_only_empty_lines(self, tmp_path: Path):
        """Test handling transcript with only whitespace/empty lines."""
        file_path = tmp_path / "whitespace.jsonl"
        file_path.write_text("\n\n   \n\n")

        turns = parse_transcript(file_path)

        assert turns == []

    def test_unicode_content(self, tmp_path: Path):
        """Test handling unicode content in messages."""
        file_path = tmp_path / "unicode.jsonl"
        content = json.dumps({
            "type": "user",
            "message": {"content": "Hello! Here's emoji: and Japanese: "},
        })
        file_path.write_text(content, encoding="utf-8")

        turns = parse_transcript(file_path)

        assert len(turns) == 1
        assert "" in turns[0].content
        assert "" in turns[0].content

    def test_very_long_content(self, tmp_path: Path):
        """Test handling very long message content."""
        file_path = tmp_path / "long.jsonl"
        long_content = "x" * 10000
        content = json.dumps({
            "type": "user",
            "message": {"content": long_content},
        })
        file_path.write_text(content)

        turns = parse_transcript(file_path)

        assert len(turns) == 1
        assert len(turns[0].content) == 10000

    def test_nested_tool_calls(self, tmp_path: Path):
        """Test parsing multiple tool calls in one message."""
        file_path = tmp_path / "multi_tool.jsonl"
        content = json.dumps({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Multiple tools:"},
                    {"type": "tool_use", "name": "Read", "input": {"path": "/a"}, "id": "t1"},
                    {"type": "tool_use", "name": "Write", "input": {"path": "/b"}, "id": "t2"},
                    {"type": "tool_use", "name": "Task", "input": {"subagent_type": "pact-test-engineer"}, "id": "t3"},
                ],
            },
        })
        file_path.write_text(content)

        turns = parse_transcript(file_path)

        assert len(turns) == 1
        assert len(turns[0].tool_calls) == 3
        assert turns[0].tool_calls[0].name == "Read"
        assert turns[0].tool_calls[1].name == "Write"
        assert turns[0].tool_calls[2].name == "Task"


class TestFindTriggerTurnIndex:
    """Tests for find_trigger_turn_index function."""

    def test_find_existing_turn(self):
        """Test finding index when turn exists."""
        turns = [
            Turn(turn_type="user", content="Hello", line_number=1),
            Turn(turn_type="assistant", content="Hi", line_number=2),
            Turn(turn_type="user", content="/PACT:peer-review", line_number=5),
            Turn(turn_type="assistant", content="Starting...", line_number=6),
        ]

        index = find_trigger_turn_index(turns, 5)
        assert index == 2  # The turn with line_number=5 is at index 2

    def test_return_zero_when_not_found(self):
        """Test returning 0 when turn not found."""
        turns = [
            Turn(turn_type="user", content="Hello", line_number=1),
            Turn(turn_type="assistant", content="Hi", line_number=2),
        ]

        index = find_trigger_turn_index(turns, 99)
        assert index == 0

    def test_empty_turns_list(self):
        """Test handling empty turns list."""
        index = find_trigger_turn_index([], 5)
        assert index == 0

    def test_first_turn_found(self):
        """Test finding the first turn."""
        turns = [
            Turn(turn_type="user", content="First", line_number=1),
            Turn(turn_type="assistant", content="Second", line_number=2),
        ]

        index = find_trigger_turn_index(turns, 1)
        assert index == 0

    def test_last_turn_found(self):
        """Test finding the last turn."""
        turns = [
            Turn(turn_type="user", content="First", line_number=1),
            Turn(turn_type="assistant", content="Last", line_number=10),
        ]

        index = find_trigger_turn_index(turns, 10)
        assert index == 1

    def test_duplicate_line_numbers_returns_first(self):
        """Test handling duplicate line numbers returns first match."""
        turns = [
            Turn(turn_type="user", content="First at 5", line_number=5),
            Turn(turn_type="assistant", content="Also at 5", line_number=5),
            Turn(turn_type="user", content="At 6", line_number=6),
        ]

        # Should return the first turn with line_number=5
        index = find_trigger_turn_index(turns, 5)
        assert index == 0
        assert turns[index].content == "First at 5"

    def test_with_non_sequential_line_numbers(self):
        """Test with gaps in line numbers (some lines parsed, others skipped)."""
        turns = [
            Turn(turn_type="user", content="Line 10", line_number=10),
            Turn(turn_type="assistant", content="Line 25", line_number=25),
            Turn(turn_type="user", content="Line 100", line_number=100),
        ]

        assert find_trigger_turn_index(turns, 10) == 0
        assert find_trigger_turn_index(turns, 25) == 1
        assert find_trigger_turn_index(turns, 100) == 2
        assert find_trigger_turn_index(turns, 50) == 0  # Not found, return 0


class TestLargeFileHandling:
    """Tests for large file handling in read_last_n_lines."""

    def test_large_file_uses_chunked_reading(self, tmp_path: Path, monkeypatch):
        """Test that files larger than threshold use chunked reading."""
        # Temporarily set threshold to a small value (patch in constants module)
        monkeypatch.setattr(
            "refresh.constants.LARGE_FILE_THRESHOLD_BYTES",
            1000,
        )

        # Reimport to get the patched value
        import refresh.transcript_parser as tp

        # Create a file larger than the threshold
        file_path = tmp_path / "large.txt"
        # Each line is ~20 bytes, need ~100 lines to exceed 1000 bytes
        lines_content = [f"line{i:05d}_padding" for i in range(100)]
        file_path.write_text("\n".join(lines_content))

        assert file_path.stat().st_size > 1000

        # Read last 10 lines - returns (lines, total_count)
        lines, total = tp.read_last_n_lines(file_path, 10)

        assert len(lines) == 10
        # Verify we got the last lines
        assert "line00090" in lines[0]
        assert "line00099" in lines[9]
        # Total is estimated for large files
        assert total > 0

    def test_small_file_simple_read(self, tmp_path: Path, monkeypatch):
        """Test that files smaller than threshold use simple reading."""
        monkeypatch.setattr(
            "refresh.constants.LARGE_FILE_THRESHOLD_BYTES",
            10000,
        )

        import refresh.transcript_parser as tp

        # Create a small file
        file_path = tmp_path / "small.txt"
        file_path.write_text("line1\nline2\nline3\nline4\nline5")

        assert file_path.stat().st_size < 10000

        lines, total = tp.read_last_n_lines(file_path, 3)

        assert len(lines) == 3
        assert "line3" in lines[0]
        assert "line5" in lines[2]
        assert total == 5  # Exact count for small files

    def test_chunked_reading_boundary_conditions(self, tmp_path: Path, monkeypatch):
        """Test chunked reading at exact boundaries."""
        monkeypatch.setattr(
            "refresh.constants.LARGE_FILE_THRESHOLD_BYTES",
            500,
        )

        import refresh.transcript_parser as tp

        # Create file just over threshold
        file_path = tmp_path / "boundary.txt"
        lines_content = [f"line{i:03d}" for i in range(100)]
        file_path.write_text("\n".join(lines_content))

        lines, total = tp.read_last_n_lines(file_path, 5)

        assert len(lines) == 5
        # Last 5 lines should be 95-99
        assert "line095" in lines[0]
        assert "line099" in lines[4]
        assert total > 0  # Estimated for large files

    def test_large_file_request_more_lines_than_exist(self, tmp_path: Path, monkeypatch):
        """Test requesting more lines than file contains."""
        monkeypatch.setattr(
            "refresh.constants.LARGE_FILE_THRESHOLD_BYTES",
            100,
        )

        import refresh.transcript_parser as tp

        file_path = tmp_path / "few_lines.txt"
        file_path.write_text("a" * 50 + "\nline1\nline2\nline3")

        lines, total = tp.read_last_n_lines(file_path, 100)

        # Should return all lines we have
        assert len(lines) <= 100
        assert any("line3" in line for line in lines)
        assert total > 0
