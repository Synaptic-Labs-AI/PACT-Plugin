"""
Location: pact-plugin/tests/test_non_tty_stamp_wiring.py
Summary: The harness-stamp wiring INSIDE session_init.main() — the gap the W1
unit battery leaves: test_session_init.py's existing main-path assertions were
amended to ``harness=ANY`` (tolerant, pins nothing), and test_non_tty_w1.py
unit-tests the pieces (``_read_transcript_entrypoint``, ``_saved_context``,
``set_harness_stamp``) without driving the composition. These tests run the
real ``main()`` (in-process, the established test_session_init pattern) with a
real transcript file on stdin and pin the two observables the wiring owns:
the PERSISTED context's ``harness`` value, and the ``session_start`` journal
event's ``entrypoint`` diagnostic (present-when-read, OMITTED-when-empty).
Used by: the full-suite merge gate (`cd pact-plugin && python3 -m pytest -q`,
no path argument).
"""

from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

SESSION_ID = "9f8e7d6c-5b4a-3f2e-1d0c-9b8a7f6e5d4c"
LEAD = "PACT:pact-orchestrator"


def _transcript(tmp_path, entrypoint):
    path = tmp_path / "session.jsonl"
    path.write_text(
        json.dumps({"type": "user", "entrypoint": entrypoint}) + "\n",
        encoding="utf-8",
    )
    return str(path)


def _run_main(tmp_path, monkeypatch, pact_context, transcript_path,
              saved_context=None):
    """Drive the real session_init.main() with the heavy neighbors patched
    (the test_session_init idiom) and return (persisted context dict,
    captured journal events). The pact_context fixture points the context
    module at this tmp tree, so persist_context's write is readable back."""
    import session_init

    events = []

    def _spy(event):
        events.append(event)

    pact_context(team_name="session-9f8e7d6c", session_id=SESSION_ID)
    if saved_context is not None:
        monkeypatch.setattr(session_init, "_saved_context",
                            lambda *a, **k: dict(saved_context))
    stdin = io.StringIO(json.dumps({
        "hook_event_name": "SessionStart",
        "source": "startup",
        "session_id": SESSION_ID,
        "transcript_path": transcript_path,
        "agent_type": LEAD,
    }))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "project"))
    (tmp_path / "project").mkdir(exist_ok=True)
    with patch("session_init.setup_plugin_symlinks", return_value=None), \
         patch("session_init.ensure_project_memory_md", return_value=None), \
         patch("session_init.check_pinned_staleness", return_value=None), \
         patch("session_init.update_session_info", return_value=None), \
         patch("session_init.get_task_list", return_value=None), \
         patch("session_init.restore_last_session", return_value=None), \
         patch("session_init.check_resume_state", return_value=None), \
         patch("session_init.append_event", _spy), \
         patch("sys.stdin", stdin), \
         patch("sys.stdout", new_callable=io.StringIO):
        with pytest.raises(SystemExit) as exc_info:
            session_init.main()
    assert exc_info.value.code == 0

    from shared import pact_context as ctx
    persisted = json.loads(ctx._context_path.read_text(encoding="utf-8"))
    return persisted, events


class TestWriterOneWiringThroughMain:
    def test_remote_transcript_persists_stamp_and_journals_entrypoint(
            self, tmp_path, monkeypatch, pact_context):
        persisted, events = _run_main(
            tmp_path, monkeypatch, pact_context,
            _transcript(tmp_path, "remote"))
        assert persisted["harness"] == "non_tty", (
            "a remote entrypoint on the transcript MUST stamp the persisted "
            "context through the main() wiring"
        )
        start = next(e for e in events if e.get("type") == "session_start")
        assert start.get("entrypoint") == "remote", (
            "the raw entrypoint rides the session_start event as the "
            "post-mortem launcher diagnostic"
        )

    def test_cli_transcript_stamps_nothing_but_journals_the_raw_value(
            self, tmp_path, monkeypatch, pact_context):
        persisted, events = _run_main(
            tmp_path, monkeypatch, pact_context,
            _transcript(tmp_path, "cli"))
        assert persisted["harness"] == "", (
            "'cli' is not in the non-TTY mapping — no stamp may land"
        )
        start = next(e for e in events if e.get("type") == "session_start")
        assert start.get("entrypoint") == "cli", (
            "the diagnostic is the RAW launcher value — journaled even when "
            "it maps to no stamp (post-mortem attribution, not gating)"
        )

    def test_unreadable_transcript_omits_the_diagnostic_entirely(
            self, tmp_path, monkeypatch, pact_context):
        persisted, events = _run_main(
            tmp_path, monkeypatch, pact_context,
            str(tmp_path / "nope.jsonl"))
        assert persisted["harness"] == ""
        start = next(e for e in events if e.get("type") == "session_start")
        assert "entrypoint" not in start, (
            "the field is OMITTED when the read yielded nothing — never "
            "written empty"
        )

    def test_recorded_stamp_is_carried_not_cleared_by_a_cli_transcript(
            self, tmp_path, monkeypatch, pact_context):
        """MONOTONIC through main(): a stamp already recorded for this session
        survives a re-fire whose transcript names a TTY launcher — the wiring
        may set the stamp, never clear it."""
        persisted, _ = _run_main(
            tmp_path, monkeypatch, pact_context,
            _transcript(tmp_path, "cli"),
            saved_context={"harness": "non_tty"})
        assert persisted["harness"] == "non_tty"
