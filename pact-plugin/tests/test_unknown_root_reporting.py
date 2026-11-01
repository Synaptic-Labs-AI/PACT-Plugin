"""F5: the two refusals that were silent now report.

Location: pact-plugin/tests/test_unknown_root_reporting.py
Summary: Two consumers of the memory-root answer refused without saying so.
  - `gate_target` returns None on an unknown root, and the pin-cap gate
    returned that None as a silent allow. The cap is off for the whole
    session, so the gate now allows WITH an advisory naming the condition --
    the PreToolUse counterpart of `check_claude_md_refusals`' SessionStart
    report.
  - `sync_retrieved_to_claude_md` produces ROOT_UNKNOWN and its one caller,
    `PACTMemory.search`, used to discard the object. The reason now reaches
    `last_sync_status`, the same structured channel `save()` uses.

Used by/with: hooks/pin_caps_gate.py (`_unknown_root_decision`),
hooks/shared/claude_md_manager.py (`session_memory_root`), and
skills/pact-memory/scripts/memory_api.py (`PACTMemory.search`).
"""
from __future__ import annotations


def _write_frame(file_path):
    return {
        "tool_name": "Write",
        "agent_type": "pact-orchestrator",
        "tool_input": {"file_path": str(file_path), "content": "# notes\n"},
    }


def test_the_pin_cap_gate_reports_an_unknown_memory_root(tmp_path, monkeypatch):
    """The gate cannot identify the document, so it allows -- but it must say
    the cap was not checked rather than returning None into silence."""
    from pin_caps_gate import _gate

    target = tmp_path / "CLAUDE.md"
    target.write_text("# notes\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "")  # git is genuinely unavailable

    decision = _gate(_write_frame(target))

    assert decision is not None, "the gate allowed an unknown root in silence"
    assert decision.verdict == "ALLOW_ADVISORY"
    assert decision.cause == "unknown_root"
    assert "pin cap was not checked" in decision.reason


def test_the_pin_cap_gate_stays_silent_on_a_known_root_non_target(
    tmp_path, monkeypatch
):
    """Control: an ordinary ungated target keeps returning None. The advisory
    is for the unknown root, not for every refused target."""
    from pin_caps_gate import _gate

    nested = tmp_path / "sub"
    nested.mkdir()
    target = nested / "CLAUDE.md"
    target.write_text("# notes\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    assert _gate(_write_frame(target)) is None


def test_search_keeps_the_retrieved_sync_reason(monkeypatch):
    """ROOT_UNKNOWN used to be produced and dropped. It now reaches the same
    reason channel a save uses, so a caller can see it."""
    from scripts import memory_api
    from scripts.working_memory import SyncResult

    class _Result:
        id = "mem-1"

        def to_dict(self):
            return {"id": "mem-1"}

    memory = memory_api.PACTMemory(
        project_id="test-project", session_id="test-session", db_path=None
    )
    monkeypatch.setattr(memory_api, "_ensure_ready", lambda: None)
    monkeypatch.setattr(
        memory_api, "graph_enhanced_search", lambda *args, **kwargs: [_Result()]
    )

    for reason in (SyncResult.ROOT_UNKNOWN, SyncResult.WROTE):
        monkeypatch.setattr(
            memory_api,
            "sync_retrieved_to_claude_md",
            lambda *args, _reason=reason, **kwargs: SyncResult(_reason),
        )
        memory.search("query")
        assert memory.last_sync_status == reason


def test_search_without_a_sync_leaves_the_status_alone(monkeypatch):
    """Control: `sync_to_claude=False` performs no write, so it must not
    overwrite a status left by an earlier write on this instance."""
    from scripts import memory_api

    memory = memory_api.PACTMemory(
        project_id="test-project", session_id="test-session", db_path=None
    )
    memory._last_sync_status = "wrote"
    monkeypatch.setattr(memory_api, "_ensure_ready", lambda: None)
    monkeypatch.setattr(
        memory_api, "graph_enhanced_search", lambda *args, **kwargs: []
    )

    memory.search("query", sync_to_claude=False)

    assert memory.last_sync_status == "wrote"
