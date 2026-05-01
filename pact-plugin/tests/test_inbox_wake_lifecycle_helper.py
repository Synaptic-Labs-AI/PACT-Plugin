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


@pytest.mark.parametrize("agent", sorted(SELF_COMPLETE_EXEMPT_AGENTS))
def test_lifecycle_relevant_exempt_owner_with_corrupted_metadata(agent):
    """Owner-check hoist (be-M1): a self-complete-exempt agent (e.g.
    secretary) with non-dict metadata must STILL be exempt — return
    False. Pre-hoist behavior was True because the metadata-shape gate
    short-circuited to conservative-count BEFORE checking the owner
    carve-out, so corrupted metadata accidentally promoted exempt agents
    to lifecycle-relevant tasks. This is the inverse asymmetry of the
    sibling test_lifecycle_relevant_counts_under_malformed_metadata: a
    NON-exempt owner with corrupted metadata stays True (count it
    conservatively); an EXEMPT owner with corrupted metadata flips to
    False (the carve-out is owner-shape, not metadata-shape).

    Counter-test-by-revert: reverting the owner carve-out below the
    metadata-shape gate would flip this test RED."""
    task = {"status": "in_progress", "owner": agent, "metadata": "not-a-dict"}
    assert wl._lifecycle_relevant(task) is False, (
        f"Exempt owner {agent!r} with corrupted metadata must remain "
        f"exempt; pre-hoist behavior was True."
    )


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


# Adversarial-shape sweep across the (status, owner, metadata) cartesian
# product. Pins pure-never-raises for the predicate against arbitrary
# task shapes — required to gate the future try/except cleanup at
# session_init.py:728-730 (the gate depends on the WHOLE call chain
# being raise-free, not just the count_active_tasks entry point).
_BAD_STATUSES = [
    None, "", "kaboom", 42, 3.14, [], {}, True, b"bytes",
]
_BAD_OWNERS = [
    None, "", 42, [], {}, ["secretary"], {"name": "x"}, True,
]
_BAD_METADATAS = [
    "string", 42, [], True,
    {"completion_type": 42},  # wrong type for completion_type
    {"completion_type": "signal", "type": []},  # wrong type for type
    {"completion_type": "signal", "type": "blocker", "extra": object()},
    {"nested": {"deep": {"very": "deep"}}},
]


@pytest.mark.parametrize("status", _BAD_STATUSES)
def test_lifecycle_relevant_never_raises_on_adversarial_status(status):
    task = {"status": status, "owner": "x", "metadata": {}}
    try:
        result = wl._lifecycle_relevant(task)
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"_lifecycle_relevant raised on status={status!r}: {exc}")
    assert isinstance(result, bool)


@pytest.mark.parametrize("owner", _BAD_OWNERS)
def test_lifecycle_relevant_never_raises_on_adversarial_owner(owner):
    task = {"status": "in_progress", "owner": owner, "metadata": {}}
    try:
        result = wl._lifecycle_relevant(task)
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"_lifecycle_relevant raised on owner={owner!r}: {exc}")
    assert isinstance(result, bool)


@pytest.mark.parametrize("metadata", _BAD_METADATAS)
def test_lifecycle_relevant_never_raises_on_adversarial_metadata(metadata):
    task = {"status": "in_progress", "owner": "x", "metadata": metadata}
    try:
        result = wl._lifecycle_relevant(task)
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"_lifecycle_relevant raised on metadata={metadata!r}: {exc}")
    assert isinstance(result, bool)


@pytest.mark.parametrize("task", [
    {"status": "in_progress", "owner": ["secretary"], "metadata": []},
    {"status": [], "owner": {}, "metadata": "string"},
    {"status": None, "owner": None, "metadata": None},
    {"status": "pending", "owner": "kaboom", "metadata": {"completion_type": []}},
    {"status": 42, "owner": 99, "metadata": {"type": []}},
])
def test_lifecycle_relevant_never_raises_on_combined_adversarial_shapes(task):
    """Cross-field adversarial combinations — catches interactions
    between the status gate, owner-membership check, and metadata
    parse paths."""
    try:
        result = wl._lifecycle_relevant(task)
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"_lifecycle_relevant raised on {task!r}: {exc}")
    assert isinstance(result, bool)


