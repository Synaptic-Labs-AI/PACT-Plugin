"""
Behavioral invariants for pact-plugin/hooks/shared/wake_lifecycle.py.

Direct-import tests of count_active_tasks() and _lifecycle_relevant().
Pin the carve-out semantics (signal-tasks, exempt agents) to the
SELF_COMPLETE_EXEMPT_AGENTS frozenset reuse from shared.intentional_wait
(no duplicate literal). Pure-never-raises property pins the contract so
the redundant try/except in session_init.py:728-730 can be removed in
future cleanup.
"""

import json
import sys
from pathlib import Path

import pytest

# Hooks dir is added to sys.path by conftest.
import shared.wake_lifecycle as wl
from shared.intentional_wait import SELF_COMPLETE_EXEMPT_AGENTS


# ---------- Source-level structural invariants ----------

def test_helper_imports_exempt_set_from_intentional_wait():
    """No duplicate frozenset literal — the helper must reuse the
    canonical set from shared.intentional_wait."""
    src = (
        Path(__file__).resolve().parent.parent
        / "hooks" / "shared" / "wake_lifecycle.py"
    ).read_text(encoding="utf-8")
    assert "from shared.intentional_wait import SELF_COMPLETE_EXEMPT_AGENTS" in src
    # No re-declaration: the literal must not appear with a curly-brace
    # set syntax in the helper itself.
    assert "frozenset({\"secretary\"" not in src
    assert "frozenset({'secretary'" not in src


def test_helper_documented_pure_never_raises():
    """Pin the docstring contract — pure functions, never raise. This is
    the structural anchor that lets future cleanup remove the redundant
    try/except wrapping count_active_tasks at session_init.py:728-730."""
    docs = (wl.count_active_tasks.__doc__ or "") + (wl.__doc__ or "")
    assert "never raise" in docs.lower() or "never raises" in docs.lower()


# ---------- _lifecycle_relevant predicate ----------

@pytest.mark.parametrize("task,expected", [
    ({"status": "in_progress", "owner": "x"}, True),
    ({"status": "pending", "owner": "x"}, True),
    ({"status": "completed", "owner": "x"}, False),
    ({"status": "deleted", "owner": "x"}, False),
    ({"status": "blocked", "owner": "x"}, False),
    ({"status": "in_progress"}, True),  # missing owner is fine
    ({}, False),  # missing status fails the active-status gate
])
def test_lifecycle_relevant_status_gate(task, expected):
    assert wl._lifecycle_relevant(task) is expected


@pytest.mark.parametrize("agent", sorted(SELF_COMPLETE_EXEMPT_AGENTS))
def test_lifecycle_relevant_excludes_exempt_agents(agent):
    task = {"status": "in_progress", "owner": agent}
    assert wl._lifecycle_relevant(task) is False


@pytest.mark.parametrize("metadata,expected", [
    ({"completion_type": "signal", "type": "blocker"}, False),
    ({"completion_type": "signal", "type": "algedonic"}, False),
    # Wrong type → not a signal carve-out, still counts.
    ({"completion_type": "signal", "type": "regular"}, True),
    # Missing completion_type → counts.
    ({"type": "blocker"}, True),
    # Empty metadata → counts.
    ({}, True),
])
def test_lifecycle_relevant_signal_task_carveout(metadata, expected):
    task = {"status": "in_progress", "owner": "x", "metadata": metadata}
    assert wl._lifecycle_relevant(task) is expected


def test_lifecycle_relevant_handles_non_dict_input():
    for bad in (None, [], 42, "string", True):
        assert wl._lifecycle_relevant(bad) is False


def test_lifecycle_relevant_counts_under_malformed_metadata():
    """Malformed metadata (non-dict) is conservative-counted: cannot
    silently exempt a real active task on a parse failure."""
    task = {"status": "in_progress", "owner": "x", "metadata": "not-a-dict"}
    assert wl._lifecycle_relevant(task) is True


# ---------- count_active_tasks ----------

def test_count_active_tasks_returns_zero_on_empty_team_name(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert wl.count_active_tasks("") == 0
    assert wl.count_active_tasks(None) == 0  # type: ignore[arg-type]


def test_count_active_tasks_returns_zero_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert wl.count_active_tasks("ghost-team") == 0


def _stage_task(tmp_path: Path, team: str, task_id: str, **fields) -> None:
    d = tmp_path / ".claude" / "tasks" / team
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{task_id}.json").write_text(
        json.dumps({"id": task_id, **fields}), encoding="utf-8"
    )


def test_count_active_tasks_counts_pending_and_in_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-counts"
    _stage_task(tmp_path, team, "1", status="pending", owner="x")
    _stage_task(tmp_path, team, "2", status="in_progress", owner="y")
    _stage_task(tmp_path, team, "3", status="completed", owner="z")
    _stage_task(tmp_path, team, "4", status="deleted", owner="w")
    assert wl.count_active_tasks(team) == 2


def test_count_active_tasks_skips_signal_and_exempt(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-carveouts"
    _stage_task(tmp_path, team, "real", status="in_progress", owner="x")
    _stage_task(
        tmp_path, team, "sig",
        status="in_progress", owner="y",
        metadata={"completion_type": "signal", "type": "blocker"},
    )
    _stage_task(tmp_path, team, "sec", status="in_progress", owner="secretary")
    assert wl.count_active_tasks(team) == 1


def test_count_active_tasks_skips_unparseable_files(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-malformed"
    d = tmp_path / ".claude" / "tasks" / team
    d.mkdir(parents=True)
    _stage_task(tmp_path, team, "ok", status="in_progress", owner="x")
    (d / "garbage.json").write_text("not valid json {{{", encoding="utf-8")
    assert wl.count_active_tasks(team) == 1


# ---------- Pure-never-raises property ----------

@pytest.mark.parametrize("bad_input", [
    None,
    "",
    "/etc",
    "team\x00with-null",
    "../../../escape",
    42,
])
def test_count_active_tasks_never_raises_on_bad_team_name(bad_input, tmp_path, monkeypatch):
    """Pure-function contract: any input shape exits with a count, never
    raises. Pinning this lets the redundant try/except at
    session_init.py:728-730 be removed in future cleanup."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Should not raise for any bad input.
    result = wl.count_active_tasks(bad_input)  # type: ignore[arg-type]
    assert isinstance(result, int)
    assert result >= 0


def test_lifecycle_relevant_never_raises():
    for bad in [None, [], {}, {"status": None}, {"metadata": None},
                {"status": "in_progress", "owner": None},
                {"status": "in_progress", "metadata": []}]:
        try:
            result = wl._lifecycle_relevant(bad)
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"_lifecycle_relevant raised on {bad!r}: {exc}")
        assert isinstance(result, bool)
