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

import os
import subprocess

import pytest


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


def _git(*args, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t",
         "-c", "init.defaultBranch=main", *args],
        cwd=str(cwd), env=env, capture_output=True, text=True,
        timeout=30, check=True,
    )


def _missing_anchor(tmp_path):
    """A declared directory that does not exist: cause MISSING (no record is
    read under pytest, so recovery cannot rescue it)."""
    return tmp_path / "removed-worktree"


def _forged_anchor(tmp_path):
    """A `.git` FILE naming another repository's live worktree admin dir: git
    answers, the answer fails verification, cause UNVERIFIED."""
    main = tmp_path / "main"
    main.mkdir()
    _git("init", "-q", ".", cwd=main)
    (main / "README").write_text("seed\n")
    _git("add", ".", cwd=main)
    _git("commit", "-qm", "seed", cwd=main)
    _git("worktree", "add", "-q", str(main / ".worktrees" / "wt"), "-b", "wt",
         cwd=main)
    admin = next((main / ".git" / "worktrees").iterdir())
    forged = tmp_path / "forged"
    forged.mkdir()
    (forged / ".git").write_text(f"gitdir: {admin}\n")
    return forged


@pytest.mark.parametrize(
    "build, cause",
    [(_missing_anchor, "missing"), (_forged_anchor, "unverified")],
    ids=["missing", "unverified"],
)
def test_the_advisory_renders_the_cause_of_the_one_observation(
    tmp_path, monkeypatch, build, cause
):
    """The advisory's cause clause is `unknown_root_reason` for the cause the
    gate acted on, and the gate resolves the session ONCE. Red if the gate
    resolves a second time (the spy counts two) or renders a cause of its own
    instead of the SessionRoot's (the clause is missing from the reason)."""
    import shared.project_scope as project_scope
    from pin_caps_gate import _gate

    anchor = build(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(anchor))
    monkeypatch.chdir(tmp_path)

    real = project_scope.session_memory_root
    seen = []

    def spy():
        session = real()
        seen.append(session)
        return session

    monkeypatch.setattr(project_scope, "session_memory_root", spy)
    target = tmp_path / "CLAUDE.md"

    decision = _gate(_write_frame(target))

    assert len(seen) == 1, f"the gate resolved the session {len(seen)} times"
    assert seen[0].cause == cause, (
        f"FIXTURE INVALID: the layout gave {seen[0].cause!r}, not {cause!r}"
    )
    assert decision is not None and decision.cause == "unknown_root"
    clause = project_scope.unknown_root_reason(cause, seen[0].anchor)
    assert clause in decision.reason, decision.reason
    assert "pin cap was not checked" in decision.reason
    if cause == "missing":
        assert "session record did not verify a main checkout" in decision.reason


@pytest.mark.parametrize("anchored", [True, False], ids=["anchor", "no-anchor"])
def test_the_advisory_names_the_record_only_when_there_was_an_anchor(
    tmp_path, monkeypatch, anchored
):
    """Exact advisory text for cause MISSING. A missing anchor went through
    record recovery, so the advisory says the record did not verify; a None
    anchor (a vanished cwd nothing names) consulted no record and names none."""
    import shared.project_scope as project_scope
    from pin_caps_gate import _gate

    gone = tmp_path / "gone"
    anchor = gone if anchored else None
    monkeypatch.setattr(
        project_scope, "session_memory_root",
        lambda: project_scope.SessionRoot(
            anchor, anchored, None, project_scope.ROOT_CAUSE_MISSING
        ),
    )

    decision = _gate(_write_frame(tmp_path / "CLAUDE.md"))

    clause = (
        f"the project directory {gone} no longer exists and the session record "
        "did not verify a main checkout for it"
        if anchored else "the working directory no longer exists"
    )
    assert decision is not None and decision.reason == (
        f"PACT could not identify this session's project memory: {clause}, so "
        "the pin cap was not checked for this Write."
    ), decision


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


def test_search_without_a_sync_clears_the_previous_status(monkeypatch):
    """`sync_to_claude=False` performs no write, and an empty search clears
    any status left by an earlier write on this instance at method entry."""
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

    assert memory.last_sync_status is None
