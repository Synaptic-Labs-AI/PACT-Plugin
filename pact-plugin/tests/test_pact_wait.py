"""
Tests for the structural wait mechanism: hooks/pact_wait.py (watcher CLI)
and hooks/shared/wait_target.py (deliverable predicate registry).

Verification-level coverage binding the exit contract (the numbers are the
contract — a crash must never masquerade as EXPIRED):
- predicates: handoff (non-empty `produced` STRING; list-typed produced reads
  absent — that shape is the live failure class this mechanism answers),
  teachback_submit (non-empty object); both read absent while a rejection
  record holds the unrevised original (revision_number comparison).
- evaluate_target outcomes: present / not_present / target_invalid (unsafe
  team name or task id) / target_missing / target_unreadable /
  target_corrupt / unknown_predicate.
- wait_for_deliverable: fast-exit on already-present, fires after delayed
  appearance, expires loud at the deadline, terminal on a target missing on
  two consecutive reads, terminal on persistently corrupt file, tolerant of
  one bad read, terminal on unknown predicate.
- main(): usage errors exit 2 (argparse), happy path returns 0.
- one subprocess smoke: the script runs standalone from an arbitrary cwd
  (its shared.* import resolves via the script directory).
- TEST-phase matrix (trailing section): duplicate-watcher re-arm (named
  scenario), corrupt-streak boundary (second consecutive read terminal;
  streak resets on a good read), terminal immediacy, exit-1 crash
  distinctness, empty/traversal task-id containment, and the anti-dark-key
  arm (a recommended-only field never satisfies the handoff predicate).
"""
import argparse
import json
import os
import subprocess
import sys
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
    _atomic_write(task_file, json.dumps(payload))
    return task_file


def _atomic_write(path: Path, text: str) -> None:
    """Write a task file the way a racing watcher must see it: whole or not
    at all. Path.write_text truncates before it writes, so a watcher polling
    inside that window reads an empty file as CORRUPT; a temp file renamed
    over the target never exposes a partial body."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


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


_PREDICATE_REJECTION = [
    ("handoff", "handoff", "handoff_rejection", {"produced": "p", "decisions": "d"}),
    ("teachback_submit", "teachback_submit", "teachback_rejection", {"understanding": "u"}),
]


@pytest.mark.parametrize("predicate,key,rejection_key,deliverable", _PREDICATE_REJECTION)
def test_rejected_deliverable_reads_absent_until_revised(
    predicate, key, rejection_key, deliverable
):
    """The rejected original stays on disk beside its rejection record, so
    presence alone would fire a post-rejection watch on the stale copy.
    The teammate's re-submission raises metadata.revision_number past the
    rejection's; only then does the deliverable count again."""
    check = wait_target.PREDICATES[predicate]
    rejected = {"metadata": {key: deliverable, rejection_key: {"revision_number": 1}}}
    assert not check(rejected)
    rejected["metadata"]["revision_number"] = 1
    assert not check(rejected)
    rejected["metadata"]["revision_number"] = 2
    assert check(rejected)
    # A second rejection of revision 2 holds the watch again until revision 3.
    rejected["metadata"][rejection_key] = {"revision_number": 2}
    assert not check(rejected)
    rejected["metadata"]["revision_number"] = 3
    assert check(rejected)


@pytest.mark.parametrize("predicate,key,rejection_key,deliverable", _PREDICATE_REJECTION)
@pytest.mark.parametrize("bad", [None, "2", True, 0, -1, 1.5])
def test_malformed_revision_numbers_count_as_the_first_submission(
    predicate, key, rejection_key, deliverable, bad
):
    """A revision number that is not a positive int counts as 1 on either
    side: a malformed rejection still holds a watch on the unrevised
    original, and a malformed teammate count never passes a rejection."""
    check = wait_target.PREDICATES[predicate]
    malformed_rejection = {
        "metadata": {key: deliverable, rejection_key: {"revision_number": bad}}
    }
    assert not check(malformed_rejection)
    malformed_rejection["metadata"]["revision_number"] = 2
    assert check(malformed_rejection)
    malformed_count = {
        "metadata": {
            key: deliverable,
            rejection_key: {"revision_number": 1},
            "revision_number": bad,
        }
    }
    assert not check(malformed_count)


