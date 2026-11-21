"""
Tests for the structural wait mechanism: hooks/pact_wait.py (watcher CLI)
and hooks/shared/wait_target.py (deliverable predicate registry).

Verification-level coverage binding the exit contract (the numbers are the
contract — a crash must never masquerade as EXPIRED):
- predicates: handoff (non-empty `produced` STRING; list-typed produced reads
  absent — that shape is the live failure class this mechanism answers),
  teachback_submit (non-empty object).
- evaluate_target outcomes: present / not_present / target_missing (incl.
  unsafe team name) / target_unreadable / target_corrupt / unknown_predicate.
- wait_for_deliverable: fast-exit on already-present, fires after delayed
  appearance, expires loud at the deadline, terminal on missing target,
  terminal on persistently corrupt file, tolerant of a mid-rewrite-style
  transient parse failure, terminal on unknown predicate.
- main(): usage errors exit 2 (argparse), happy path returns 0.
- one subprocess smoke: the script runs standalone from an arbitrary cwd
  (its shared.* import resolves via the script directory).
- TEST-phase matrix (trailing section): duplicate-watcher re-arm (named
  scenario), corrupt-streak boundary (second consecutive read terminal;
  streak resets on a good read), terminal immediacy, exit-1 crash
  distinctness, empty/traversal task-id containment, and the anti-dark-key
  arm (a recommended-only field never satisfies the handoff predicate).
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import pact_wait
from shared import wait_target

HOOKS_DIR = Path(__file__).parent.parent / "hooks"


# --- helpers ---------------------------------------------------------------

def _write_task(tasks_base: Path, team: str, task_id: str, payload: dict) -> Path:
    team_dir = tasks_base / team
    team_dir.mkdir(parents=True, exist_ok=True)
    task_file = team_dir / f"{task_id}.json"
    task_file.write_text(json.dumps(payload), encoding="utf-8")
    return task_file


def _handoff_task(produced) -> dict:
    return {"metadata": {"handoff": {"produced": produced, "decisions": "d"}}}


# --- exit-contract constants (the test matrix binds the final numbers) -----

def test_exit_class_numbers_are_pinned():
    assert pact_wait.EXIT_FIRED == 0
    assert pact_wait.EXIT_EXPIRED == 3
    assert pact_wait.EXIT_TERMINAL == 4
    # 1 and 2 are owned by crash / usage and must never alias a watch outcome.
    assert pact_wait.EXIT_FIRED != 1 and pact_wait.EXIT_FIRED != 2
    assert len({pact_wait.EXIT_FIRED, pact_wait.EXIT_EXPIRED, pact_wait.EXIT_TERMINAL}) == 3


# --- predicate registry ----------------------------------------------------

def test_registry_ships_exactly_the_two_predicates():
    assert set(wait_target.PREDICATES) == {"handoff", "teachback_submit"}
    assert all(callable(fn) for fn in wait_target.PREDICATES.values())


def test_handoff_predicate_string_produced_is_present():
    assert wait_target.handoff_present(_handoff_task("a summary"))


@pytest.mark.parametrize(
    "produced",
    ["", "   ", None, ["a summary"], 0, False],
)
def test_handoff_predicate_non_string_or_empty_produced_is_absent(produced):
    assert not wait_target.handoff_present(_handoff_task(produced))


@pytest.mark.parametrize(
    "task",
    [
        {},
        {"metadata": {}},
        {"metadata": {"handoff": {}}},
        {"metadata": {"handoff": "not-a-dict"}},
        {"metadata": "not-a-dict"},
    ],
)
def test_handoff_predicate_malformed_shapes_are_absent(task):
    assert not wait_target.handoff_present(task)


def test_teachback_predicate():
    present = {"metadata": {"teachback_submit": {"understanding": "u"}}}
    assert wait_target.teachback_submit_present(present)
    assert not wait_target.teachback_submit_present(
        {"metadata": {"teachback_submit": {}}}
    )
    assert not wait_target.teachback_submit_present(
        {"metadata": {"teachback_submit": "not-a-dict"}}
    )
    assert not wait_target.teachback_submit_present({"metadata": {}})


# --- evaluate_target -------------------------------------------------------

def test_evaluate_present(tmp_path):
    _write_task(tmp_path, "team-a", "7", _handoff_task("done"))
    outcome = wait_target.evaluate_target(
        "team-a", "7", "handoff", tasks_base_dir=str(tmp_path)
    )
    assert outcome == wait_target.EVAL_PRESENT


def test_evaluate_not_present(tmp_path):
    _write_task(tmp_path, "team-a", "7", {"metadata": {}})
    outcome = wait_target.evaluate_target(
        "team-a", "7", "handoff", tasks_base_dir=str(tmp_path)
    )
    assert outcome == wait_target.EVAL_NOT_PRESENT


def test_evaluate_missing_file_and_unsafe_team(tmp_path):
    assert (
        wait_target.evaluate_target(
            "team-a", "7", "handoff", tasks_base_dir=str(tmp_path)
        )
        == wait_target.EVAL_TARGET_MISSING
    )
    # Traversal-shaped team name resolves through the containment seam to no
    # safe dir -> missing, never a crash.
    assert (
        wait_target.evaluate_target(
            "../escape", "7", "handoff", tasks_base_dir=str(tmp_path)
        )
        == wait_target.EVAL_TARGET_MISSING
    )


def test_evaluate_corrupt(tmp_path):
    team_dir = tmp_path / "team-a"
    team_dir.mkdir()
    (team_dir / "7.json").write_text("{not json", encoding="utf-8")
    assert (
        wait_target.evaluate_target(
            "team-a", "7", "handoff", tasks_base_dir=str(tmp_path)
        )
        == wait_target.EVAL_TARGET_CORRUPT
    )


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="root reads bypass file permissions",
)
def test_evaluate_unreadable(tmp_path):
    task_file = _write_task(tmp_path, "team-a", "7", _handoff_task("done"))
    task_file.chmod(0o000)
    assert (
        wait_target.evaluate_target(
            "team-a", "7", "handoff", tasks_base_dir=str(tmp_path)
        )
        == wait_target.EVAL_TARGET_UNREADABLE
    )


def test_evaluate_unknown_predicate(tmp_path):
    assert (
        wait_target.evaluate_target(
            "team-a", "7", "no_such_predicate", tasks_base_dir=str(tmp_path)
        )
        == wait_target.EVAL_UNKNOWN_PREDICATE
    )


# --- wait_for_deliverable --------------------------------------------------

def test_fast_exit_when_already_present(tmp_path):
    _write_task(tmp_path, "team-a", "7", _handoff_task("done"))
    start = time.monotonic()
    rc = pact_wait.wait_for_deliverable(
        "team-a", "7", "handoff", deadline=30,
        tasks_base_dir=str(tmp_path), poll_interval=0.2,
    )
    # FIRED before the first sleep: strictly faster than one poll interval.
    assert rc == pact_wait.EXIT_FIRED
    assert time.monotonic() - start < 0.2


def test_fires_after_delayed_appearance(tmp_path):
    task_file = _write_task(tmp_path, "team-a", "7", {"metadata": {}})
    timer = threading.Timer(
        0.15,
        lambda: task_file.write_text(
            json.dumps(_handoff_task("late but delivered")), encoding="utf-8"
        ),
    )
    timer.start()
    try:
        rc = pact_wait.wait_for_deliverable(
            "team-a", "7", "handoff", deadline=5,
            tasks_base_dir=str(tmp_path), poll_interval=0.05,
        )
    finally:
        timer.join()
    assert rc == pact_wait.EXIT_FIRED


def test_expires_loud_at_deadline(tmp_path, capsys):
    _write_task(tmp_path, "team-a", "7", {"metadata": {}})
    start = time.monotonic()
    rc = pact_wait.wait_for_deliverable(
        "team-a", "7", "handoff", deadline=0.3,
        tasks_base_dir=str(tmp_path), poll_interval=0.1,
    )
    assert rc == pact_wait.EXIT_EXPIRED
    assert time.monotonic() - start >= 0.3
    captured = capsys.readouterr()
    assert "EXPIRED" in captured.err
    assert "team-a" in captured.err and "7" in captured.err


def test_terminal_on_missing_target(tmp_path):
    rc = pact_wait.wait_for_deliverable(
        "team-a", "7", "handoff", deadline=30,
        tasks_base_dir=str(tmp_path), poll_interval=0.05,
    )
    assert rc == pact_wait.EXIT_TERMINAL


def test_terminal_on_persistently_corrupt_file(tmp_path):
    team_dir = tmp_path / "team-a"
    team_dir.mkdir()
    (team_dir / "7.json").write_text("{still not json", encoding="utf-8")
    rc = pact_wait.wait_for_deliverable(
        "team-a", "7", "handoff", deadline=30,
        tasks_base_dir=str(tmp_path), poll_interval=0.05,
    )
    assert rc == pact_wait.EXIT_TERMINAL


def test_tolerates_transient_corrupt_read(tmp_path):
    # A rewrite landing between polls must not kill the watch: corrupt on
    # the first poll, healed (deliverable written) well before the second.
    team_dir = tmp_path / "team-a"
    team_dir.mkdir()
    task_file = team_dir / "7.json"
    task_file.write_text("{mid-rewrite", encoding="utf-8")
    timer = threading.Timer(
        0.03,
        lambda: task_file.write_text(
            json.dumps(_handoff_task("arrived")), encoding="utf-8"
        ),
    )
    timer.start()
    try:
        rc = pact_wait.wait_for_deliverable(
            "team-a", "7", "handoff", deadline=5,
            tasks_base_dir=str(tmp_path), poll_interval=0.1,
        )
    finally:
        timer.join()
    assert rc == pact_wait.EXIT_FIRED


def test_terminal_on_unknown_predicate(tmp_path):
    rc = pact_wait.wait_for_deliverable(
        "team-a", "7", "no_such_predicate", deadline=30,
        tasks_base_dir=str(tmp_path), poll_interval=0.05,
    )
    assert rc == pact_wait.EXIT_TERMINAL


# --- main(): CLI surface ---------------------------------------------------

def test_main_fired_happy_path(tmp_path):
    _write_task(tmp_path, "team-a", "7", _handoff_task("done"))
    rc = pact_wait.main(
        [
            "--team", "team-a", "--task", "7",
            "--predicate", "handoff", "--deadline", "5",
            "--tasks-base-dir", str(tmp_path),
        ]
    )
    assert rc == pact_wait.EXIT_FIRED


@pytest.mark.parametrize(
    "argv",
    [
        ["--team", "team-a", "--task", "7", "--predicate", "handoff"],
        ["--team", "team-a", "--task", "7", "--predicate", "handoff",
         "--deadline", "0"],
        ["--team", "team-a", "--task", "7", "--predicate", "handoff",
         "--deadline", "-3"],
        ["--team", "team-a", "--task", "7", "--predicate", "handoff",
         "--deadline", "abc"],
        ["--team", "team-a", "--predicate", "handoff", "--deadline", "5"],
    ],
)
def test_usage_errors_exit_2(argv, tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        pact_wait.main(argv + ["--tasks-base-dir", str(tmp_path)])
    assert excinfo.value.code == 2


def test_subprocess_standalone_smoke(tmp_path):
    # Run as a real script from an arbitrary cwd: the shared.* import must
    # resolve via the script directory, and the full contract must hold
    # end-to-end. The task file EXISTS but the deliverable never lands, so
    # the watch runs to completion and expires loud (a missing file would
    # correctly exit TERMINAL before any watching starts).
    _write_task(tmp_path, "team-a", "7", {"metadata": {}})
    cmd = [
        sys.executable, str(HOOKS_DIR / "pact_wait.py"),
        "--team", "team-a", "--task", "7",
        "--predicate", "handoff", "--deadline", "0.5",
        "--tasks-base-dir", str(tmp_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd="/", timeout=30)
    assert proc.returncode == pact_wait.EXIT_EXPIRED
    assert "EXPIRED" in proc.stderr


# --- TEST-phase matrix (watcher-unit arms) ----------------------------------
#
# The arms below bind the boundary behaviors the CODE-phase verification
# suite left open. No dated ablation tables live in this file (census at
# TEST intake), so extending it trips no re-measure obligation.

def test_duplicate_watcher_re_arm_fast_exits_fired(tmp_path):
    """NAMED SCENARIO — duplicate-watcher re-arm: the tier-3 outlet and a
    lead who missed the first arm can arm a SECOND watcher whose
    deliverable already landed. The duplicate resolves on its FIRST
    evaluation, before any sleep, and must not burn its deadline (this is
    why re-arming needs no dedup marker)."""
    _write_task(tmp_path, "team-a", "7", _handoff_task("delivered earlier"))
    start = time.monotonic()
    rc = pact_wait.wait_for_deliverable(
        "team-a", "7", "handoff", deadline=60,
        tasks_base_dir=str(tmp_path), poll_interval=0.2,
    )
    assert rc == pact_wait.EXIT_FIRED
    assert time.monotonic() - start < 0.2


def test_second_consecutive_corrupt_read_is_terminal(tmp_path):
    """Corrupt-tolerance boundary: the window is ONE corrupt poll. A file
    still unparseable on the SECOND consecutive read is corrupt, not
    mid-rewrite — TERMINAL immediately, and a deliverable landing just
    past that second read does not resurrect the watch. The heal lands
    BETWEEN the second poll (t=0.1) and the third (t=0.2) deliberately: a
    loosened tolerance (terminal only on a third consecutive read) would
    flip this arm to FIRED instead of staying green."""
    team_dir = tmp_path / "team-a"
    team_dir.mkdir()
    task_file = team_dir / "7.json"
    task_file.write_text("{still corrupt", encoding="utf-8")
    timer = threading.Timer(
        0.15,
        lambda: task_file.write_text(
            json.dumps(_handoff_task("delivered")), encoding="utf-8"
        ),
    )
    timer.start()
    try:
        rc = pact_wait.wait_for_deliverable(
            "team-a", "7", "handoff", deadline=30,
            tasks_base_dir=str(tmp_path), poll_interval=0.1,
        )
    finally:
        timer.join()
    assert rc == pact_wait.EXIT_TERMINAL


def test_alternating_corrupt_reads_expire_never_turn_terminal(tmp_path):
    """The corrupt streak RESETS on every good read: corrupt, good, corrupt,
    good stays a live watch and ends at the deadline (EXPIRED), never
    TERMINAL — tolerance is per-CONSECUTIVE-run, not per-file history.
    Timing: one poll_interval of poll clock (0.15s) against ~0.12s corrupt
    windows, so each window covers at most one poll."""
    team_dir = tmp_path / "team-a"
    team_dir.mkdir()
    task_file = team_dir / "7.json"
    task_file.write_text(json.dumps({"metadata": {}}), encoding="utf-8")
    schedule = [
        (0.20, "{corrupt"),
        (0.32, json.dumps({"metadata": {}})),
        (0.50, "{corrupt again"),
        (0.62, json.dumps({"metadata": {}})),
    ]
    timers = [
        threading.Timer(at, lambda body=body: task_file.write_text(body, encoding="utf-8"))
        for at, body in schedule
    ]
    for timer in timers:
        timer.start()
    try:
        rc = pact_wait.wait_for_deliverable(
            "team-a", "7", "handoff", deadline=1.0,
            tasks_base_dir=str(tmp_path), poll_interval=0.15,
        )
    finally:
        for timer in timers:
            timer.join()
    assert rc == pact_wait.EXIT_EXPIRED


def test_terminal_outcomes_surface_well_before_the_deadline(tmp_path):
    """Terminal is IMMEDIATE (the docstring's contract): a watch that
    cannot do its job at all must not silently burn its deadline before
    saying so — the lead's re-arm decision depends on learning this fast."""
    start = time.monotonic()
    rc = pact_wait.wait_for_deliverable(
        "team-a", "7", "handoff", deadline=30,
        tasks_base_dir=str(tmp_path), poll_interval=0.05,
    )
    assert rc == pact_wait.EXIT_TERMINAL
    assert time.monotonic() - start < 5.0


def test_unexpected_runtime_error_never_masquerades_as_a_watch_outcome(
    tmp_path, monkeypatch
):
    """Exit 1 is RESERVED for crashes: an unexpected exception propagates
    OUT of wait_for_deliverable (to the traceback + exit 1 the uncaught
    __main__ path produces) and is never mapped onto EXPIRED (3) or
    TERMINAL (4). Exercised at the one seam that can raise; the mapping
    simply does not exist in the CLI, and this arm reds if anyone adds it."""
    def _boom(*_args, **_kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(pact_wait.wait_target, "evaluate_target", _boom)
    with pytest.raises(RuntimeError):
        pact_wait.wait_for_deliverable(
            "team-a", "7", "handoff", deadline=30,
            tasks_base_dir=str(tmp_path), poll_interval=0.05,
        )


def test_empty_task_id_is_terminal_not_usage_or_crash(tmp_path):
    """A malformed TARGET (empty task id, supplied but useless) is the
    terminal class per the batch-1 ruling: exit 4, loudly unwatchable —
    not a usage error (the argument WAS supplied) and never a crash."""
    rc = pact_wait.main(
        [
            "--team", "team-a", "--task", "",
            "--predicate", "handoff", "--deadline", "5",
            "--tasks-base-dir", str(tmp_path),
        ]
    )
    assert rc == pact_wait.EXIT_TERMINAL


def test_traversal_shaped_task_id_stays_contained(tmp_path):
    """Path machinery inside a task id is stripped BEFORE the join, so the
    lookup stays inside the containment-validated team dir: a traversal
    shape resolves to a file that does not exist there (MISSING), never a
    read outside the store."""
    outcome = wait_target.evaluate_target(
        "team-a", "../../../etc/passwd", "handoff", tasks_base_dir=str(tmp_path)
    )
    assert outcome == wait_target.EVAL_TARGET_MISSING
    # "../7" sanitizes to "7" — still joined INSIDE the team dir, never
    # resolved against the parent.
    outcome = wait_target.evaluate_target(
        "team-a", "../7", "handoff", tasks_base_dir=str(tmp_path)
    )
    assert outcome == wait_target.EVAL_TARGET_MISSING


def test_handoff_predicate_never_keys_on_a_recommended_only_field():
    """The registry's NEVER rule, bound for the shipped pair: a handoff
    carrying only the RECOMMENDED reasoning_chain is NOT a delivered
    summary. Keying a predicate on a recommended-only field would go dark
    on legitimate handoffs and silently burn the whole deadline."""
    assert not wait_target.handoff_present(
        {"metadata": {"handoff": {"reasoning_chain": "a long chain"}}}
    )
    # And the required field alone still fires regardless of what else is
    # present (keying is on `produced`, never on a required superset).
    assert wait_target.handoff_present(
        {"metadata": {"handoff": {"produced": "p", "reasoning_chain": "r"}}}
    )