# ---------- Dotfile exclusion (te-M2) ----------

def test_count_active_tasks_excludes_dotfile_prefixed_json(tmp_path, monkeypatch):
    """Dotfile-prefixed `.fake_task.json` files planted in the team
    directory must not influence the count. (Path.glob('*.json') matches
    dotfiles on POSIX, contra a common assumption — the explicit
    `name.startswith('.')` guard in iter_team_task_jsons is what excludes
    them.) Without that guard, an attacker who can write a single
    dotfile into the team's tasks dir could inflate the active-tasks
    count and suppress Teardown emit."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-dotfile"
    d = tmp_path / ".claude" / "tasks" / team
    d.mkdir(parents=True)
    # One legitimate active task.
    _stage_task(tmp_path, team, "real", status="in_progress", owner="x")
    # Dotfile-prefixed shape that would be active if matched.
    (d / ".fake_task.json").write_text(
        json.dumps({"id": "fake", "status": "in_progress", "owner": "y"}),
        encoding="utf-8",
    )
    # Dotfile-only file (pure leading-dot).
    (d / ".hidden.json").write_text(
        json.dumps({"id": "hidden", "status": "in_progress", "owner": "z"}),
        encoding="utf-8",
    )
    assert wl.count_active_tasks(team) == 1


# ---------- Symlink-escape defense (be-B1) ----------

def test_count_active_tasks_returns_zero_when_team_dir_symlink_escapes_root(tmp_path, monkeypatch):
    """Symlink-escape defense: even if `team_name` passes the safe-path
    allowlist, a symlink at ~/.claude/tasks/{team_name} pointing outside
    tasks_root must be detected via resolve()+relative_to and counted
    as 0. Mirrors session_end.py::cleanup_wake_registry's defense."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    tasks_root = tmp_path / ".claude" / "tasks"
    tasks_root.mkdir(parents=True)
    # Outside tasks_root: a directory with a real active task.
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "real.json").write_text(
        json.dumps({"id": "real", "status": "in_progress", "owner": "x"}),
        encoding="utf-8",
    )
    # team_name is allowlist-safe, but the team_dir symlinks outside.
    team = "team-sym"
    (tasks_root / team).symlink_to(outside, target_is_directory=True)
    # Without symlink-escape defense the count would be 1; with it, 0.
    assert wl.count_active_tasks(team) == 0


# ---------- Per-file symlink defense (be-F1) ----------

def test_count_active_tasks_skips_symlinked_task_files(tmp_path, monkeypatch):
    """Per-file symlink defense (be-F1): even when the team_dir is
    legitimate, individual task-file entries that are symlinks must be
    skipped. The team_dir-level resolve()+relative_to defense catches
    a malicious team_dir, but a regular team_dir with a planted symlink
    inside (e.g., `~/.claude/tasks/team-x/task-1.json -> /etc/passwd`)
    would otherwise be read by iter_team_task_jsons. Skip silently — the
    platform task system writes only regular files.

    Counter-test-by-revert: removing the per-file is_symlink guard
    would let a planted symlink contribute to the count."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-symfile"
    d = tmp_path / ".claude" / "tasks" / team
    d.mkdir(parents=True)
    # One legitimate active task as a regular file.
    _stage_task(tmp_path, team, "real", status="in_progress", owner="x")
    # External payload that the symlink will point at (active-shaped).
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    target = elsewhere / "planted.json"
    target.write_text(
        json.dumps({"id": "planted", "status": "in_progress", "owner": "y"}),
        encoding="utf-8",
    )
    # Symlinked task file inside the team dir — must be skipped.
    (d / "planted.json").symlink_to(target)
    assert wl.count_active_tasks(team) == 1