@pytest.mark.parametrize("predicate,key,rejection_key,deliverable", _PREDICATE_REJECTION)
def test_only_its_own_rejection_kind_holds_a_predicate(
    predicate, key, rejection_key, deliverable
):
    """Each predicate reads only its own rejection record; a non-dict
    rejection value is no rejection."""
    check = wait_target.PREDICATES[predicate]
    other = "teachback_rejection" if rejection_key == "handoff_rejection" else "handoff_rejection"
    assert check({"metadata": {key: deliverable, other: {"revision_number": 1}}})
    assert check({"metadata": {key: deliverable, rejection_key: "rejected"}})


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
    # safe dir -> invalid (no file can ever exist there), never a crash.
    assert (
        wait_target.evaluate_target(
            "../escape", "7", "handoff", tasks_base_dir=str(tmp_path)
        )
        == wait_target.EVAL_TARGET_INVALID
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


def test_evaluate_non_utf8_file_is_corrupt_not_crash(tmp_path):
    """A task file holding bytes that are not valid UTF-8 is CORRUPT-class,
    never a crash: UnicodeDecodeError is a ValueError, and the module's
    never-raise contract routes every unusable payload to an explicit
    outcome (the shared reader's M2 hardening catches the same family)."""
    team_dir = tmp_path / "team-a"
    team_dir.mkdir()
    (team_dir / "7.json").write_bytes(b'{"metadata": {"handoff": "\xff\xfe"}}')
    assert (
        wait_target.evaluate_target(
            "team-a", "7", "handoff", tasks_base_dir=str(tmp_path)
        )
        == wait_target.EVAL_TARGET_CORRUPT
    )


# --- deadline argument totality --------------------------------------------

@pytest.mark.parametrize("text", ["nan", "inf", "-inf", "infinity", "NaN"])
def test_non_finite_deadlines_are_usage_errors(text):
    """A non-finite deadline can never expire: `nan` fails every
    remaining <= 0 comparison and `inf` never runs out, so either arms a
    watcher that can never emit EXPIRED — the wake contract's deadline leg
    dies. Both are caller bugs and must be usage errors (exit 2), the
    same class as a negative or zero deadline."""
    with pytest.raises(argparse.ArgumentTypeError):
        pact_wait._positive_float(text)


def test_finite_positive_deadlines_still_accepted():
    assert pact_wait._positive_float("1") == 1.0
    assert pact_wait._positive_float("0.5") == 0.5
    assert pact_wait._positive_float("1800") == 1800.0
    assert pact_wait._positive_float("86400") == pact_wait.MAX_DEADLINE_SECONDS


@pytest.mark.parametrize("text", ["86401", "86400.5", "1e9"])
def test_deadline_above_the_ceiling_is_a_usage_error(text, tmp_path):
    """The expiry leg must fire within a working session: a finite but
    enormous deadline is refused before any watching starts (exit 2)."""
    with pytest.raises(argparse.ArgumentTypeError):
        pact_wait._positive_float(text)
    with pytest.raises(SystemExit) as excinfo:
        pact_wait.main([
            "--team", "team-a", "--task", "7", "--predicate", "handoff",
            "--deadline", text, "--tasks-base-dir", str(tmp_path),
        ])
    assert excinfo.value.code == 2


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

def _record_sleeps(monkeypatch) -> list:
    """Replace the watcher's sleep with a recorder: a fast exit is one that
    never sleeps, which holds on any machine load (a wall-clock bound does
    not)."""
    calls: list = []
    monkeypatch.setattr(pact_wait.time, "sleep", calls.append)
    return calls


def test_fast_exit_when_already_present(tmp_path, monkeypatch):
    _write_task(tmp_path, "team-a", "7", _handoff_task("done"))
    sleeps = _record_sleeps(monkeypatch)
    rc = pact_wait.wait_for_deliverable(
        "team-a", "7", "handoff", deadline=30,
        tasks_base_dir=str(tmp_path), poll_interval=0.2,
    )
    # FIRED before the first sleep.
    assert rc == pact_wait.EXIT_FIRED
    assert sleeps == []


def test_fires_after_delayed_appearance(tmp_path, monkeypatch):
    """The deliverable lands after the first poll: the watch reads it on
    the next one and fires."""
    task_file = _write_task(tmp_path, "team-a", "7", {"metadata": {}})
    sleeps = _step_file_between_polls(
        monkeypatch, task_file, [json.dumps(_handoff_task("late but delivered"))]
    )
    rc = pact_wait.wait_for_deliverable(
        "team-a", "7", "handoff", deadline=30,
        tasks_base_dir=str(tmp_path), poll_interval=0.05,
    )
    assert rc == pact_wait.EXIT_FIRED
    assert len(sleeps) == 1


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
    # The remedy is to re-arm or escalate, never to poll the file by hand.
    assert "re-arm the watcher" in captured.err
    assert "escalate" in captured.err
    assert "manually" not in captured.err


def test_terminal_on_missing_target(tmp_path):
    rc = pact_wait.wait_for_deliverable(
        "team-a", "7", "handoff", deadline=30,
        tasks_base_dir=str(tmp_path), poll_interval=0.05,
    )
    assert rc == pact_wait.EXIT_TERMINAL


def test_terminal_on_persistently_corrupt_file(tmp_path, capsys):
    """A real file that never stops being unparseable ends the watch
    TERMINAL on its second read. Nothing writes the file during the watch,
    so the outcome does not depend on timing."""
    team_dir = tmp_path / "team-a"
    team_dir.mkdir()
    (team_dir / "7.json").write_text("{still not json", encoding="utf-8")
    rc = pact_wait.wait_for_deliverable(
        "team-a", "7", "handoff", deadline=30,
        tasks_base_dir=str(tmp_path), poll_interval=0.05,
    )
    assert rc == pact_wait.EXIT_TERMINAL
    # The TERMINAL stderr carries the exit-class name and the same identity
    # triple as the other exits — predicate included — so the notice answers
    # "what woke me" without re-deriving it.
    captured = capsys.readouterr()
    assert "pact-wait TERMINAL" in captured.err
    assert "predicate 'handoff'" in captured.err
    assert "task 7" in captured.err and "team-a" in captured.err


def _step_file_between_polls(monkeypatch, task_file, bodies):
    """Replace the watcher's sleep with a step that writes the next body to
    the real task file, so each poll reads exactly one scripted state.
    Deterministic on any machine load: the file changes only between polls,
    never during one. A None body leaves the file unchanged for that poll.
    Returns the list of sleeps taken."""
    queue = list(bodies)
    sleeps = []

    def _step(seconds):
        sleeps.append(seconds)
        if queue:
            body = queue.pop(0)
            if body is not None:
                task_file.write_text(body, encoding="utf-8")

    monkeypatch.setattr(pact_wait.time, "sleep", _step)
    return sleeps


def test_tolerates_transient_corrupt_read(tmp_path, monkeypatch):
    """A rewrite caught mid-flight must not kill the watch: the real file
    is corrupt on the first poll and holds the deliverable on the second,
    so the watch fires after exactly one sleep."""
    team_dir = tmp_path / "team-a"
    team_dir.mkdir()
    task_file = team_dir / "7.json"
    task_file.write_text("{mid-rewrite", encoding="utf-8")
    sleeps = _step_file_between_polls(
        monkeypatch, task_file, [json.dumps(_handoff_task("arrived"))]
    )
    rc = pact_wait.wait_for_deliverable(
        "team-a", "7", "handoff", deadline=30,
        tasks_base_dir=str(tmp_path), poll_interval=0.1,
    )
    assert rc == pact_wait.EXIT_FIRED
    assert len(sleeps) == 1


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

def test_duplicate_watcher_re_arm_fast_exits_fired(tmp_path, monkeypatch):
    """NAMED SCENARIO — duplicate-watcher re-arm: the tier-3 outlet and a
    lead who missed the first arm can arm a SECOND watcher whose
    deliverable already landed. The duplicate resolves on its FIRST
    evaluation, before any sleep, and must not burn its deadline (this is
    why re-arming needs no dedup marker)."""
    _write_task(tmp_path, "team-a", "7", _handoff_task("delivered earlier"))
    sleeps = _record_sleeps(monkeypatch)
    rc = pact_wait.wait_for_deliverable(
        "team-a", "7", "handoff", deadline=60,
        tasks_base_dir=str(tmp_path), poll_interval=0.2,
    )
    assert rc == pact_wait.EXIT_FIRED
    assert sleeps == []


def test_second_consecutive_corrupt_read_is_terminal(tmp_path, monkeypatch):
    """Corrupt-tolerance boundary: the window is ONE corrupt poll. A real
    file still unparseable on the SECOND consecutive read is corrupt, not
    mid-rewrite: TERMINAL then, and the deliverable written right after
    that read does not resurrect the watch. A loosened tolerance (terminal
    only on a third consecutive read) would reach the deliverable and flip
    this arm to FIRED."""
    team_dir = tmp_path / "team-a"
    team_dir.mkdir()
    task_file = team_dir / "7.json"
    task_file.write_text("{still corrupt", encoding="utf-8")
    sleeps = _step_file_between_polls(
        monkeypatch, task_file, [None, json.dumps(_handoff_task("delivered"))]
    )
    rc = pact_wait.wait_for_deliverable(
        "team-a", "7", "handoff", deadline=30,
        tasks_base_dir=str(tmp_path), poll_interval=0.1,
    )
    assert rc == pact_wait.EXIT_TERMINAL
    assert len(sleeps) == 1


def test_alternating_corrupt_reads_never_turn_terminal(tmp_path, monkeypatch):
    """The bad-read streak RESETS on every good read: a real file read as
    good, corrupt, good, corrupt, good, then delivered stays a live watch
    and fires, never TERMINAL. Tolerance is per CONSECUTIVE run, not per
    file history: without the reset the second corrupt read would end the
    watch."""
    team_dir = tmp_path / "team-a"
    team_dir.mkdir()
    task_file = team_dir / "7.json"
    task_file.write_text(json.dumps({"metadata": {}}), encoding="utf-8")
    sleeps = _step_file_between_polls(
        monkeypatch,
        task_file,
        [
            "{corrupt",
            json.dumps({"metadata": {}}),
            "{corrupt again",
            json.dumps({"metadata": {}}),
            json.dumps(_handoff_task("delivered")),
        ],
    )
    rc = pact_wait.wait_for_deliverable(
        "team-a", "7", "handoff", deadline=30,
        tasks_base_dir=str(tmp_path), poll_interval=0.15,
    )
    assert rc == pact_wait.EXIT_FIRED
    assert len(sleeps) == 5


def test_terminal_outcomes_surface_well_before_the_deadline(tmp_path):
    """Terminal surfaces within MAX_CONSECUTIVE_BAD_READS polls (the
    docstring's contract), never at the deadline: a watch that cannot do
    its job at all must not silently burn its deadline before saying so —
    the lead's re-arm decision depends on learning this fast."""
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
    lookup stays inside the containment-validated team dir. STRENGTHENED
    (cold round 3): with team-a/7.json seeded, "../7" sanitizes to "7" and
    the contained join FINDS it (PRESENT) — an escaped join would read the
    bare canary beside the team dir instead, whose payload is built to FAIL
    the predicate, so a sanitizer-removal mutation flips this arm red. A
    fully traversal-shaped id with its own reachable satisfying canary
    reads MISSING with the sanitizer intact."""
    team_dir = tmp_path / "team-a"
    team_dir.mkdir()
    (team_dir / "7.json").write_text(
        json.dumps({"metadata": {"handoff": {"produced": "contained",
                                             "decisions": "d"}}}),
        encoding="utf-8",
    )
    # The escaped join's target: tasks/7.json, OUTSIDE the team dir. Its
    # handoff is deliberately predicate-FAILING (a list-typed handoff), so a
    # mutation that drops the sanitizer reads THIS file and flips the arm
    # to NOT_PRESENT.
    (tmp_path / "7.json").write_text(
        json.dumps({"metadata": {"handoff": ["escaped-canary"]}}),
        encoding="utf-8",
    )
    outcome = wait_target.evaluate_target(
        "team-a", "../7", "handoff", tasks_base_dir=str(tmp_path)
    )
    assert outcome == wait_target.EVAL_PRESENT
    # Fully traversal-shaped id: "../outside" sanitizes to "outside" —
    # joined INSIDE the team dir, where nothing exists (MISSING). The
    # mutation's target — tasks/outside.json, seeded predicate-SATISFYING —
    # sits beside the team dir and must never be read.
    (tmp_path / "outside.json").write_text(
        json.dumps({"metadata": {"handoff": {"produced": "escaped-canary",
                                             "decisions": "d"}}}),
        encoding="utf-8",
    )
    outcome = wait_target.evaluate_target(
        "team-a", "../outside", "handoff", tasks_base_dir=str(tmp_path)
    )
    assert outcome == wait_target.EVAL_TARGET_MISSING
    # The deep-hostile shape stays MISSING: it sanitizes to etcpasswd —
    # still inside the team dir, still absent.
    outcome = wait_target.evaluate_target(
        "team-a", "../../../etc/passwd", "handoff", tasks_base_dir=str(tmp_path)
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


# --- bad-read grace: one absent/unreadable/corrupt read is tolerated ---------

def _scripted_outcomes(monkeypatch, outcomes):
    """Replace evaluate_target with a fixed outcome sequence; returns the
    list of calls made, so the arms can count reads without timing."""
    calls = []
    queue = list(outcomes)

    def _next(*_args, **_kwargs):
        calls.append(1)
        return queue.pop(0)

    monkeypatch.setattr(pact_wait.wait_target, "evaluate_target", _next)
    return calls


@pytest.mark.parametrize(
    "bad",
    [
        wait_target.EVAL_TARGET_MISSING,
        wait_target.EVAL_TARGET_UNREADABLE,
        wait_target.EVAL_TARGET_CORRUPT,
    ],
)
def test_one_bad_read_is_tolerated_then_fires(monkeypatch, bad):
    calls = _scripted_outcomes(monkeypatch, [bad, wait_target.EVAL_PRESENT])
    rc = pact_wait.wait_for_deliverable(
        "team-a", "7", "handoff", deadline=5, poll_interval=0.01,
    )
    assert rc == pact_wait.EXIT_FIRED
    assert len(calls) == 2


@pytest.mark.parametrize(
    "first,second",
    [
        (wait_target.EVAL_TARGET_MISSING, wait_target.EVAL_TARGET_MISSING),
        (wait_target.EVAL_TARGET_UNREADABLE, wait_target.EVAL_TARGET_UNREADABLE),
        (wait_target.EVAL_TARGET_MISSING, wait_target.EVAL_TARGET_CORRUPT),
    ],
)
def test_two_consecutive_bad_reads_are_terminal(monkeypatch, capsys, first, second):
    calls = _scripted_outcomes(monkeypatch, [first, second])
    rc = pact_wait.wait_for_deliverable(
        "team-a", "7", "handoff", deadline=5, poll_interval=0.01,
    )
    assert rc == pact_wait.EXIT_TERMINAL
    assert len(calls) == 2
    assert "2 consecutive reads" in capsys.readouterr().err


def test_a_good_read_resets_the_bad_read_streak(monkeypatch):
    _scripted_outcomes(
        monkeypatch,
        [
            wait_target.EVAL_TARGET_MISSING,
            wait_target.EVAL_NOT_PRESENT,
            wait_target.EVAL_TARGET_UNREADABLE,
            wait_target.EVAL_PRESENT,
        ],
    )
    rc = pact_wait.wait_for_deliverable(
        "team-a", "7", "handoff", deadline=5, poll_interval=0.01,
    )
    assert rc == pact_wait.EXIT_FIRED


@pytest.mark.parametrize(
    "fixed",
    [wait_target.EVAL_TARGET_INVALID, wait_target.EVAL_UNKNOWN_PREDICATE],
)
def test_fixed_failures_are_terminal_on_the_first_read(monkeypatch, fixed):
    """An unusable target or unknown predicate cannot heal between polls,
    so it gets no grace read."""
    calls = _scripted_outcomes(monkeypatch, [fixed])
    rc = pact_wait.wait_for_deliverable(
        "team-a", "7", "handoff", deadline=5, poll_interval=0.01,
    )
    assert rc == pact_wait.EXIT_TERMINAL
    assert len(calls) == 1


def test_file_absent_on_first_read_then_delivered_fires(tmp_path, monkeypatch):
    """Real-file form of the grace: the task file is absent on the first
    poll and present on the second (a rewrite caught mid-flight)."""
    (tmp_path / "team-a").mkdir()
    task_file = tmp_path / "team-a" / "7.json"
    sleeps = _step_file_between_polls(
        monkeypatch, task_file, [json.dumps(_handoff_task("landed"))]
    )
    rc = pact_wait.wait_for_deliverable(
        "team-a", "7", "handoff", deadline=30,
        tasks_base_dir=str(tmp_path), poll_interval=0.3,
    )
    assert task_file.exists()
    assert rc == pact_wait.EXIT_FIRED
    assert len(sleeps) == 1


def test_deeply_nested_json_is_corrupt_not_a_crash(tmp_path):
    """json.loads raises RecursionError (not JSONDecodeError) on nesting
    deep enough to exhaust the parser's stack; evaluate_target's
    never-raise contract maps it to CORRUPT."""
    team_dir = tmp_path / "team-a"
    team_dir.mkdir()
    (team_dir / "7.json").write_text("[" * 200000, encoding="utf-8")
    assert (
        wait_target.evaluate_target(
            "team-a", "7", "handoff", tasks_base_dir=str(tmp_path)
        )
        == wait_target.EVAL_TARGET_CORRUPT
    )


# --- the production seam: default tasks base, no --tasks-base-dir ----------
#
# Every arm above passes --tasks-base-dir, but the arm commands the gates emit
# never do: in production the watcher resolves its tasks base from
# CLAUDE_CONFIG_DIR, falling back to HOME/.claude. These arms run the real
# script as a child process over that unstubbed resolution.

def _run_default_base(env_overrides: dict, drop: tuple = ()) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.update(env_overrides)
    cmd = [
        sys.executable, str(HOOKS_DIR / "pact_wait.py"),
        "--team", "team-a", "--task", "7",
        "--predicate", "handoff", "--deadline", "5",
    ]
    return subprocess.run(
        cmd, capture_output=True, text=True, cwd="/", env=env, timeout=30
    )


def test_default_base_honours_claude_config_dir(tmp_path):
    """With CLAUDE_CONFIG_DIR set, the watcher reads
    $CLAUDE_CONFIG_DIR/tasks and fires. HOME points at an empty directory, so
    a watcher that ignored the variable would find no task file and exit 4."""
    config = tmp_path / "config"
    home = tmp_path / "home"
    home.mkdir()
    _write_task(config / "tasks", "team-a", "7", _handoff_task("delivered"))
    proc = _run_default_base(
        {"CLAUDE_CONFIG_DIR": str(config), "HOME": str(home)}
    )
    assert proc.returncode == pact_wait.EXIT_FIRED, proc.stderr
    assert "pact-wait FIRED" in proc.stdout


def test_default_base_falls_back_to_home_claude(tmp_path):
    """With CLAUDE_CONFIG_DIR absent from the environment, the watcher reads
    $HOME/.claude/tasks and fires."""
    home = tmp_path / "home"
    _write_task(home / ".claude" / "tasks", "team-a", "7", _handoff_task("delivered"))
    proc = _run_default_base({"HOME": str(home)}, drop=("CLAUDE_CONFIG_DIR",))
    assert proc.returncode == pact_wait.EXIT_FIRED, proc.stderr
    assert "pact-wait FIRED" in proc.stdout



# --- never-raise census: every exception the resolve/read/parse path raises -

def test_oversized_integer_literal_is_corrupt_not_a_crash(tmp_path):
    """json.loads raises ValueError (not JSONDecodeError) for an integer
    literal past the int-string digit limit; it maps to CORRUPT."""
    team_dir = tmp_path / "team-a"
    team_dir.mkdir()
    (team_dir / "7.json").write_text('{"metadata": {"x": ' + "9" * 5000 + "}}",
                                     encoding="utf-8")
    assert (
        wait_target.evaluate_target("team-a", "7", "handoff", tasks_base_dir=str(tmp_path))
        == wait_target.EVAL_TARGET_CORRUPT
    )


def test_oversized_integer_task_file_exits_terminal_not_crash(tmp_path):
    """End to end: the watcher CLI exits 4 (TERMINAL after two bad reads),
    never 1 with a traceback."""
    team_dir = tmp_path / "team-a"
    team_dir.mkdir()
    (team_dir / "7.json").write_text('{"x": ' + "9" * 5000 + "}", encoding="utf-8")
    rc = pact_wait.wait_for_deliverable(
        "team-a", "7", "handoff", deadline=5,
        tasks_base_dir=str(tmp_path), poll_interval=0.01,
    )
    assert rc == pact_wait.EXIT_TERMINAL


@pytest.mark.parametrize(
    "stage,exc,outcome",
    [
        ("exists", PermissionError(13, "denied"), "target_unreadable"),
        ("exists", OSError(5, "io"), "target_unreadable"),
        ("open", OSError(5, "io"), "target_unreadable"),
        ("read", OSError(5, "io"), "target_unreadable"),
        ("read", UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"), "target_corrupt"),
        ("loads", ValueError("Exceeds the limit"), "target_corrupt"),
        ("loads", json.JSONDecodeError("bad", "x", 0), "target_corrupt"),
        ("loads", RecursionError("deep"), "target_corrupt"),
        ("base", RuntimeError("no home"), "target_invalid"),
    ],
)
def test_evaluate_target_never_raises_for_any_census_exception(
    tmp_path, monkeypatch, stage, exc, outcome
):
    """Census of the read path: each exception the resolve/read/parse steps
    can raise maps to an outcome constant; none escapes the never-raise
    contract."""
    _write_task(tmp_path, "team-a", "7", _handoff_task("done"))

    def boom(*_a, **_k):
        raise exc

    base = str(tmp_path)
    if stage == "exists":
        # Raise for the watched file only: a class-wide patch outlives the test
        # body, and a conftest teardown that calls Path.exists then errors.
        target = tmp_path / "team-a" / "7.json"
        real_exists = Path.exists

        def exists(self, *a, **k):
            if self == target:
                raise exc
            return real_exists(self, *a, **k)

        monkeypatch.setattr(Path, "exists", exists)
    elif stage == "open":
        monkeypatch.setattr(wait_target.os, "open", boom)
    elif stage == "read":
        monkeypatch.setattr(wait_target, "_read_regular_file", boom)
    elif stage == "loads":
        monkeypatch.setattr(wait_target.json, "loads", boom)
    else:
        monkeypatch.setattr(wait_target, "get_claude_config_dir", boom)
        base = None
    assert wait_target.evaluate_target(
        "team-a", "7", "handoff", tasks_base_dir=base
    ) == outcome



# --- only regular files are read: a FIFO or a device cannot hold the watch --

def _special_target(tmp_path, kind):
    import os

    team_dir = tmp_path / "team-a"
    team_dir.mkdir()
    target = team_dir / "7.json"
    if kind == "fifo":
        os.mkfifo(target)
    elif kind == "dev_zero":
        target.symlink_to("/dev/zero")
    else:
        target.mkdir()
    return target


@pytest.mark.parametrize("kind", ["fifo", "dev_zero", "directory"])
def test_non_regular_task_file_is_unreadable_not_read(tmp_path, kind):
    """A FIFO blocks a reader until a writer appears and /dev/zero never
    ends; neither is opened for reading. Each reads as UNREADABLE."""
    _special_target(tmp_path, kind)
    assert (
        wait_target.evaluate_target("team-a", "7", "handoff", tasks_base_dir=str(tmp_path))
        == wait_target.EVAL_TARGET_UNREADABLE
    )


@pytest.mark.parametrize("kind", ["fifo", "dev_zero"])
def test_non_regular_task_file_ends_the_watch_within_its_deadline(tmp_path, kind):
    """Through the real CLI: the watch ends TERMINAL (one grace read, then
    exit 4) inside its 1 s deadline instead of blocking on the read."""
    _special_target(tmp_path, kind)
    proc = subprocess.run(
        [sys.executable, str(HOOKS_DIR / "pact_wait.py"), "--team", "team-a",
         "--task", "7", "--predicate", "handoff", "--deadline", "1",
         "--tasks-base-dir", str(tmp_path)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == pact_wait.EXIT_TERMINAL, proc.stderr
    assert "target_unreadable" in proc.stderr



# --- a symlink loop under the tasks base never escapes as a crash ----------

def _looped_team(tmp_path):
    """tasks/team-a -> tasks/loop-b -> tasks/team-a: Path.resolve raises
    RuntimeError on this loop on Python 3.12."""
    (tmp_path / "loop-b").symlink_to(tmp_path / "team-a")
    (tmp_path / "team-a").symlink_to(tmp_path / "loop-b")


def test_symlink_looped_team_dir_is_invalid_not_a_crash(tmp_path):
    from shared.task_utils import _team_dir_under, task_dir_for

    _looped_team(tmp_path)
    assert _team_dir_under(tmp_path, "team-a") is None
    assert task_dir_for("team-a", tasks_base_dir=str(tmp_path)) is None
    assert (
        wait_target.evaluate_target("team-a", "7", "handoff", tasks_base_dir=str(tmp_path))
        == wait_target.EVAL_TARGET_INVALID
    )


def test_symlink_looped_team_dir_ends_the_watch_terminal(tmp_path):
    """Through the real CLI: exit 4 on the first read (a loop is a fixed
    property of the store), never exit 1 with a traceback."""
    _looped_team(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(HOOKS_DIR / "pact_wait.py"), "--team", "team-a",
         "--task", "7", "--predicate", "handoff", "--deadline", "5",
         "--tasks-base-dir", str(tmp_path)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == pact_wait.EXIT_TERMINAL, proc.stderr
    assert "target_invalid" in proc.stderr
