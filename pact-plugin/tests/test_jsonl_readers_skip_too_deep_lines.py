"""
Location: pact-plugin/tests/test_jsonl_readers_skip_too_deep_lines.py
Summary: Every per-line JSON reader of a journal or JSONL file skips a line
         nested too deeply to parse, as it skips a malformed line.
Used by: pytest.

json.loads raises RecursionError, not ValueError, on a line nested past the
decoder's limit: from depth 1,000 on Python 3.9 and about 1,000,000 on 3.14.
A per-line handler that catches only ValueError lets it escape, and one such
line then drops the whole read (or reaches a hook's main). Each test puts one
such line before a real record in the reader's scan order and checks that the
record is still found.
"""

import json
from pathlib import Path

import pytest

_DEPTH = 1_000_000


def _deep(marker):
    """A line too deep to parse that still carries ``marker``, so a cheap
    substring pre-filter lets it through to json.loads."""
    return ('{"type": "x", "note": %s, "a": ' % json.dumps(marker)
            + "[" * _DEPTH + "]" * _DEPTH + "}")


_PAUSE = {"v": 1, "type": "session_paused", "ts": "2020-01-01T00:00:00Z",
          "pr_number": 6161, "pr_url": "https://example.invalid/pull/6161",
          "branch": "feat/x", "worktree_path": "/tmp/nowhere",
          "consolidation_completed": True}


class TestSessionJournal:

    def _journal(self, tmp_path, *lines):
        (tmp_path / "session-journal.jsonl").write_text(
            "".join(line + "\n" for line in lines), encoding="utf-8")
        return str(tmp_path)

    def test_read_events_from_skips_the_deep_line(self, tmp_path):
        from shared.session_journal import read_events_from
        sd = self._journal(tmp_path, _deep("session_paused"), json.dumps(_PAUSE))
        assert [e["pr_number"] for e in read_events_from(sd, "session_paused")] == [6161]

    def test_read_last_event_from_skips_the_deep_line(self, tmp_path):
        from shared.session_journal import read_last_event_from
        # The scan runs from the end, so the deep line comes first.
        sd = self._journal(tmp_path, json.dumps(_PAUSE), _deep("session_paused"))
        event = read_last_event_from(sd, "session_paused")
        assert event is not None
        assert event["pr_number"] == 6161

    def test_the_pause_claim_survives_a_deep_line(self, tmp_path):
        from shared.session_resume import check_resume_state
        sd = self._journal(tmp_path, json.dumps(_PAUSE), _deep("session_paused"))
        assert "6161" in (check_resume_state(sd) or "")


class TestTeammateRegistry:

    def test_resolve_skips_the_deep_line(self, tmp_path, monkeypatch):
        from shared import session_registry
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        reg = tmp_path / ".claude" / "pact-sessions" / ".teammate-registry.jsonl"
        reg.parent.mkdir(parents=True)
        monkeypatch.setattr(session_registry, "get_registry_path", lambda: reg)
        team = tmp_path / ".claude" / "teams" / "pact-x"
        team.mkdir(parents=True)
        (team / "config.json").write_text(json.dumps({"members": [{"name": "alice"}]}))
        reg.write_text(_deep("s1") + "\n"
                       + json.dumps({"session_id": "s1", "value": "alice@pact-x"}) + "\n")
        assert session_registry.resolve("s1") == "alice@pact-x"

    def test_prune_drops_the_deep_line_and_keeps_a_live_one(self, tmp_path):
        from session_end import _prune_registry_dead_teams
        teams = tmp_path / ".claude" / "teams"
        (teams / "pact-live").mkdir(parents=True)
        reg = tmp_path / ".claude" / "pact-sessions" / ".teammate-registry.jsonl"
        reg.parent.mkdir(parents=True)
        live = json.dumps({"session_id": "s1", "value": "alice@pact-live"})
        reg.write_text(_deep("@pact-live") + "\n" + live + "\n", encoding="utf-8")
        assert _prune_registry_dead_teams(registry_path=reg, teams_dir=teams) == 1
        assert reg.read_text(encoding="utf-8").splitlines() == [live]


class TestFailureLog:

    @pytest.fixture
    def log(self, tmp_path, monkeypatch):
        from shared.failure_log import get_failure_log_path
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        path = get_failure_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_deep("x") + "\n" + json.dumps({"error": "kept"}) + "\n",
                        encoding="utf-8")
        return path

    def test_read_failures_skips_the_deep_line(self, log):
        from shared.failure_log import read_failures
        assert [e.get("error") for e in read_failures()] == ["kept"]

    def test_append_failure_still_writes_its_record(self, log):
        from shared.failure_log import append_failure
        append_failure(classification="c", error="new")
        assert [json.loads(l).get("error") for l in log.read_text().splitlines()] == [
            "kept", "new"]


def test_the_handoff_census_skips_the_deep_line(tmp_path):
    import handoff_census as hc
    journal = tmp_path / ".claude" / "pact-sessions" / "proj" / "sess"
    journal.mkdir(parents=True)
    (journal / "session-journal.jsonl").write_text(
        _deep("agent_handoff") + "\n"
        + json.dumps({"type": "agent_handoff", "handoff": ["produced"]}) + "\n",
        encoding="utf-8")
    assert list(hc.journal_handoffs(tmp_path / ".claude")) == [{"produced": None}]


def test_the_telegram_inbox_skips_the_deep_line(tmp_path):
    from unittest.mock import MagicMock
    from telegram.routing import FileBasedRouter
    coord = tmp_path / "coordinator"
    (coord / "updates").mkdir(parents=True)
    router = FileBasedRouter(MagicMock(), session_id="sess-1", coordinator_dir=coord)
    (coord / "updates" / "sess-1.jsonl").write_text(
        _deep("x") + "\n" + json.dumps({"update_id": 7}) + "\n", encoding="utf-8")
    assert router._read_inbox() == [{"update_id": 7}]
