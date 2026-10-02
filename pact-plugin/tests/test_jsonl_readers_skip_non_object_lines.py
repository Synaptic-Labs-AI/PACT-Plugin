"""
Location: pact-plugin/tests/test_jsonl_readers_skip_non_object_lines.py
Summary: A per-line JSONL reader skips a line that is valid JSON but not an
         object (a list, string, number or null), as it skips a malformed line.
Used by: pytest.

Each reader below either crashed on such a line or handed it to a consumer
that expects a dict. The other per-line readers already test isinstance(dict).
"""

import json
from pathlib import Path

import pytest

_NON_OBJECTS = ['["x"]', '"x"', "42", "null"]


@pytest.mark.parametrize("line", ['["agent_handoff"]', '"agent_handoff"'])
def test_the_handoff_census_skips_a_non_object_line(tmp_path, line):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import handoff_census as hc
    journal = tmp_path / ".claude" / "pact-sessions" / "proj" / "sess"
    journal.mkdir(parents=True)
    (journal / "session-journal.jsonl").write_text(
        line + "\n" + json.dumps({"type": "agent_handoff", "handoff": ["produced"]}) + "\n",
        encoding="utf-8")
    assert list(hc.journal_handoffs(tmp_path / ".claude")) == [{"produced": None}]


@pytest.mark.parametrize("line", _NON_OBJECTS)
def test_the_telegram_inbox_skips_a_non_object_line(tmp_path, line):
    from unittest.mock import MagicMock
    from telegram.routing import FileBasedRouter
    coord = tmp_path / "coordinator"
    (coord / "updates").mkdir(parents=True)
    router = FileBasedRouter(MagicMock(), session_id="sess-1", coordinator_dir=coord)
    (coord / "updates" / "sess-1.jsonl").write_text(
        line + "\n" + json.dumps({"update_id": 7}) + "\n", encoding="utf-8")
    assert router._read_inbox() == [{"update_id": 7}]


@pytest.mark.parametrize("line", _NON_OBJECTS)
def test_read_failures_skips_a_non_object_line(tmp_path, monkeypatch, line):
    from shared.failure_log import get_failure_log_path, read_failures
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = get_failure_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(line + "\n" + json.dumps({"error": "kept"}) + "\n", encoding="utf-8")
    assert read_failures() == [{"error": "kept"}]
